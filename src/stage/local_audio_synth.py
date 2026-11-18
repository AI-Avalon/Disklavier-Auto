"""
Python Native Direct Audio Synthesizer with Dual-Pedal Physical Acoustic Simulation.

Simulates Yamaha Disklavier physical acoustics on Windows speakers / UR22 or offline buffers:
- Damper Pedal (Right / CC#64): sample-accurate continuous damper-lift envelope (0..127),
  sympathetic string resonance during half/full pedal, and immediate string muting when
  CC#64 drops to 0 during repedaling (110 -> 0 -> 95) or phrase release.
- Una Corda Pedal (Left / CC#67): lateral hammer-shift physics attenuating upper partials
  (>= 3rd/4th harmonics) and softening hammer strike transient proportionally to CC#67 (0..127).
- Optional Windows `Microsoft GS Wavetable Synth` MIDI fallback.
- Real-time RMS / dBFS / clipping / NaN-Inf / pedal acoustic telemetry.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

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


def _build_pedal_envelope(
    events: List[Dict[str, Any]],
    control: int,
    n_samples: int,
    sample_rate: int,
) -> Tuple[np.ndarray, List[Tuple[float, int]]]:
    """Builds a sample-accurate continuous [0..127] pedal envelope with 35ms mechanical ramp."""
    pts: List[Tuple[float, int]] = []
    key_name = f"pedal_cc{control}"
    for ev in events:
        if int(ev.get("pitch", 0) or 0) > 0:
            continue
        if int(ev.get("control", 0) or 0) == control or key_name in ev:
            t = max(0.0, float(ev.get("onset_s", ev.get("time_offset_s", 0.0))))
            val = max(0, min(127, int(ev.get("value", ev.get(key_name, ev.get("val", 0))))))
            pts.append((t, val))

    if not pts:
        return np.zeros(n_samples, dtype=np.float64), []

    pts.sort(key=lambda x: x[0])
    env = np.zeros(n_samples, dtype=np.float64)
    ramp_samples = max(1, int(0.035 * sample_rate))
    cur_val = float(pts[0][1]) if pts[0][0] <= 0.01 else 0.0
    prev_idx = 0

    for t_ev, v_ev in pts:
        idx = max(0, min(n_samples - 1, int(t_ev * sample_rate)))
        if idx > prev_idx:
            env[prev_idx:idx] = cur_val
        end_ramp = min(n_samples, idx + ramp_samples)
        if end_ramp > idx:
            env[idx:end_ramp] = np.linspace(cur_val, float(v_ev), end_ramp - idx)
        cur_val = float(v_ev)
        prev_idx = end_ramp

    if prev_idx < n_samples:
        env[prev_idx:] = cur_val
    return env, pts


class LocalAudioSynthesizer:
    """
    Python-side physical-model audio renderer for Virtual Cellist/Pianist inquiries,
    pp audition previews of rejected drafts, and adopted two-hand + dual-pedal piano phrases.
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
        self.last_pedal_stats: Dict[str, Any] = {
            "pedal_cc64_active": False,
            "pedal_cc67_active": False,
            "cc64_peak": 0,
            "cc67_peak": 0,
            "repedal_count": 0,
            "half_pedal_ratio": 0.0,
            "una_corda_veil_db": 0.0,
        }
        self.last_backend: str = self._detect_backend()
        self.audition_preview_enabled: bool = True
        self._midi_synth_port: Any = None
        if self.enabled and self.use_midi_synth_fallback:
            self._open_midi_synth()

    def _detect_backend(self) -> str:
        if not self.enabled:
            return "Production (Hardware Only / Synth OFF)"
        if sd is not None:
            return "Python Direct Audio (sounddevice + Dual-Pedal Physics)"
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
        una_corda_val: float = 0.0,
        damper_env_slice: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        sr = int(sample_rate or self.sample_rate)
        dur = max(0.06, min(2.6, float(duration_s)))
        tail = 0.14 if muted_audition else 0.95
        n_samp = max(256, int((dur + tail) * sr))
        if damper_env_slice is not None and len(damper_env_slice) > 0:
            n_samp = min(n_samp, len(damper_env_slice))
        t = np.arange(n_samp, dtype=np.float64) / sr

        f0 = midi_to_hz(max(21, min(108, int(pitch))))
        vel_norm = max(0.12, min(1.0, float(velocity) / 112.0))
        veil = max(0.0, min(1.0, float(una_corda_val) / 127.0))

        # Una Corda lateral shift lowers overall attack brightness & slightly softens hammer impact
        amp_scale = (0.12 if muted_audition else 0.31) * (vel_norm ** 1.12) * (1.0 - 0.18 * veil)

        inharm = 0.00014 * ((f0 / 261.63) ** 1.2)
        partials = (
            (1.00, 1.00),
            (2.00, 0.42 * (1.0 - 0.22 * veil)),
            (3.00, 0.24 * (1.0 - 0.45 * veil)),
            (4.00, 0.12 * (1.0 - 0.78 * veil)),
            (5.00, 0.06 * (1.0 - 0.88 * veil)),
        )
        wave = np.zeros(n_samp, dtype=np.float64)
        for h_num, weight in partials:
            fh = f0 * h_num * math.sqrt(1.0 + inharm * (h_num ** 2))
            if fh >= sr * 0.45:
                continue
            decay_rate = (4.8 if muted_audition else 1.45) + 0.48 * h_num + (f0 / 680.0)
            wave += weight * np.sin(2.0 * np.pi * fh * t) * np.exp(-decay_rate * t)

        # Sympathetic string resonance bloom when damper pedal is lifted
        if not muted_audition and damper_env_slice is not None and len(damper_env_slice) >= n_samp:
            open_frac = np.clip((damper_env_slice[:n_samp] - 18.0) / 100.0, 0.0, 1.0)
            symp = 0.065 * open_frac * np.sin(2.0 * np.pi * f0 * 2.0 * t + 0.4) * np.exp(-0.9 * t)
            wave += symp

        # Softened hammer strike attack proportional to Una Corda CC#67
        attack_s = 0.005 + 0.017 * veil
        attack = np.minimum(1.0, t / attack_s)

        # Damper release behavior governed continuously by CC#64 envelope
        release_idx = min(n_samp, int(dur * sr))
        env_rel = np.ones(n_samp, dtype=np.float64)
        if 0 < release_idx < n_samp:
            rel_len = n_samp - release_idx
            if not muted_audition and damper_env_slice is not None and len(damper_env_slice) >= n_samp:
                cc_post = damper_env_slice[release_idx:n_samp]
                open_post = np.clip((cc_post - 14.0) / 105.0, 0.0, 1.0)
                # Closed damper damps rapidly (24/s); open damper lets string ring (1.3/s)
                inst_damp = (1.0 - open_post) * 24.0 + open_post * 1.3
                env_rel[release_idx:] = np.exp(-np.cumsum(inst_damp / sr))
            else:
                env_rel[release_idx:] = np.exp(-np.linspace(0.0, 6.2, rel_len))

        out = wave * attack * env_rel * amp_scale
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
            max(float(n.get("onset_s", 0.0)) + float(n.get("duration_s", 0.25)) for n in notes[:6]) + 0.14,
        )
        total_dur = min(2.0, total_dur)
        buf = np.zeros(int(total_dur * sr), dtype=np.float64)

        for n in notes[:6]:
            pitch = int(n.get("pitch", n.get("note", 50)) or 50)
            if pitch <= 0:
                continue
            onset = max(0.0, float(n.get("onset_s", 0.0)))
            dur = max(0.08, min(0.65, float(n.get("duration_s", 0.25))))
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
                una_corda_val=80.0,
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
        return_pedal_metrics: bool = False,
    ) -> Any:
        sr = int(sample_rate or self.sample_rate)
        note_evs = [e for e in events if int(e.get("pitch", 0) or 0) > 0]
        if not note_evs:
            empty = np.zeros(int(0.5 * sr), dtype=np.float32)
            return (empty, dict(self.last_pedal_stats)) if return_pedal_metrics else empty

        max_end = max(
            float(e.get("onset_s", 0.0)) + float(e.get("duration_s", 0.35)) for e in note_evs
        )
        total_s = min(4.2, max(0.9, max_end + 0.65))
        n_buf = int(total_s * sr)
        buf = np.zeros(n_buf, dtype=np.float64)

        cc64_env, cc64_pts = _build_pedal_envelope(events, 64, n_buf, sr)
        cc67_env, cc67_pts = _build_pedal_envelope(events, 67, n_buf, sr)

        for ev in note_evs:
            p = int(ev.get("pitch", 60))
            v = int(ev.get("velocity", 68))
            onset = max(0.0, float(ev.get("onset_s", 0.0)))
            dur = max(0.06, float(ev.get("duration_s", 0.35)))
            start_i = int(onset * sr)
            if start_i >= n_buf:
                continue
            uc_val = float(cc67_env[start_i]) if start_i < len(cc67_env) else 0.0
            note_wave = self.synthesize_piano_note(
                pitch=p,
                velocity=v,
                duration_s=dur,
                sample_rate=sr,
                muted_audition=False,
                una_corda_val=uc_val,
                damper_env_slice=cc64_env[start_i:],
            )
            end_i = min(n_buf, start_i + len(note_wave))
            if end_i > start_i:
                buf[start_i:end_i] += note_wave[: end_i - start_i]

        out = np.nan_to_num(buf, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)
        if not return_pedal_metrics:
            return out

        cc64_vals = [v for _, v in cc64_pts] or [0]
        cc67_vals = [v for _, v in cc67_pts] or [0]
        repedals = sum(
            1
            for i in range(1, len(cc64_vals) - 1)
            if cc64_vals[i] <= cc64_vals[i - 1] - 25 and cc64_vals[i + 1] >= cc64_vals[i] + 20
        )
        half_frac = float(np.mean((cc64_env >= 30.0) & (cc64_env <= 85.0))) if len(cc64_env) else 0.0
        mean_uc = float(np.mean(cc67_env)) if len(cc67_env) else 0.0
        veil_db = round(-3.8 * (mean_uc / 127.0), 2)
        stats = {
            "pedal_cc64_active": max(cc64_vals) > 0,
            "pedal_cc67_active": max(cc67_vals) > 0,
            "cc64_peak": int(max(cc64_vals)),
            "cc67_peak": int(max(cc67_vals)),
            "repedal_count": int(repedals),
            "half_pedal_ratio": round(half_frac, 3),
            "una_corda_veil_db": veil_db,
        }
        return out, stats

    def _emit_midi_synth_async(self, events: List[Dict[str, Any]]) -> None:
        if self._midi_synth_port is None or mido is None:
            return
        note_evs = [e for e in events if int(e.get("pitch", 0) or 0) > 0][:14]

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
                segments.append(self.synthesize_phrase_events(human_notes[:6], sample_rate=sr))
            segments.append(np.zeros(int(0.06 * sr), dtype=np.float32))

        if self.audition_preview_enabled and rejected_candidates:
            first_rej = rejected_candidates[0]
            rej_evs = getattr(first_rej, "events", None) or (
                first_rej.get("events", []) if isinstance(first_rej, dict) else []
            )
            if rej_evs:
                segments.append(self.synthesize_audition_fragment(rej_evs, sample_rate=sr))
                segments.append(np.zeros(int(0.05 * sr), dtype=np.float32))

        adopted_wave, pedal_stats = self.synthesize_phrase_events(
            adopted_events, sample_rate=sr, return_pedal_metrics=True
        )
        segments.append(adopted_wave)

        raw = np.concatenate(segments) if segments else adopted_wave
        has_bad = bool(np.any(~np.isfinite(raw)))
        clean = np.nan_to_num(raw, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float64)
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
            self.last_pedal_stats = pedal_stats
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
            **pedal_stats,
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
                **self.last_pedal_stats,
            }

    def close(self) -> None:
        self.stop_audio()
        if self._midi_synth_port is not None:
            try:
                self._midi_synth_port.close()
            except Exception:
                pass
            self._midi_synth_port = None
