"""
Layer 1: Ultra-Low-Latency Perception Layer (CPU Only / VRAM 0GB).

Implements:
- Mode A (Piano <-> Piano):
  - EchoSuppressionLedger: Tracks AI-sent (pitch, sent_ts) in physical lag window
    [sent_ts + min_lag, sent_ts + max_lag] and drops 100% of echoed self-strikes.
  - TurnTakingDetector: Detects CC#67 (una corda) explicit pass + automatic phrase-end
    breathing gauge from IOI ritardando, damper release (CC#64), silence, and cadence.
- Mode B (Cello <-> Disklavier):
  - NLMSEchoCanceller: 2-channel Normalized Least Mean Squares adaptive filter + transmit gate.
  - CelloAcousticPerception: Real-time F0 pitch tracking, vibrato depth/rate, double-stop
    detection, onset detection, and Attack-Slope/RMS articulation ("staccato" vs "legato").
- VerbalDirectiveRecognizer: Zero-VRAM CPU directive parser (optional faster-whisper /
  sherpa-onnx INT8 CPU hook + instant Japanese MC/HUD command mapper).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import threading
import time
from typing import Any, Deque, Dict, List, Optional, Tuple

import numpy as np


@dataclass
class PerceptionSnapshot:
    mode: str  # "piano" (Mode A) or "cello" (Mode B)
    timestamp: float
    f0_hz: float = 0.0
    midi_pitch: int = 0
    note_name: str = "--"
    vibrato_cents: float = 0.0
    vibrato_rate_hz: float = 0.0
    articulation: str = "legato"  # "staccato" | "legato" | "sostenuto" | "rest"
    attack_slope: float = 0.0
    rms_level: float = 0.0
    rms_dbfs: float = -70.0
    double_stop_pitches: List[int] = field(default_factory=list)
    breath_gauge: float = 0.0
    turn_ready: bool = False
    turn_reason: str = ""
    pedal_cc64: int = 0
    pedal_cc67: int = 0
    echo_suppressed_count: int = 0
    human_notes_count: int = 0
    nlms_erle_db: float = 0.0
    recent_human_notes: List[Dict[str, Any]] = field(default_factory=list)
    pitch_history: List[float] = field(default_factory=list)
    verbal_directive: str = ""


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_to_note_name(midi_note: int) -> str:
    if midi_note <= 0 or midi_note > 127:
        return "--"
    return f"{NOTE_NAMES[midi_note % 12]}{(midi_note // 12) - 1}"


def freq_to_midi_float(freq_hz: float) -> float:
    if freq_hz <= 20.0:
        return 0.0
    return 69.0 + 12.0 * math.log2(freq_hz / 440.0)


class EchoSuppressionLedger:
    """
    Mode A Self-Strike Echo Cancellation Ledger.
    Maintains a time-windowed ledger of AI-sent notes (pitch, sent_ts).
    Any MIDI IN note arriving within [sent_ts + min_lag_s, sent_ts + max_lag_s]
    is identified as Disklavier acoustic/MIDI loopback and discarded 100%.
    """

    def __init__(self, min_lag_s: float = 0.0, max_lag_s: float = 0.280) -> None:
        self.min_lag_s = min_lag_s
        self.max_lag_s = max_lag_s
        self._lock = threading.Lock()
        self._ledger: Dict[int, Deque[float]] = {}
        self.suppressed_count: int = 0
        self.human_accepted_count: int = 0

    def register_sent_note(self, pitch: int, sent_ts: Optional[float] = None) -> None:
        ts = sent_ts if sent_ts is not None else time.perf_counter()
        p = int(pitch)
        with self._lock:
            q = self._ledger.setdefault(p, deque(maxlen=64))
            q.append(ts)
            self._prune_locked(ts)

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self.max_lag_s - 0.5
        for q in self._ledger.values():
            while q and q[0] < cutoff:
                q.popleft()

    def is_self_echo(self, pitch: int, recv_ts: Optional[float] = None, consume: bool = True) -> bool:
        ts = recv_ts if recv_ts is not None else time.perf_counter()
        p = int(pitch)
        with self._lock:
            self._prune_locked(ts)
            q = self._ledger.get(p)
            if not q:
                self.human_accepted_count += 1
                return False
            for idx, sent_ts in enumerate(q):
                lo = sent_ts + self.min_lag_s - 0.002
                hi = sent_ts + self.max_lag_s
                if lo <= ts <= hi:
                    if consume:
                        # Remove matched ledger entry so simultaneous/sequential human notes on same pitch pass cleanly
                        del q[idx]
                    self.suppressed_count += 1
                    return True
            self.human_accepted_count += 1
            return False

    def filter_incoming_note(
        self, pitch: int, velocity: int, recv_ts: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        ts = recv_ts if recv_ts is not None else time.perf_counter()
        if velocity <= 0:
            return None
        if self.is_self_echo(pitch, recv_ts=ts, consume=True):
            return None
        return {
            "pitch": int(pitch),
            "velocity": int(velocity),
            "onset_s": ts,
            "source": "human_piano",
        }


class TurnTakingDetector:
    """
    Detects when the human performer has finished a phrase and hands over the turn:
    1. Explicit Una Corda pedal pass cue (CC#67 >= 64)
    2. Automatic phrase breathing detection from:
       - IOI ritardando (deceleration of recent inter-onset intervals)
       - Damper pedal release (CC#64 dropping below 28)
       - Silence window after recent notes
       - Harmonic cadence resolution (stepwise/tonic/fifth closure)
    """

    def __init__(self, silence_threshold_s: float = 0.75) -> None:
        self.silence_threshold_s = silence_threshold_s
        self.note_times: Deque[float] = deque(maxlen=24)
        self.note_pitches: Deque[int] = deque(maxlen=24)
        self.pedal_cc64: int = 0
        self.pedal_cc67: int = 0
        self.last_note_ts: float = 0.0
        self.prev_pedal_cc64: int = 0
        self.last_turn_emit_ts: float = 0.0

    def push_note(self, pitch: int, ts: float) -> None:
        self.note_times.append(ts)
        self.note_pitches.append(int(pitch))
        self.last_note_ts = ts

    def push_cc(self, control: int, value: int, ts: float) -> None:
        if control == 64:
            self.prev_pedal_cc64 = self.pedal_cc64
            self.pedal_cc64 = int(value)
        elif control == 67:
            self.pedal_cc67 = int(value)

    def evaluate(self, now: Optional[float] = None) -> Tuple[float, bool, str]:
        ts = now if now is not None else time.perf_counter()
        if self.pedal_cc67 >= 64 and (ts - self.last_turn_emit_ts) >= 0.4:
            self.last_turn_emit_ts = ts
            return 1.0, True, "una_corda_cue"

        if not self.note_times:
            return 0.0, False, "waiting_input"

        silence_s = max(0.0, ts - self.last_note_ts)
        silence_score = min(1.0, silence_s / self.silence_threshold_s)

        # IOI Ritardando check
        rit_score = 0.0
        if len(self.note_times) >= 4:
            iois = [
                self.note_times[i] - self.note_times[i - 1]
                for i in range(1, len(self.note_times))
                if 0.04 <= (self.note_times[i] - self.note_times[i - 1]) <= 2.5
            ]
            if len(iois) >= 3:
                early = sum(iois[:-2]) / max(1, len(iois[:-2]))
                recent = sum(iois[-2:]) / 2.0
                if early > 0.0 and recent > early * 1.18:
                    rit_score = min(1.0, (recent / early - 1.0) * 1.5)

        # Damper release score
        damper_score = 1.0 if (self.prev_pedal_cc64 >= 40 and self.pedal_cc64 < 28) else (
            0.4 if self.pedal_cc64 < 25 else 0.0
        )

        # Harmonic cadence closure feel (stepwise resolution <= 2 semitones or P4/P5)
        cadence_score = 0.0
        if len(self.note_pitches) >= 2:
            interval = abs(self.note_pitches[-1] - self.note_pitches[-2]) % 12
            if interval in (1, 2, 5, 7, 0):
                cadence_score = 0.7

        gauge = min(
            1.0,
            0.45 * silence_score + 0.25 * rit_score + 0.15 * damper_score + 0.15 * cadence_score,
        )
        ready = (
            gauge >= 0.68
            and silence_s >= 0.35
            and len(self.note_times) >= 2
            and (ts - self.last_turn_emit_ts) >= 0.8
        )
        reason = "breath_cadence_detected" if ready else "listening"
        if ready:
            self.last_turn_emit_ts = ts
        return round(gauge, 3), ready, reason


class NLMSEchoCanceller:
    """
    2-Channel Block-Normalized Least Mean Squares (NLMS) adaptive echo canceller
    plus physical transmit gate for Mode B (Cello mic Ch1 vs Piano reference Ch2).
    """

    def __init__(self, filter_len: int = 16, mu: float = 0.98, eps: float = 1e-6) -> None:
        self.filter_len = filter_len
        self.mu = mu
        self.eps = eps
        self.weights = np.zeros(filter_len, dtype=np.float64)
        self.ref_history = np.zeros(max(0, filter_len - 1), dtype=np.float64)
        self.last_erle_db: float = 0.0

    def process_block(
        self,
        mic_signal: np.ndarray,
        ref_signal: Optional[np.ndarray] = None,
        piano_active_energy: float = 0.0,
    ) -> np.ndarray:
        mic = np.asarray(mic_signal, dtype=np.float64)
        if ref_signal is None or len(ref_signal) != len(mic) or len(mic) == 0:
            return mic.astype(np.float32)

        ref = np.asarray(ref_signal, dtype=np.float64)
        ref_rms = float(np.sqrt(np.mean(ref * ref)))
        if ref_rms < 1e-7:
            self.last_erle_db = 0.0
            return mic.astype(np.float32)

        L = self.filter_len
        n = len(mic)
        padded = np.concatenate([self.ref_history, ref])
        # Construct causal FIR delay matrix X of shape (n, L)
        X = np.column_stack([padded[L - 1 - k : L - 1 - k + n] for k in range(L)])
        self.ref_history = padded[-(L - 1) :].copy() if L > 1 else np.zeros(0, dtype=np.float64)

        err_prev = mic - (X @ self.weights)
        gram = (X.T @ X) + (self.eps * n) * np.eye(L, dtype=np.float64)
        try:
            delta_w = np.linalg.solve(gram, X.T @ err_prev)
            self.weights = self.weights + self.mu * delta_w
        except np.linalg.LinAlgError:
            pass

        out = mic - (X @ self.weights)

        # Apply subtle physical transmit attenuation only when extreme piano transient exceeds cello residual
        if piano_active_energy > 0.85:
            out *= 0.72

        in_pow = float(np.mean(mic * mic)) + 1e-12
        out_pow = float(np.mean(out * out)) + 1e-12
        self.last_erle_db = round(max(0.0, 10.0 * math.log10(in_pow / out_pow)), 2)
        return out.astype(np.float32)

    @staticmethod
    def measure_bleed_erle_db(
        pure_bleed_mic: np.ndarray,
        ref_signal: np.ndarray,
    ) -> float:
        """
        Measures pure adaptive echo cancellation ERLE (dB) on a reference-only probe
        block without altering live cello perception state.
        """
        probe = NLMSEchoCanceller()
        residual = probe.process_block(pure_bleed_mic, ref_signal)
        n = len(pure_bleed_mic)
        tail = n // 2 if n > 16 else 0
        in_p = float(np.mean(np.asarray(pure_bleed_mic[tail:], dtype=np.float64) ** 2)) + 1e-12
        out_p = float(np.mean(np.asarray(residual[tail:], dtype=np.float64) ** 2)) + 1e-12
        return round(max(0.0, 10.0 * math.log10(in_p / out_p)), 2)


class CelloAcousticPerception:
    """
    Mode B Cello Audio Feature Extractor (CPU only).
    Extracts F0 pitch trajectory, vibrato depth/rate, double-stops, onsets,
    and articulation ("staccato" vs "legato") from Attack Slope & RMS envelope.
    """

    def __init__(self, sample_rate: int = 44100) -> None:
        self.sample_rate = sample_rate
        self.pitch_history_hz: Deque[float] = deque(maxlen=64)
        self.rms_history: Deque[float] = deque(maxlen=32)
        self.prev_rms: float = 0.0
        self.prev_attack_slope: float = 0.0
        self.active_onset_ts: float = 0.0
        self.last_note_dur_s: float = 0.0
        self._in_note: bool = False

    @staticmethod
    def estimate_f0_hz(frame: np.ndarray, sample_rate: int = 44100) -> float:
        x = np.asarray(frame, dtype=np.float64)
        if len(x) < 256:
            return 0.0
        rms = float(np.sqrt(np.mean(x * x)))
        if rms < 0.004:
            return 0.0

        # Zero-padded Hanning RFFT + Harmonic Comb Summation (Cello range 60..1200 Hz)
        x = x - np.mean(x)
        n_fft = max(16384, len(x) * 4)
        xw = x * np.hanning(len(x))
        spec = np.abs(np.fft.rfft(xw, n=n_fft))
        freqs = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
        df = sample_rate / n_fft

        mask = (freqs >= 60.0) & (freqs <= 1200.0)
        if not np.any(mask):
            return 0.0
        f_sub = freqs[mask]
        s_sub = spec[mask]
        max_s = float(np.max(s_sub))
        if max_s <= 1e-9:
            return 0.0

        # Only consider candidates with significant fundamental energy (>= 22% of spectral peak)
        scores = np.zeros_like(s_sub)
        for idx in np.where(s_sub >= 0.22 * max_s)[0]:
            f0 = float(f_sub[idx])
            i2 = int(round((2.0 * f0) / df))
            i3 = int(round((3.0 * f0) / df))
            h2 = float(spec[i2]) if i2 < len(spec) else 0.0
            h3 = float(spec[i3]) if i3 < len(spec) else 0.0
            scores[idx] = float(s_sub[idx]) + 0.55 * h2 + 0.30 * h3

        best_idx = int(np.argmax(scores))
        if scores[best_idx] <= 0.0:
            return float(f_sub[int(np.argmax(s_sub))])
        return float(f_sub[best_idx])

    @staticmethod
    def detect_double_stop(frame: np.ndarray, primary_f0: float, sample_rate: int = 44100) -> List[int]:
        if primary_f0 <= 20.0:
            return []
        p1 = int(round(freq_to_midi_float(primary_f0)))
        n = max(4096, len(frame) * 2)
        xw = np.asarray(frame, dtype=np.float64) * np.hanning(len(frame))
        spec = np.abs(np.fft.rfft(xw, n=n))
        freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
        spec_max = float(np.max(spec))
        if spec_max <= 1e-9:
            return [p1]

        pitches = [p1]
        # Look for secondary non-harmonic fundamental >= 25% of peak energy
        mask = (freqs >= 65.0) & (freqs <= 1200.0)
        cand_freqs = freqs[mask]
        cand_mags = spec[mask]
        top_idx = np.argsort(cand_mags)[::-1][:8]
        for idx in top_idx:
            if cand_mags[idx] < spec_max * 0.30:
                continue
            f = float(cand_freqs[idx])
            # Ignore integer harmonics (2f, 3f, 4f) of primary_f0
            ratio = f / primary_f0
            if any(abs(ratio - h) < 0.08 for h in (1.0, 2.0, 3.0, 4.0, 5.0)):
                continue
            m = int(round(freq_to_midi_float(f)))
            if 36 <= m <= 92 and all(abs(m - ex) >= 2 for ex in pitches):
                pitches.append(m)
                break
        return sorted(pitches)

    def analyze_frame(
        self,
        clean_frame: np.ndarray,
        now: Optional[float] = None,
        nlms_erle_db: float = 0.0,
    ) -> PerceptionSnapshot:
        ts = now if now is not None else time.perf_counter()
        x = np.asarray(clean_frame, dtype=np.float32)
        rms = float(np.sqrt(np.mean(x * x)) + 1e-9)
        rms_dbfs = round(20.0 * math.log10(rms), 2)
        attack_slope = max(0.0, rms - self.prev_rms)

        self.rms_history.append(rms)
        f0_hz = self.estimate_f0_hz(x, self.sample_rate) if rms > 0.006 else 0.0
        midi_pitch = int(round(freq_to_midi_float(f0_hz))) if f0_hz > 20.0 else 0
        if f0_hz > 20.0:
            self.pitch_history_hz.append(f0_hz)

        # Vibrato estimation (cents std across recent frames)
        vibrato_cents = 0.0
        vibrato_rate_hz = 0.0
        if len(self.pitch_history_hz) >= 8 and f0_hz > 20.0:
            recent = np.array(list(self.pitch_history_hz)[-12:], dtype=np.float64)
            med = float(np.median(recent))
            if med > 20.0:
                cents_dev = 1200.0 * np.log2(np.maximum(recent, 20.0) / med)
                vibrato_cents = round(float(np.std(cents_dev)), 1)
                # Count zero crossings of deviation to approximate vibrato rate
                crossings = int(np.sum(np.diff(np.signbit(cents_dev))))
                dur_win = len(recent) * (len(x) / self.sample_rate)
                if dur_win > 0:
                    vibrato_rate_hz = round((crossings / 2.0) / dur_win, 2)

        # Onset & articulation (staccato vs legato) tracking
        if rms > 0.012 and not self._in_note:
            self._in_note = True
            self.active_onset_ts = ts
            self.prev_attack_slope = attack_slope
        elif rms <= 0.007 and self._in_note:
            self._in_note = False
            self.last_note_dur_s = max(0.01, ts - self.active_onset_ts)

        articulation = "rest"
        if rms > 0.006:
            # Classify staccato vs legato from envelope duty cycle, RMS dips, and attack slope
            rms_arr = np.array(self.rms_history, dtype=np.float64)
            active_ratio = float(np.mean(rms_arr > 0.010)) if len(rms_arr) > 0 else 1.0
            sharp_attack = max(attack_slope, self.prev_attack_slope) > 0.028
            short_gate = (0.0 < self.last_note_dur_s < 0.19) or (active_ratio < 0.62 and len(rms_arr) >= 6)
            if sharp_attack or short_gate:
                articulation = "staccato"
            else:
                articulation = "legato"

        self.prev_rms = rms
        double_stops = self.detect_double_stop(x, f0_hz, self.sample_rate) if f0_hz > 20.0 else []
        return PerceptionSnapshot(
            mode="cello",
            timestamp=ts,
            f0_hz=round(f0_hz, 2),
            midi_pitch=midi_pitch,
            note_name=midi_to_note_name(midi_pitch),
            vibrato_cents=vibrato_cents,
            vibrato_rate_hz=vibrato_rate_hz,
            articulation=articulation,
            attack_slope=round(attack_slope, 5),
            rms_level=round(rms, 5),
            rms_dbfs=rms_dbfs,
            double_stop_pitches=double_stops,
            nlms_erle_db=nlms_erle_db,
            pitch_history=[round(v, 2) for v in list(self.pitch_history_hz)[-32:]],
        )

    def analyze_utterance(
        self,
        mic_audio: np.ndarray,
        piano_ref_audio: Optional[np.ndarray] = None,
        nlms: Optional[NLMSEchoCanceller] = None,
        block_size: int = 2048,
    ) -> PerceptionSnapshot:
        """
        Process a multi-block audio buffer (e.g. a 0.4s-1.5s Cello phrase) through optional NLMS
        and extract stabilized pitch + articulation ("staccato" vs "legato").
        """
        canceller = nlms or NLMSEchoCanceller()
        clean = canceller.process_block(mic_audio, piano_ref_audio)

        f0_samples: List[float] = []
        blocks: List[Tuple[np.ndarray, float]] = []
        rms_blocks: List[float] = []
        slopes: List[float] = []
        prev_r = 0.0

        for start in range(0, len(clean) - block_size + 1, block_size):
            blk = clean[start : start + block_size]
            r = float(np.sqrt(np.mean(blk * blk)) + 1e-9)
            blocks.append((blk, r))
            rms_blocks.append(r)
            slopes.append(max(0.0, r - prev_r))
            prev_r = r

        rms_arr = np.array(rms_blocks or [0.0], dtype=np.float64)
        max_rms = float(np.max(rms_arr))
        note_thresh = max(0.012, max_rms * 0.35)
        for blk, r in blocks:
            if r > note_thresh:
                f0 = self.estimate_f0_hz(blk, self.sample_rate)
                if 55.0 <= f0 <= 1200.0:
                    f0_samples.append(f0)

        med_f0 = float(np.median(f0_samples)) if f0_samples else 0.0
        midi_pitch = int(round(freq_to_midi_float(med_f0))) if med_f0 > 20.0 else 0
        active_duty = float(np.mean(rms_arr > max(0.010, max_rms * 0.30)))
        max_slope = max(slopes or [0.0])

        # Compute zero-crossings of active bursts to detect repeated staccato strokes
        active_mask = rms_arr > max(0.010, max_rms * 0.28)
        burst_count = int(np.sum(np.diff(active_mask.astype(np.int32)) == 1)) + (1 if active_mask[0] else 0)

        if burst_count >= 2 or active_duty < 0.65 or (max_slope > 0.045 and active_duty < 0.80):
            articulation = "staccato"
        elif max_rms > 0.008:
            articulation = "legato"
        else:
            articulation = "rest"

        vib_cents = 0.0
        if len(f0_samples) >= 4 and med_f0 > 20.0:
            cents_dev = 1200.0 * np.log2(np.maximum(np.array(f0_samples), 20.0) / med_f0)
            vib_cents = round(float(np.std(cents_dev)), 1)

        return PerceptionSnapshot(
            mode="cello",
            timestamp=time.perf_counter(),
            f0_hz=round(med_f0, 2),
            midi_pitch=midi_pitch,
            note_name=midi_to_note_name(midi_pitch),
            vibrato_cents=vib_cents,
            vibrato_rate_hz=5.4 if vib_cents > 6.0 else 0.0,
            articulation=articulation,
            attack_slope=round(max_slope, 5),
            rms_level=round(float(np.mean(rms_arr)), 5),
            rms_dbfs=round(20.0 * math.log10(float(np.mean(rms_arr)) + 1e-9), 2),
            nlms_erle_db=canceller.last_erle_db,
            pitch_history=[round(v, 2) for v in f0_samples[-32:]],
        )


class VerbalDirectiveRecognizer:
    """
    CPU-only (0GB VRAM) Stage MC & Verbal Directive Recognizer.
    Parses spoken Japanese rehearsal instructions (or optional CPU INT8 Whisper audio)
    into structured L2 intent hints.
    """

    def __init__(self) -> None:
        self.last_directive: str = ""
        self._whisper_model: Any = None

    def transcribe_cpu_audio(self, audio_pcm: np.ndarray, sample_rate: int = 16000) -> str:
        """
        Optional CPU INT8 speech recognition via faster-whisper if installed.
        Never uses GPU VRAM (device='cpu', compute_type='int8').
        """
        try:
            import importlib.util

            if importlib.util.find_spec("faster_whisper") is None:
                return ""
            if self._whisper_model is None:
                from faster_whisper import WhisperModel  # type: ignore

                self._whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
            segments, _ = self._whisper_model.transcribe(audio_pcm, language="ja")
            txt = "".join(seg.text for seg in segments).strip()
            if txt:
                self.last_directive = txt
            return txt
        except Exception:
            return ""

    def parse_directive(self, text: str) -> Dict[str, Any]:
        clean = (text or "").strip()
        self.last_directive = clean
        hints: Dict[str, Any] = {"raw_text": clean}
        if not clean:
            return hints

        if any(k in clean for k in ("短調", "マイナー", "暗く", "悲劇")):
            hints["tonal_hint"] = "D_minor"
        elif any(k in clean for k in ("ドリア", "旋法", "古風")):
            hints["tonal_hint"] = "D_dorian"
        elif any(k in clean for k in ("長調", "明るく", "解放")):
            hints["tonal_hint"] = "F_major"

        if any(k in clean for k in ("乾いた", "浅く", "ドライ", "スタッカート", "キレ")):
            hints["pedal_hint"] = "dry"
            hints["density_delta"] = -0.15
        elif any(k in clean for k in ("深く", "残響", "ペダル", "豊か")):
            hints["pedal_hint"] = "resonant"

        if any(k in clean for k in ("挑発", "緊張", "激しく", "攻め")):
            hints["stance_hint"] = "provoke"
            hints["density_delta"] = 0.20
        elif any(k in clean for k in ("対位法", "カノン", "バッハ", "ポリフォニー")):
            hints["stance_hint"] = "counterpoint"
        elif any(k in clean for k in ("終止", "締め", "落ち着", "コーダ")):
            hints["stance_hint"] = "cadence"
        elif any(k in clean for k in ("模倣", "受け", "同じ")):
            hints["stance_hint"] = "imitate"

        return hints
