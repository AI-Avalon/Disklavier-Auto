"""
Concert-Hall Physical Modeling Audio Synthesizer & Dual-Pedal Acoustic Engine.

Upgrades audio synthesis to concert-hall chamber recording quality:
1. Grand Piano Physical Model:
   - Up to 10 inharmonic partials with register-dependent string stiffness B(p)
   - Triple-string unison micro-beating & dual-stage soundboard decay
   - Velocity- & Una-Corda-coupled hammer felt strike transient
   - Continuous Damper (CC#64) open-string sympathetic resonance + repedaling felt muting
   - Continuous Una Corda (CC#67) lateral shift attenuating upper partials (>= 3rd) & softening attack
2. Solo Cello Bowed-String & Body Formant Model:
   - Helmholtz bowed-string harmonic spectrum shaped by 4 cello body formants (200, 420, 950, 2150 Hz)
   - Rosin bow-bite onset transient, progressive 5.3Hz vibrato bloom, and dynamic bow-pressure swell
3. Concert Hall Stereo Stage & FFT Convolution Reverb (RT60 = 1.6s):
   - Natural pitch-width stereo imaging (bass left -> treble right; cello stage left-center)
   - Asymmetric early reflections + decorrelated stereo diffuse reverb tail via FFT convolution
4. Direct Stereo WAV (44.1kHz 16-bit PCM) & MP3 (192kbps via lameenc) Export.
"""

from __future__ import annotations

import math
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.signal import fftconvolve, lfilter

try:
    import sounddevice as sd
except Exception:  # pragma: no cover
    sd = None  # type: ignore[assignment]

try:
    import soundfile as sf
except Exception:  # pragma: no cover
    sf = None  # type: ignore[assignment]

try:
    import lameenc
except Exception:  # pragma: no cover
    lameenc = None  # type: ignore[assignment]

try:
    import mido
except Exception:  # pragma: no cover
    mido = None  # type: ignore[assignment]


def midi_to_hz(midi_pitch: int) -> float:
    return 440.0 * (2.0 ** ((float(midi_pitch) - 69.0) / 12.0))


def _cello_formant_gain(freq_hz: float) -> float:
    """Models cello body wood & bridge resonance peaks around 200, 420, 950, and 2150 Hz."""
    f = max(40.0, float(freq_hz))
    f1 = 1.10 * math.exp(-0.5 * ((math.log2(f / 205.0)) / 0.55) ** 2)
    f2 = 0.85 * math.exp(-0.5 * ((math.log2(f / 425.0)) / 0.48) ** 2)
    f3 = 0.55 * math.exp(-0.5 * ((math.log2(f / 950.0)) / 0.50) ** 2)
    f4 = 0.35 * math.exp(-0.5 * ((math.log2(f / 2150.0)) / 0.52) ** 2)
    roll = 1.0 / (1.0 + (f / 2900.0) ** 2)
    return (0.42 + f1 + f2 + f3 + f4) * roll


def _stereo_pan_gains(pan: float) -> Tuple[float, float]:
    """Constant-power stereo panner (-1.0 left .. +1.0 right)."""
    p = max(-1.0, min(1.0, float(pan)))
    ang = (p + 1.0) * (math.pi * 0.25)
    return math.cos(ang), math.sin(ang)


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
        if int(ev.get("pitch", ev.get("note", 0)) or 0) > 0:
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


_HALL_IR_CACHE: Dict[Tuple[int, float], Tuple[np.ndarray, np.ndarray]] = {}


