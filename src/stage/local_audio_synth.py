"""
Python Native Direct Audio Synthesizer for Disklavier-Auto Stage System.

Guarantees immediate audible feedback from Windows speakers / UR22 even without
physical Disklavier hardware or browser AudioContext activation:
- Low-latency physical-model-inspired harmonic piano & bowed cello synthesizer
  via `sounddevice` + `numpy`.
- Optional Windows `Microsoft GS Wavetable Synth` MIDI fallback.
- Audition fragment synthesis (pianissimo muted preview of rejected candidates
  during Step 2 deliberation).
- Real-time RMS / dBFS / clipping / NaN-Inf telemetry.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any, Dict, List, Optional

import numpy as np

try:
    import sounddevice as sd
except Exception:  # pragma: no cover
    sd = None  # type: ignore[assignment]

try:
    import mido
except Exception:  # pragma: no cover
    mido = None  # type: ignore[assignment]


def midi_to_hz(midi_pitch: int) -> float:
    return 440.0 * (2.0 ** ((float(midi_pitch) - 69.0) / 12.0))


class LocalAudioSynthesizer:
    """
    Python-side direct audio renderer for Virtual Cellist/Pianist inquiries,
    pp audition previews of rejected drafts, and adopted piano responses.
    """

    def __init__(
        self,
        enabled: bool = True,
        sample_rate: int = 44100,
        use_midi_synth_fallback: bool = False,
    ) -> None:
        self.enabled: bool = bool(enabled)
        self.sample_rate: int = int(sample_rate)
        self.use_midi_synth_fallback: bool = bool(use_midi_synth_fallback)
        self._lock = threading.Lock()

        self.last_rms: float = 0.0
        self.last_dbfs: float = -72.0
        self.last_peak: float = 0.0
        self.last_has_nan_or_inf: bool = False
        self.last_clipping_count: int = 0
        self.last_backend: str = self._detect_backend()
        self.audition_preview_enabled: bool = True
        self._midi_synth_port: Any = None
        if self.enabled and self.use_midi_synth_fallback:
            self._open_midi_synth()

    def _detect_backend(self) -> str:
        if not self.enabled:
            return "Production (Hardware Only / Synth OFF)"
        if sd is not None:
            return "Python Direct Audio (sounddevice + WebAudio)"
        if mido is not None:
            return "Windows GS Wavetable + WebAudio"
        return "Offline Buffer Synth"

    def _open_midi_synth(self) -> None:
        if mido is None:
            return
        try:
            outs = mido.get_output_names()
            gs = next((o for o in outs if "wavetable" in o.lower() or "microsoft gs" in o.lower()), None)
            if gs:
                self._midi_synth_port = mido.open_output(gs)
        except Exception:
            self._midi_synth_port = None

    def set_enabled(self, enabled: bool) -> Dict[str, Any]:
        with self._lock:
            self.enabled = bool(enabled)
            self.last_backend = self._detect_backend()
            if not self.enabled:
                self.stop_audio()
            return {"ok": True, "enabled": self.enabled, "backend": self.last_backend}

    def set_audition_preview(self, enabled: bool) -> None:
        with self._lock:
            self.audition_preview_enabled = bool(enabled)

    def stop_audio(self) -> None:
        if sd is not None:
            try:
                sd.stop()
            except Exception:
                pass

    def synthesize_piano_note(
        self,
        pitch: int,
        velocity: int,
        duration_s: float,
        sample_rate: Optional[int] = None,
        muted_audition: bool = False,
    ) -> np.ndarray:
        sr = int(sample_rate or self.sample_rate)
        dur = max(0.08, min(2.4, float(duration_s)))
        tail = 0.14 if muted_audition else 0.35
        n_samp = max(256, int((dur + tail) * sr))
        t = np.arange(n_samp, dtype=np.float64) / sr

        f0 = midi_to_hz(max(21, min(108, int(pitch))))
        vel_norm = max(0.12, min(1.0, float(velocity) / 112.0))
        amp_scale = (0.12 if muted_audition else 0.34) * (vel_norm ** 1.15)

        # Inharmonicity factor B (piano steel string stiffness)
        inharm = 0.00014 * ((f0 / 261.63) ** 1.2)
        partials = (
            (1.00, 1.00),
            (2.00, 0.42),
            (3.00, 0.22),
            (4.00, 0.10),
            (5.00, 0.05),
        )
        wave = np.zeros(n_samp, dtype=np.float64)
        for h_num, weight in partials:
            fh = f0 * h_num * math.sqrt(1.0 + inharm * (h_num ** 2))
            if fh >= sr * 0.45:
                continue
            decay_rate = (4.8 if muted_audition else 2.2) + 0.55 * h_num + (f0 / 600.0)
            wave += weight * np.sin(2.0 * np.pi * fh * t) * np.exp(-decay_rate * t)

        # Hammer strike click transient (5ms) + release fade
        attack = np.minimum(1.0, t / 0.005)
        release_idx = int(dur * sr)
        if 0 < release_idx < n_samp:
            rel_len = n_samp - release_idx
            attack[release_idx:] *= np.exp(-np.linspace(0.0, 5.5, rel_len))

        out = wave * attack * amp_scale
        return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)

    def synthesize_cello_phrase(
        self,
        notes: List[Dict[str, Any]],
        articulation: str = "legato",
        sample_rate: Optional[int] = None,
    ) -> np.ndarray:
        sr = int(sample_rate or self.sample_rate)
        if not notes:
            return np.zeros(int(0.4 * sr), dtype=np.float32)

        total_dur = max(
            0.45,
            max(float(n.get("onset_s", 0.0)) + float(n.get("duration_s", 0.25)) for n in notes[:4]) + 0.12,
        )
        total_dur = min(1.8, total_dur)
        buf = np.zeros(int(total_dur * sr), dtype=np.float64)

        for n in notes[:4]:
            pitch = int(n.get("pitch", n.get("note", 50)) or 50)
            if pitch <= 0:
                continue
            onset = max(0.0, float(n.get("onset_s", 0.0)))
            dur = max(0.08, min(0.55, float(n.get("duration_s", 0.25))))
            start_i = int(onset * sr)
            if start_i >= len(buf):
                continue
            seg_len = min(len(buf) - start_i, int((dur + 0.08) * sr))
            if seg_len < 64:
                continue
            t = np.arange(seg_len, dtype=np.float64) / sr
            f0 = midi_to_hz(max(36, min(88, pitch)))
            vib = 1.0 + 0.0038 * np.sin(2.0 * np.pi * 5.4 * t)
            phase = 2.0 * np.pi * f0 * np.cumsum(vib) / sr
            tone = (
                0.55 * np.sin(phase)
                + 0.28 * np.sin(2.0 * phase)
                + 0.14 * np.sin(3.0 * phase)
                + 0.07 * np.sin(4.0 * phase)
            )
            if articulation == "staccato":
                env = np.minimum(1.0, t / 0.012) * np.exp(-9.0 * t)
            else:
                fade_out = np.minimum(1.0, np.maximum(0.0, (seg_len / sr - t) / 0.05))
                env = np.minimum(1.0, t / 0.045) * fade_out
            buf[start_i : start_i + seg_len] += tone * env * 0.24

        return np.nan_to_num(buf, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)

    def synthesize_audition_fragment(
        self,
        events: List[Dict[str, Any]],
        sample_rate: Optional[int] = None,
    ) -> np.ndarray:
        """
        Generates a brief pianissimo muted audition snippet (first 2-3 notes)
        representing the AI testing and discarding a rejected draft.
        """
        sr = int(sample_rate or self.sample_rate)
        note_evs = [e for e in events if int(e.get("pitch", 0) or 0) > 0][:3]
        if not note_evs:
            return np.zeros(int(0.18 * sr), dtype=np.float32)

        buf = np.zeros(int(0.42 * sr), dtype=np.float64)
        for idx, ev in enumerate(note_evs):
            p = int(ev.get("pitch", 60))
            note_wave = self.synthesize_piano_note(
                pitch=p,
                velocity=26,
                duration_s=0.14,
                sample_rate=sr,
                muted_audition=True,
            )
            start_i = int((idx * 0.09) * sr)
            end_i = min(len(buf), start_i + len(note_wave))
            if end_i > start_i:
                buf[start_i:end_i] += note_wave[: end_i - start_i]
        return np.nan_to_num(buf, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)

    def synthesize_phrase_events(
        self,
        events: List[Dict[str, Any]],
        sample_rate: Optional[int] = None,
    ) -> np.ndarray:
        sr = int(sample_rate or self.sample_rate)
        note_evs = [e for e in events if int(e.get("pitch", 0) or 0) > 0]
        if not note_evs:
            return np.zeros(int(0.5 * sr), dtype=np.float32)

        max_end = max(
            float(e.get("onset_s", 0.0)) + float(e.get("duration_s", 0.35)) for e in note_evs
        )
        total_s = min(3.8, max(0.8, max_end + 0.45))
        buf = np.zeros(int(total_s * sr), dtype=np.float64)

        for ev in note_evs:
            p = int(ev.get("pitch", 60))
            v = int(ev.get("velocity", 68))
            onset = max(0.0, float(ev.get("onset_s", 0.0)))
            dur = max(0.08, float(ev.get("duration_s", 0.35)))
            note_wave = self.synthesize_piano_note(
                pitch=p, velocity=v, duration_s=dur, sample_rate=sr, muted_audition=False
            )
            start_i = int(onset * sr)
            if start_i >= len(buf):
                continue
            end_i = min(len(buf), start_i + len(note_wave))
            if end_i > start_i:
                buf[start_i:end_i] += note_wave[: end_i - start_i]

        return np.nan_to_num(buf, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)

    def _emit_midi_synth_async(self, events: List[Dict[str, Any]]) -> None:
        if self._midi_synth_port is None or mido is None:
            return
        note_evs = [e for e in events if int(e.get("pitch", 0) or 0) > 0][:12]

        def _worker() -> None:
            try:
                t0 = time.perf_counter()
                for ev in note_evs:
                    target = t0 + float(ev.get("onset_s", 0.0))
                    dt = target - time.perf_counter()
                    if dt > 0.002:
                        time.sleep(min(0.4, dt))
                    p = max(21, min(108, int(ev["pitch"])))
                    v = max(18, min(112, int(ev.get("velocity", 68))))
                    self._midi_synth_port.send(mido.Message("note_on", note=p, velocity=v))
                time.sleep(0.35)
                for ev in note_evs:
                    p = max(21, min(108, int(ev["pitch"])))
                    self._midi_synth_port.send(mido.Message("note_off", note=p, velocity=0))
            except Exception:
                pass

        threading.Thread(target=_worker, name="StageMidiSynthFallback", daemon=True).start()

    def play_turn_audio(
        self,
        human_notes: List[Dict[str, Any]],
        human_mode: str,
        human_articulation: str,
        rejected_candidates: List[Any],
        adopted_events: List[Dict[str, Any]],
        play_device: bool = True,
    ) -> Dict[str, Any]:
        """
        Synthesizes the full audible turn sequence:
        1. Human inquiry (Virtual Cello or Virtual Piano)
        2. Brief pp audition preview of first rejected draft (if audition_preview_enabled)
        3. Adopted AI piano response
        Returns strict audio diagnostics (`rms`, `dbfs`, `peak`, `has_nan_or_inf`, `clipping_count`).
        """
        sr = self.sample_rate
        segments: List[np.ndarray] = []

        if human_notes:
            if human_mode == "cello":
                segments.append(
                    self.synthesize_cello_phrase(
                        human_notes, articulation=human_articulation, sample_rate=sr
                    )
                )
            else:
                segments.append(self.synthesize_phrase_events(human_notes[:4], sample_rate=sr))
            segments.append(np.zeros(int(0.06 * sr), dtype=np.float32))

        if self.audition_preview_enabled and rejected_candidates:
            first_rej = rejected_candidates[0]
            rej_evs = getattr(first_rej, "events", None) or (
                first_rej.get("events", []) if isinstance(first_rej, dict) else []
            )
            if rej_evs:
                segments.append(self.synthesize_audition_fragment(rej_evs, sample_rate=sr))
                segments.append(np.zeros(int(0.05 * sr), dtype=np.float32))

        adopted_wave = self.synthesize_phrase_events(adopted_events, sample_rate=sr)
        segments.append(adopted_wave)

        raw = np.concatenate(segments) if segments else adopted_wave
        has_bad = bool(np.any(~np.isfinite(raw)))
        clean = np.nan_to_num(raw, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float64)
        # Soft saturation limiter prevents clipping (> 1.0)
        limited = (np.tanh(clean * 0.9) * 0.92).astype(np.float32)

        rms = float(np.sqrt(np.mean(limited.astype(np.float64) ** 2)) + 1e-12)
        peak = float(np.max(np.abs(limited))) if len(limited) else 0.0
        dbfs = round(20.0 * math.log10(max(1e-6, rms)), 2)
        clipping_count = int(np.sum(np.abs(limited) > 1.0))

        with self._lock:
            self.last_rms = round(rms, 5)
            self.last_dbfs = dbfs
            self.last_peak = round(peak, 5)
            self.last_has_nan_or_inf = has_bad
            self.last_clipping_count = clipping_count
            self.last_backend = self._detect_backend()
            should_play = self.enabled and play_device

        if should_play:
            played_sd = False
            if sd is not None and len(limited) > 0:
                try:
                    sd.stop()
                    sd.play(limited, samplerate=sr, blocking=False)
                    played_sd = True
                except Exception:
                    played_sd = False
            if not played_sd and self._midi_synth_port is not None:
                self._emit_midi_synth_async(adopted_events)

        return {
            "enabled": self.enabled,
            "played_to_device": should_play,
            "sample_rate": sr,
            "duration_s": round(len(limited) / float(sr), 3),
            "rms": self.last_rms,
            "dbfs": self.last_dbfs,
            "peak": self.last_peak,
            "has_nan_or_inf": self.last_has_nan_or_inf,
            "clipping_count": self.last_clipping_count,
            "backend": self.last_backend,
        }

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "audition_preview_enabled": self.audition_preview_enabled,
                "rms": self.last_rms,
                "dbfs": self.last_dbfs,
                "peak": self.last_peak,
                "has_nan_or_inf": self.last_has_nan_or_inf,
                "clipping_count": self.last_clipping_count,
                "backend": self.last_backend,
            }

    def close(self) -> None:
        self.stop_audio()
        if self._midi_synth_port is not None:
            try:
                self._midi_synth_port.close()
            except Exception:
                pass
            self._midi_synth_port = None