def _build_hall_ir_stereo(sample_rate: int = 44100, rt60_s: float = 1.60) -> Tuple[np.ndarray, np.ndarray]:
    """Builds and caches a chamber concert hall stereo impulse response (early reflections + diffuse decay)."""
    key = (int(sample_rate), round(float(rt60_s), 3))
    if key in _HALL_IR_CACHE:
        return _HALL_IR_CACHE[key]

    n_ir = int(rt60_s * sample_rate)
    rng = np.random.default_rng(1841)
    t = np.arange(n_ir, dtype=np.float64) / sample_rate

    # Asymmetric early reflections (left vs right chamber hall walls & stage canopy)
    ir_l = np.zeros(n_ir, dtype=np.float64)
    ir_r = np.zeros(n_ir, dtype=np.float64)
    early_l = [(0.011, 0.24), (0.019, -0.18), (0.029, 0.15), (0.041, 0.11), (0.056, -0.09), (0.072, 0.07)]
    early_r = [(0.013, 0.22), (0.023, 0.17), (0.033, -0.14), (0.046, 0.11), (0.061, 0.09), (0.078, -0.07)]
    for dt_s, g in early_l:
        idx = min(n_ir - 1, int(dt_s * sample_rate))
        ir_l[idx] += g
    for dt_s, g in early_r:
        idx = min(n_ir - 1, int(dt_s * sample_rate))
        ir_r[idx] += g

    # Diffuse reverberation tail with warm HF air damping via 1-pole lfilter
    noise_l = lfilter([0.28], [1.0, -0.72], rng.standard_normal(n_ir))
    noise_r = lfilter([0.28], [1.0, -0.72], rng.standard_normal(n_ir))

    onset_fade = np.clip((t - 0.018) / 0.045, 0.0, 1.0)
    decay_env = np.exp(-6.9078 * t / rt60_s) * onset_fade
    ir_l += noise_l * decay_env * 0.030
    ir_r += noise_r * decay_env * 0.030
    _HALL_IR_CACHE[key] = (ir_l, ir_r)
    while len(_HALL_IR_CACHE) > 8:
        oldest_key = next(iter(_HALL_IR_CACHE))
        _HALL_IR_CACHE.pop(oldest_key, None)
    return ir_l, ir_r


def _apply_edge_fades(buf: np.ndarray, sample_rate: int, fade_in_s: float = 0.006, fade_out_s: float = 0.014) -> np.ndarray:
    """Applies raised-cosine micro fades at buffer edges to prevent clicks/pops."""
    n = len(buf)
    if n < 32:
        return buf
    fi = min(n // 4, max(0, int(fade_in_s * sample_rate)))
    fo = min(n // 3, max(0, int(fade_out_s * sample_rate)))
    if fi > 2:
        ramp_in = 0.5 * (1.0 - np.cos(np.linspace(0.0, math.pi, fi)))
        if buf.ndim == 2:
            buf[:fi, :] *= ramp_in[:, None]
        else:
            buf[:fi] *= ramp_in
    if fo > 2:
        ramp_out = 0.5 * (1.0 + np.cos(np.linspace(0.0, math.pi, fo)))
        if buf.ndim == 2:
            buf[-fo:, :] *= ramp_out[:, None]
        else:
            buf[-fo:] *= ramp_out
    return buf


def apply_concert_hall_reverb_stereo(
    dry_stereo: np.ndarray,
    sample_rate: int = 44100,
    wet_mix: float = 0.28,
    rt60_s: float = 1.60,
) -> np.ndarray:
    """Applies concert-hall stereo early reflections and diffuse FFT convolution reverb."""
    if dry_stereo.ndim != 2 or dry_stereo.shape[1] != 2 or len(dry_stereo) == 0:
        return dry_stereo
    ir_l, ir_r = _build_hall_ir_stereo(sample_rate=sample_rate, rt60_s=rt60_s)
    wet_l = fftconvolve(dry_stereo[:, 0], ir_l, mode="full")
    wet_r = fftconvolve(dry_stereo[:, 1], ir_r, mode="full")
    out_len = max(len(dry_stereo), len(wet_l))
    out = np.zeros((out_len, 2), dtype=np.float64)
    out[: len(dry_stereo), :] += dry_stereo * (1.0 - 0.12 * wet_mix)
    out[: len(wet_l), 0] += wet_l * wet_mix
    out[: len(wet_r), 1] += wet_r * wet_mix
    return out


class LocalAudioSynthesizer:
    """
    Concert-hall physical-model audio renderer for live turn feedback, pp audition previews,
    and full multi-act chamber masterwork stereo WAV/MP3 production.
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
        self._rng = np.random.default_rng(2026)

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
            return "Python Concert Hall Synth (sounddevice + Dual-Pedal Physics)"
        if mido is not None:
            return "Windows GS Wavetable + WebAudio"
        return "Offline Concert Hall Synth"

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
        dur = max(0.06, min(4.2, float(duration_s)))
        tail = 0.16 if muted_audition else 1.45
        n_samp = max(256, int((dur + tail) * sr))
        if damper_env_slice is not None and len(damper_env_slice) > 0:
            n_samp = min(n_samp, len(damper_env_slice))
        t = np.arange(n_samp, dtype=np.float64) / sr

        p_clamped = max(21, min(108, int(pitch)))
        f0 = midi_to_hz(p_clamped)
        vel_norm = max(0.14, min(1.0, float(velocity) / 112.0))
        veil = max(0.0, min(1.0, float(una_corda_val) / 127.0))

        # Low-register bass projection & Una Corda hammer softening
        reg_warmth = 1.15 if p_clamped < 52 else (0.92 if p_clamped > 78 else 1.0)
        amp_scale = (0.11 if muted_audition else 0.28) * (vel_norm ** 1.10) * (1.0 - 0.18 * veil) * reg_warmth

        # String stiffness inharmonicity B(p)
        inharm = 0.00018 * ((f0 / 261.63) ** 1.28)
        bright = (0.55 + 0.55 * vel_norm) * (1.0 - 0.58 * veil)

        # 10-harmonic piano overtone recipe with continuous Una Corda attenuation
        partial_specs = (
            (1.0, 1.00),
            (2.0, 0.44 * (1.0 - 0.20 * veil)),
            (3.0, 0.25 * (1.0 - 0.52 * veil) * bright),
            (4.0, 0.15 * (1.0 - 0.78 * veil) * bright),
            (5.0, 0.09 * (1.0 - 0.86 * veil) * bright),
            (6.0, 0.055 * (1.0 - 0.90 * veil) * bright),
            (7.0, 0.032 * (1.0 - 0.94 * veil) * bright),
            (8.0, 0.020 * (1.0 - 0.96 * veil) * bright),
        )
        wave = np.zeros(n_samp, dtype=np.float64)
        base_decay = (4.6 if muted_audition else (0.62 if p_clamped < 48 else 1.05)) + (f0 / 780.0)
        for h_num, weight in partial_specs:
            if weight <= 0.003:
                continue
            fh = f0 * h_num * math.sqrt(1.0 + inharm * (h_num ** 2))
            if fh >= sr * 0.44:
                continue
            # Dual-stage soundboard decay (prompt harmonic peak + warm body sustain)
            d_fast = base_decay * (1.0 + 0.55 * h_num)
            d_slow = max(0.35, base_decay * 0.48 + 0.16 * h_num)
            env_h = 0.62 * np.exp(-d_fast * t) + 0.38 * np.exp(-d_slow * t)
            wave += weight * np.sin(2.0 * np.pi * fh * t + 0.15 * h_num) * env_h

        # Triple-string unison micro-detuning for natural acoustic shimmer (above MIDI 42)
        if not muted_audition and p_clamped >= 42:
            detune_hz = f0 * 0.00042  # ~0.7 cents
            wave += 0.16 * (1.0 - 0.35 * veil) * np.sin(2.0 * np.pi * (f0 + detune_hz) * t + 1.1) * np.exp(-base_decay * t)

        # Hammer felt strike transient (short low-mid wooden knock, softer with Una Corda)
        if not muted_audition:
            knock_len = min(n_samp, int(0.016 * sr))
            if knock_len > 8:
                tk = np.arange(knock_len, dtype=np.float64) / sr
                knock_f = min(1400.0, max(160.0, f0 * 1.45))
                knock = (0.08 * vel_norm * (1.0 - 0.65 * veil)) * np.sin(2.0 * np.pi * knock_f * tk) * np.exp(-220.0 * tk)
                wave[:knock_len] += knock

        # Sympathetic string resonance bloom when damper pedal is lifted
        if not muted_audition and damper_env_slice is not None and len(damper_env_slice) >= n_samp:
            open_frac = np.clip((damper_env_slice[:n_samp] - 18.0) / 100.0, 0.0, 1.0)
            symp = 0.075 * open_frac * (
                np.sin(2.0 * np.pi * f0 * 2.0 * t + 0.4) * np.exp(-0.75 * t)
                + 0.45 * np.sin(2.0 * np.pi * f0 * 3.0 * t + 1.2) * np.exp(-0.95 * t)
            )
            wave += symp

        # Softened hammer strike attack proportional to Una Corda CC#67
        attack_s = 0.0045 + 0.016 * veil
        attack = np.minimum(1.0, t / attack_s)

        # Damper release behavior governed continuously by CC#64 envelope
        release_idx = min(n_samp, int(dur * sr))
        env_rel = np.ones(n_samp, dtype=np.float64)
        if 0 < release_idx < n_samp:
            rel_len = n_samp - release_idx
            if not muted_audition and damper_env_slice is not None and len(damper_env_slice) >= n_samp:
                cc_post = damper_env_slice[release_idx:n_samp]
                open_post = np.clip((cc_post - 14.0) / 105.0, 0.0, 1.0)
                inst_damp = (1.0 - open_post) * 24.0 + open_post * 1.15
                env_rel[release_idx:] = np.exp(-np.cumsum(inst_damp / sr))
            else:
                env_rel[release_idx:] = np.exp(-np.linspace(0.0, 6.2, rel_len))

        out = wave * attack * env_rel * amp_scale
        out = _apply_edge_fades(out, sr, fade_in_s=0.002, fade_out_s=0.012)
        return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)

    def synthesize_cello_phrase(
        self,
        notes: List[Dict[str, Any]],
        articulation: str = "legato",
        sample_rate: Optional[int] = None,
        total_dur_override: Optional[float] = None,
        return_stereo: bool = False,
    ) -> np.ndarray:
        sr = int(sample_rate or self.sample_rate)
        if not notes:
            empty = np.zeros((int(0.4 * sr), 2) if return_stereo else int(0.4 * sr), dtype=np.float32)
            return empty

        notes_use = list(notes) if total_dur_override is not None else list(notes[:8])
        max_n_end = max(float(n.get("onset_s", n.get("time_offset_s", 0.0))) + float(n.get("duration_s", 0.25)) for n in notes_use)
        total_dur = total_dur_override if total_dur_override is not None else min(4.5, max(0.50, max_n_end + 0.25))
        n_buf = max(256, int(total_dur * sr))
        buf = np.zeros(n_buf, dtype=np.float64)

        for n in notes_use:
            pitch = int(n.get("pitch", n.get("note", 50)) or 50)
            if pitch <= 0:
                continue
            onset = max(0.0, float(n.get("onset_s", n.get("time_offset_s", 0.0))))
            dur = max(0.08, min(3.2, float(n.get("duration_s", 0.28))))
            vel = max(32, min(105, int(n.get("velocity", 72))))
            start_i = int(onset * sr)
            if start_i >= n_buf:
                continue
            seg_len = min(n_buf - start_i, int((dur + 0.14) * sr))
            if seg_len < 64:
                continue

            t = np.arange(seg_len, dtype=np.float64) / sr
            f0 = midi_to_hz(max(36, min(88, pitch)))

            # Progressive vibrato (blooms gently after bow attack)
            vib_bloom = np.clip((t - 0.05) / 0.22, 0.0, 1.0)
            vib = 1.0 + 0.0040 * vib_bloom * np.sin(2.0 * np.pi * 5.3 * t)
            phase = 2.0 * np.pi * f0 * np.cumsum(vib) / sr

            # Helmholtz bowed string spectrum shaped by cello body formants
            tone = np.zeros(seg_len, dtype=np.float64)
            for h_num in range(1, 10):
                fh = f0 * h_num
                if fh >= sr * 0.42:
                    break
                form_w = _cello_formant_gain(fh) / (h_num ** 1.08)
                tone += form_w * np.sin(h_num * phase + 0.08 * h_num)

            # Subtle bow-hair rosin noise during initial articulation bite
            rosin_len = min(seg_len, int((0.024 if articulation == "staccato" else 0.045) * sr))
            if rosin_len > 16:
                rosin = self._rng.standard_normal(rosin_len) * 0.045
                # Smooth rosin noise slightly
                rosin = np.convolve(rosin, np.array([0.25, 0.5, 0.25]), mode="same")
                t_r = np.arange(rosin_len, dtype=np.float64) / sr
                tone[:rosin_len] += rosin * np.exp(-55.0 * t_r)

            if articulation == "staccato" or dur < 0.16:
                env = np.minimum(1.0, t / 0.012) * np.exp(-8.5 * t)
            else:
                # Singing bow pressure swell
                fade_out = np.minimum(1.0, np.maximum(0.0, (seg_len / sr - t) / 0.08))
                swell = 0.80 + 0.20 * np.sin(math.pi * np.clip(t / max(0.12, dur), 0.0, 1.0))
                env = np.minimum(1.0, t / 0.055) * fade_out * swell

            vel_amp = 0.24 * ((vel / 80.0) ** 0.9)
            buf[start_i : start_i + seg_len] += tone * env * vel_amp

        buf = np.nan_to_num(buf, nan=0.0, posinf=0.0, neginf=0.0)
        buf = _apply_edge_fades(buf, sr, fade_in_s=0.005, fade_out_s=0.012)
        if not return_stereo:
            return buf.astype(np.float32)

        g_l, g_r = _stereo_pan_gains(-0.22)
        st = np.column_stack([buf * g_l, buf * g_r]).astype(np.float32)
        return st

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
        total_dur_override: Optional[float] = None,
        stereo: bool = False,
    ) -> Any:
        sr = int(sample_rate or self.sample_rate)
        note_evs = [e for e in events if int(e.get("pitch", e.get("note", 0)) or 0) > 0]
        if not note_evs:
            empty = np.zeros((int(0.5 * sr), 2) if stereo else int(0.5 * sr), dtype=np.float32)
            return (empty, dict(self.last_pedal_stats)) if return_pedal_metrics else empty

        max_end = max(
            float(e.get("onset_s", e.get("time_offset_s", 0.0))) + float(e.get("duration_s", 0.35))
            for e in note_evs
        )
        total_s = total_dur_override if total_dur_override is not None else min(6.5, max(0.9, max_end + 0.95))
        n_buf = int(total_s * sr)
        buf = np.zeros((n_buf, 2) if stereo else n_buf, dtype=np.float64)

        cc64_env, cc64_pts = _build_pedal_envelope(events, 64, n_buf, sr)
        cc67_env, cc67_pts = _build_pedal_envelope(events, 67, n_buf, sr)

        for ev in note_evs:
            p = int(ev.get("pitch", ev.get("note", 60)))
            v = int(ev.get("velocity", 68))
            onset = max(0.0, float(ev.get("onset_s", ev.get("time_offset_s", 0.0))))
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
                seg = note_wave[: end_i - start_i]
                if stereo:
                    # Natural keyboard stereo imaging: bass left (-0.42) to treble right (+0.42)
                    pan = -0.42 + 0.84 * max(0.0, min(1.0, (p - 21) / 87.0))
                    g_l, g_r = _stereo_pan_gains(pan)
                    buf[start_i:end_i, 0] += seg * g_l
                    buf[start_i:end_i, 1] += seg * g_r
                else:
                    buf[start_i:end_i] += seg

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

    def render_masterwork_stereo_audio(
        self,
        work: Dict[str, Any],
        sample_rate: Optional[int] = None,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Renders a complete multi-act chamber composition (`work` from CoCompositionArchitect)
        into 44.1kHz stereo concert-hall audio with piano soundboard, cello body formants,
        dual-pedal continuous physics, and 1.6s RT60 stereo FFT convolution reverb.
        """
        sr = int(sample_rate or self.sample_rate)
        mode = str(work.get("mode", "cello"))
        human_evs = list(work.get("human_events") or [])
        piano_evs = list(work.get("piano_events") or [])

        total_s = max(12.0, float(work.get("total_duration_s", 55.0)) + 1.8)
        piano_stereo, pedal_stats = self.synthesize_phrase_events(
            piano_evs,
            sample_rate=sr,
            return_pedal_metrics=True,
            total_dur_override=total_s,
            stereo=True,
        )

        dry_mix = piano_stereo.astype(np.float64)
        if human_evs:
            if mode == "cello":
                human_stereo = self.synthesize_cello_phrase(
                    human_evs,
                    articulation="legato",
                    sample_rate=sr,
                    total_dur_override=total_s,
                    return_stereo=True,
                ).astype(np.float64)
            else:
                # Mode A: Human Piano seated slightly left-center (-0.25) vs AI Disklavier (+0.20)
                h_mono = self.synthesize_phrase_events(
                    human_evs,
                    sample_rate=sr,
                    return_pedal_metrics=False,
                    total_dur_override=total_s,
                    stereo=False,
                ).astype(np.float64)
                gl, gr = _stereo_pan_gains(-0.24)
                human_stereo = np.column_stack([h_mono * gl, h_mono * gr])
            n_min = min(len(dry_mix), len(human_stereo))
            dry_mix[:n_min] += human_stereo[:n_min]

        # Apply chamber concert hall stereo reverb (RT60 = 1.60s)
        wet_mix = apply_concert_hall_reverb_stereo(dry_mix, sample_rate=sr, wet_mix=0.28, rt60_s=1.60)
        clean = np.nan_to_num(wet_mix, nan=0.0, posinf=0.0, neginf=0.0)

        # RMS-targeted loudness mastering (-17 dBFS target) + transparent soft-knee transient ceiling (< 0.88)
        rms_raw = float(np.sqrt(np.mean(clean ** 2)) + 1e-12)
        target_rms = 0.145
        gain = min(2.35, max(0.65, target_rms / rms_raw))
        clean = clean * gain
        peak_after = float(np.max(np.abs(clean))) if len(clean) else 1.0
        if peak_after > 0.74:
            over = np.maximum(0.0, np.abs(clean) - 0.74)
            clean = np.where(
                np.abs(clean) > 0.74,
                np.sign(clean) * (0.74 + 0.14 * np.tanh(over / 0.28)),
                clean,
            )
        clean = _apply_edge_fades(clean, sr, fade_in_s=0.008, fade_out_s=0.025)
        mastered = np.tanh(clean * 0.96).astype(np.float32)

        rms = float(np.sqrt(np.mean(mastered.astype(np.float64) ** 2)) + 1e-12)
        peak = float(np.max(np.abs(mastered))) if len(mastered) else 0.0
        dbfs = round(20.0 * math.log10(max(1e-6, rms)), 2)
        clip_cnt = int(np.sum(np.abs(mastered) > 1.0))

        metrics = {
            "sample_rate": sr,
            "channels": 2,
            "duration_s": round(len(mastered) / float(sr), 2),
            "rms": round(rms, 5),
            "dbfs": dbfs,
            "peak": round(peak, 5),
            "clipping_count": clip_cnt,
            "has_nan_or_inf": bool(np.any(~np.isfinite(mastered))),
            **pedal_stats,
        }
        return mastered, metrics

    def export_stereo_mp3(
        self,
        stereo_audio: np.ndarray,
        out_mp3_path: Path,
        sample_rate: Optional[int] = None,
        bitrate_kbps: int = 128,
    ) -> Dict[str, Any]:
        """Saves 44.1kHz Stereo MP3 directly (no intermediate bloated WAV required)."""
        sr = int(sample_rate or self.sample_rate)
        clipped = np.clip(stereo_audio, -1.0, 1.0)
        pcm16 = (clipped * 32767.0).astype(np.int16)
        if lameenc is None:
            raise RuntimeError("lameenc is required for direct MP3 export")
        out_mp3_path.parent.mkdir(parents=True, exist_ok=True)
        encoder = lameenc.Encoder()
        encoder.set_bit_rate(int(bitrate_kbps))
        encoder.set_in_sample_rate(sr)
        encoder.set_channels(2 if pcm16.ndim == 2 else 1)
        encoder.set_quality(2)
        mp3_bytes = encoder.encode(pcm16.tobytes()) + encoder.flush()
        out_mp3_path.write_bytes(mp3_bytes)
        return {
            "mp3_path": str(out_mp3_path),
            "mp3_bytes": out_mp3_path.stat().st_size if out_mp3_path.exists() else 0,
            "bitrate_kbps": int(bitrate_kbps),
        }

    def export_stereo_wav_and_mp3(
        self,
        stereo_audio: np.ndarray,
        out_wav_path: Optional[Path],
        out_mp3_path: Optional[Path] = None,
        sample_rate: Optional[int] = None,
        mp3_bitrate_kbps: int = 128,
    ) -> Dict[str, Any]:
        """Saves 44.1kHz 16-bit PCM Stereo WAV (optional) and Stereo MP3 (default 128kbps)."""
        sr = int(sample_rate or self.sample_rate)
        clipped = np.clip(stereo_audio, -1.0, 1.0)
        pcm16 = (clipped * 32767.0).astype(np.int16)

        wav_bytes = 0
        if out_wav_path is not None:
            out_wav_path.parent.mkdir(parents=True, exist_ok=True)
            if sf is not None:
                sf.write(str(out_wav_path), pcm16, sr, subtype="PCM_16")
            else:
                import wave

                with wave.open(str(out_wav_path), "wb") as wf:
                    wf.setnchannels(2 if pcm16.ndim == 2 else 1)
                    wf.setsampwidth(2)
                    wf.setframerate(sr)
                    wf.writeframes(pcm16.tobytes())
            wav_bytes = out_wav_path.stat().st_size if out_wav_path.exists() else 0

        mp3_saved = False
        if out_mp3_path is not None and lameenc is not None:
            self.export_stereo_mp3(
                stereo_audio,
                out_mp3_path=out_mp3_path,
                sample_rate=sr,
                bitrate_kbps=mp3_bitrate_kbps,
            )
            mp3_saved = True

        return {
            "wav_path": str(out_wav_path) if out_wav_path is not None else None,
            "wav_bytes": wav_bytes,
            "mp3_path": str(out_mp3_path) if mp3_saved else None,
            "mp3_bytes": out_mp3_path.stat().st_size if (mp3_saved and out_mp3_path and out_mp3_path.exists()) else 0,
        }

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

        # Light stereo hall shimmer when playing live buffer
        gl, gr = _stereo_pan_gains(0.0)
        stereo_dry = np.column_stack([clean * gl, clean * gr])
        hall_stereo = apply_concert_hall_reverb_stereo(stereo_dry, sample_rate=sr, wet_mix=0.22, rt60_s=1.2)
        peak_raw = float(np.max(np.abs(hall_stereo))) if len(hall_stereo) else 1.0
        if peak_raw > 0.85:
            hall_stereo = hall_stereo * (0.82 / peak_raw)
        hall_stereo = _apply_edge_fades(hall_stereo, sr, fade_in_s=0.005, fade_out_s=0.018)
        limited = np.tanh(hall_stereo * 0.92).astype(np.float32)

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
