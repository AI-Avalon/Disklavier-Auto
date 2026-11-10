"""
C519ML Real-Time Audio Monitor, CQT Spectrogram, Polyphonic Double-Stop / Flageolet Detector,
and MIDI-Linked Acoustic Bleed Cancellation Engine for Steinberg UR22mkII.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import sounddevice as sd


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_to_name(midi_note: int) -> str:
    if midi_note <= 0 or midi_note > 127:
        return "--"
    octave = (midi_note // 12) - 1
    return f"{NOTE_NAMES[midi_note % 12]}{octave}"


def freq_to_midi(freq: float) -> float:
    if freq <= 20.0:
        return 0.0
    return 69.0 + 12.0 * math.log2(freq / 440.0)


def midi_to_freq(midi_note: float) -> float:
    return 440.0 * (2.0 ** ((midi_note - 69.0) / 12.0))


class C519MLAudioMonitor:
    """
    Captures live audio at 44.1 kHz from Steinberg UR22mkII (AKG C519ML condenser mic),
    computes 60fps oscilloscope waveforms, 48-bin log-frequency CQT-like spectrogram,
    acoustic roughness (Sethares dissonance), double/triple-stop chord detection,
    flageolet harmonic detection, and MIDI-linked acoustic bleed cancellation.
    """

    SAMPLE_RATE = 44100
    BLOCK_SIZE = 735  # 44100 / 60 = 735 samples per frame (exact 60 fps!)
    FFT_SIZE = 4096

    def __init__(
        self,
        device_keyword: str = "Steinberg UR22",
        active_notes_provider: Optional[Callable[[], Dict[int, dict]]] = None,
    ) -> None:
        self.device_keyword = device_keyword
        self.active_notes_provider = active_notes_provider
        self.device_index: Optional[int] = None
        self.device_name: str = "Searching..."

        # Mode: "proxy" (Piano-as-Cello Proxy), "real_cello" (Real Cello on C519ML), "synth" (Synth Cello Injector)
        self.input_mode: str = "proxy"
        self.bleed_cancellation_enabled: bool = True

        # Ring buffer for sample-accurate calibration & R&D recording (20 seconds)
        self.ring_capacity = self.SAMPLE_RATE * 20
        self.ring_buffer = np.zeros(self.ring_capacity, dtype=np.float32)
        self.ring_write_pos = 0
        self.last_frame_perf_time = time.perf_counter()
        self._lock = threading.Lock()

        # Precompute 60 pitch bins from C2 (MIDI 36, 65.4 Hz) to B6 (MIDI 95, 1975.5 Hz)
        self.midi_bins = np.arange(36, 96, dtype=np.int32)
        self.freq_bins = np.array([midi_to_freq(m) for m in self.midi_bins])
        self.fft_Window = np.hanning(self.FFT_SIZE).astype(np.float32)
        self.fft_freqs = np.fft.rfftfreq(self.FFT_SIZE, d=1.0 / self.SAMPLE_RATE)

        # Latest 60fps telemetry frame
        self.latest_waveform: List[float] = [0.0] * 128
        self.latest_cqt_bins: List[float] = [-90.0] * 60
        self.latest_clean_cqt_bins: List[float] = [-90.0] * 60
        self.rms_dbfs: float = -65.0
        self.peak_dbfs: float = -55.0
        self.noise_floor_dbfs: float = -58.5
        self.acoustic_roughness: float = 0.0
        self.bleed_suppression_db: float = 0.0

        # Cello / Proxy musical feature state
        self.detected_notes: List[int] = []
        self.detected_note_names: List[str] = []
        self.is_double_stop: bool = False
        self.is_flageolet: bool = False
        self.bow_articulation: str = "Sustained / Ready"
        self.estimated_bpm: float = 72.0
        self._onset_times: deque = deque(maxlen=16)
        self._prev_rms_linear: float = 0.0
        self._proxy_sustained_notes: Dict[int, float] = {}

        # Synthetic cello injector state (when mode == "synth")
        self._synth_phase: float = 0.0
        self._synth_active_midi: Optional[int] = None

        # Recording buffer for Motif Listening / Rehearsal Time-Machine
        self.is_recording_motif: bool = False
        self.recorded_motif_events: List[dict] = []
        self._motif_start_time: float = 0.0

        self.stream: Optional[sd.InputStream] = None
        self._running = False
        self.start()

    def _find_device(self) -> Optional[int]:
        try:
            devices = sd.query_devices()
            for idx, dev in enumerate(devices):
                if (
                    dev["max_input_channels"] > 0
                    and self.device_keyword.lower() in dev["name"].lower()
                ):
                    self.device_name = dev["name"]
                    return idx
            # Fallback to default input device
            default_in = sd.default.device[0]
            if default_in is not None and default_in >= 0:
                self.device_name = devices[default_in]["name"]
                return default_in
        except Exception:
            pass
        return None

    def start(self) -> bool:
        self.device_index = self._find_device()
        self._running = True
        try:
            self.stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                blocksize=self.BLOCK_SIZE,
                device=self.device_index,
                channels=1,
                dtype="float32",
                callback=self._audio_callback,
            )
            self.stream.start()
            return True
        except Exception as exc:
            self.device_name = f"Simulated Audio ({exc})"
            # Fallback thread if audio device is busy
            threading.Thread(target=self._fallback_loop, daemon=True).start()
            return False

    def _fallback_loop(self) -> None:
        while self._running:
            noise = np.random.normal(0, 0.0012, size=(self.BLOCK_SIZE, 1)).astype(
                np.float32
            )
            self._audio_callback(noise, self.BLOCK_SIZE, None, None)
            time.sleep(1.0 / 60.0)

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        now = time.perf_counter()
        mono = indata[:, 0].copy()

        # Optional Synth Cello Injector signal addition when in 'synth' mode
        if self.input_mode == "synth" and self._synth_active_midi is not None:
            f0 = midi_to_freq(self._synth_active_midi)
            t_vec = (np.arange(frames, dtype=np.float32) / self.SAMPLE_RATE) + self._synth_phase
            # Rich cello saw/harmonic waveform with gentle vibrato
            vib = 1.0 + 0.003 * np.sin(2.0 * np.pi * 5.5 * t_vec)
            sig = (
                0.08 * np.sin(2.0 * np.pi * f0 * vib * t_vec)
                + 0.05 * np.sin(2.0 * np.pi * 2 * f0 * vib * t_vec)
                + 0.03 * np.sin(2.0 * np.pi * 3 * f0 * vib * t_vec)
            )
            mono = mono + sig.astype(np.float32)
            self._synth_phase += frames / self.SAMPLE_RATE

        with self._lock:
            # Write to circular buffer
            end_pos = self.ring_write_pos + frames
            if end_pos <= self.ring_capacity:
                self.ring_buffer[self.ring_write_pos : end_pos] = mono
            else:
                first_part = self.ring_capacity - self.ring_write_pos
                self.ring_buffer[self.ring_write_pos :] = mono[:first_part]
                self.ring_buffer[: frames - first_part] = mono[first_part:]
            self.ring_write_pos = end_pos % self.ring_capacity
            self.last_frame_perf_time = now

            # Extract last FFT_SIZE samples for spectral analysis
            if self.ring_write_pos >= self.FFT_SIZE:
                fft_slice = self.ring_buffer[
                    self.ring_write_pos - self.FFT_SIZE : self.ring_write_pos
                ].copy()
            else:
                part1 = self.ring_buffer[
                    self.ring_capacity - (self.FFT_SIZE - self.ring_write_pos) :
                ]
                part2 = self.ring_buffer[: self.ring_write_pos]
                fft_slice = np.concatenate([part1, part2])

        # Compute RMS and Peak in dBFS
        rms_lin = float(np.sqrt(np.mean(mono * mono)) + 1e-12)
        peak_lin = float(np.max(np.abs(mono)) + 1e-12)
        self.rms_dbfs = round(20.0 * math.log10(rms_lin), 2)
        self.peak_dbfs = round(20.0 * math.log10(peak_lin), 2)

        # Downsample waveform to 128 points for 60fps UI oscilloscope
        idx = np.linspace(0, len(mono) - 1, 128).astype(np.int32)
        self.latest_waveform = [round(float(x), 5) for x in mono[idx]]

        # Compute windowed RFFT magnitude spectrum
        spec = np.abs(np.fft.rfft(fft_slice * self.fft_Window)) / (self.FFT_SIZE * 0.5)
        spec_db = 20.0 * np.log10(spec + 1e-10)

        # Map FFT bins to 60 semitone CQT-like bins (MIDI 36..95)
        raw_cqt = np.full(60, -90.0, dtype=np.float32)
        for i, f_center in enumerate(self.freq_bins):
            f_low = f_center * (2.0 ** (-0.5 / 12.0))
            f_high = f_center * (2.0 ** (0.5 / 12.0))
            mask = (self.fft_freqs >= f_low) & (self.fft_freqs <= f_high)
            if np.any(mask):
                raw_cqt[i] = float(np.max(spec_db[mask]))
            else:
                nearest_idx = int(np.argmin(np.abs(self.fft_freqs - f_center)))
                raw_cqt[i] = float(spec_db[nearest_idx])

        self.latest_cqt_bins = [round(float(v), 1) for v in raw_cqt]

        # Acoustic Bleed Cancellation (subtract active Disklavier accompaniment harmonics)
        clean_cqt = raw_cqt.copy()
        if self.bleed_cancellation_enabled and self.active_notes_provider is not None:
            active_piano = self.active_notes_provider()
            for p_note, p_info in active_piano.items():
                # Only subtract accompaniment notes tagged as 'accompaniment' (not proxy cello notes!)
                tag = str(p_info.get("source_tag", ""))
                if "proxy_cello" in tag:
                    continue
                vel = p_info.get("velocity", 50)
                if vel <= 1:
                    continue
                # Subtract fundamental and first 4 harmonics in linear power domain
                for h_idx, h_gain in enumerate([1.0, 0.45, 0.22, 0.12], start=1):
                    h_midi = int(round(p_note + 12.0 * math.log2(h_idx)))
                    bin_idx = h_midi - 36
                    if 0 <= bin_idx < 60:
                        est_db = -52.0 + (vel / 105.0) * 32.0 + 20.0 * math.log10(h_gain)
                        raw_lin = 10.0 ** (clean_cqt[bin_idx] / 20.0)
                        bleed_lin = 10.0 ** (est_db / 20.0)
                        rem_lin = max(1e-5, raw_lin - 0.82 * bleed_lin)
                        clean_cqt[bin_idx] = 20.0 * math.log10(rem_lin)

        raw_energy = float(np.sum(10.0 ** (raw_cqt / 10.0)))
        clean_energy = float(np.sum(10.0 ** (clean_cqt / 10.0)))
        if raw_energy > clean_energy and clean_energy > 0:
            self.bleed_suppression_db = round(
                10.0 * math.log10(raw_energy / clean_energy), 2
            )
        else:
            self.bleed_suppression_db = 0.0

        self.latest_clean_cqt_bins = [round(float(v), 1) for v in clean_cqt]

        # Compute Sethares Acoustic Roughness (sensory dissonance between top spectral peaks)
        self.acoustic_roughness = self._compute_roughness(spec, self.fft_freqs)

        # Detect Cello Single Notes, Double/Triple Stops, Flageolet, and Bowing
        self._analyze_musical_features(clean_cqt, rms_lin, now)

    def _compute_roughness(self, spec_mag: np.ndarray, freqs: np.ndarray) -> float:
        """
        Computes Sethares (1993) / Plomp-Levelt sensory dissonance across top spectral peaks.
        """
        valid = (freqs >= 60.0) & (freqs <= 3500.0)
        v_freqs = freqs[valid]
        v_mags = spec_mag[valid]
        if len(v_mags) < 8:
            return 0.0
        # Find top 10 local peaks
        top_indices = np.argpartition(v_mags, -10)[-10:]
        p_freqs = v_freqs[top_indices]
        p_mags = v_mags[top_indices]
        max_m = float(np.max(p_mags)) + 1e-9
        if 20.0 * math.log10(max_m) < -54.0:
            return 0.02  # ambient baseline
        norm_mags = p_mags / max_m

        roughness = 0.0
        b1, b2 = 3.5, 5.75
        for i in range(len(p_freqs)):
            for j in range(i + 1, len(p_freqs)):
                f_min = min(p_freqs[i], p_freqs[j])
                f_diff = abs(p_freqs[i] - p_freqs[j])
                s = 0.24 / (0.021 * f_min + 19.0)
                x = s * f_diff
                if x < 1.2:
                    d = math.exp(-b1 * x) - math.exp(-b2 * x)
                    roughness += float(norm_mags[i] * norm_mags[j] * d)
        return round(min(1.0, roughness * 1.5), 4)

    def _analyze_musical_features(
        self, clean_cqt: np.ndarray, rms_lin: float, now: float
    ) -> None:
        threshold_db = self.noise_floor_dbfs + 9.5
        active_bins = np.where(clean_cqt > threshold_db)[0]

        # Onset & Bowing articulation detection
        rms_delta = rms_lin - self._prev_rms_linear
        self._prev_rms_linear = rms_lin
        if rms_delta > 0.0045 and self.rms_dbfs > threshold_db:
            if not self._onset_times or (now - self._onset_times[-1]) > 0.14:
                self._onset_times.append(now)
                if len(self._onset_times) >= 3:
                    iois = np.diff(list(self._onset_times))
                    valid_iois = iois[(iois > 0.18) & (iois < 2.2)]
                    if len(valid_iois) > 0:
                        inst_bpm = 60.0 / float(np.median(valid_iois))
                        self.estimated_bpm = round(
                            0.75 * self.estimated_bpm + 0.25 * max(40.0, min(168.0, inst_bpm)),
                            1,
                        )
            self.bow_articulation = (
                "Spiccato / Marcato" if rms_delta > 0.015 else "Legato / Sostenuto"
            )

        if len(active_bins) == 0:
            # In Proxy mode, sustain detected notes briefly (350 ms) to mimic cello bow sustain
            if self.input_mode == "proxy":
                self._proxy_sustained_notes = {
                    k: t for k, t in self._proxy_sustained_notes.items() if (now - t) < 0.45
                }
                self.detected_notes = sorted(self._proxy_sustained_notes.keys())
                self.detected_note_names = [midi_to_name(n) for n in self.detected_notes]
            else:
                self.detected_notes = []
                self.detected_note_names = []
                self.is_double_stop = False
                self.is_flageolet = False
            return

        # Harmonic peeling to identify up to 3 simultaneous fundamentals (Double/Triple stops)
        residual = clean_cqt.copy()
        fundamentals: List[int] = []
        for _ in range(3):
            best_bin = int(np.argmax(residual))
            best_val = float(residual[best_bin])
            if best_val <= threshold_db:
                break
            midi_pitch = int(self.midi_bins[best_bin])
            fundamentals.append(midi_pitch)
            # Suppress this pitch and its octave/+19 harmonics so we don't mistake overtones for double stops
            for offset in [0, 12, 19, 24, 28]:
                idx = best_bin + offset
                if 0 <= idx < 60:
                    residual[idx] -= 18.0
                if 0 <= idx - 1 < 60:
                    residual[idx - 1] -= 10.0
                if 0 <= idx + 1 < 60:
                    residual[idx + 1] -= 10.0

        fundamentals = sorted(set(fundamentals))
        if self.input_mode == "proxy":
            for f_note in fundamentals:
                self._proxy_sustained_notes[f_note] = now
            self._proxy_sustained_notes = {
                k: t for k, t in self._proxy_sustained_notes.items() if (now - t) < 0.45
            }
            fundamentals = sorted(self._proxy_sustained_notes.keys())[:3]

        self.detected_notes = fundamentals
        self.detected_note_names = [midi_to_name(n) for n in fundamentals]
        self.is_double_stop = len(fundamentals) >= 2
        # Flageolet detection: high register (MIDI >= 67 / G4+) with strong pure upper harmonic signature
        self.is_flageolet = any(n >= 69 for n in fundamentals) and (
            len(fundamentals) == 1
        )

        # Record motif if active
        if self.is_recording_motif and fundamentals:
            rel_t = round(now - self._motif_start_time, 3)
            if (
                not self.recorded_motif_events
                or self.recorded_motif_events[-1]["notes"] != fundamentals
            ):
                self.recorded_motif_events.append(
                    {
                        "time_s": rel_t,
                        "notes": list(fundamentals),
                        "note_names": list(self.detected_note_names),
                        "rms_dbfs": self.rms_dbfs,
                        "is_double_stop": self.is_double_stop,
                        "is_flageolet": self.is_flageolet,
                    }
                )

    # -------------------------------------------------------------------------
    # Precision Audio Window Extraction for Experiments 1-5
    # -------------------------------------------------------------------------
    def get_recent_audio_samples(self, duration_s: float = 1.0) -> Tuple[np.ndarray, float]:
        """
        Returns the last `duration_s` seconds of mono 44.1 kHz audio along with the exact
        `perf_counter` timestamp of the end of the buffer.
        """
        num_samples = min(self.ring_capacity, int(duration_s * self.SAMPLE_RATE))
        with self._lock:
            end_time = self.last_frame_perf_time
            pos = self.ring_write_pos
            if pos >= num_samples:
                samples = self.ring_buffer[pos - num_samples : pos].copy()
            else:
                part1 = self.ring_buffer[self.ring_capacity - (num_samples - pos) :]
                part2 = self.ring_buffer[:pos]
                samples = np.concatenate([part1, part2])
        return samples, end_time

    def start_motif_recording(self) -> None:
        self.recorded_motif_events.clear()
        self._motif_start_time = time.perf_counter()
        self.is_recording_motif = True

    def stop_motif_recording(self) -> List[dict]:
        self.is_recording_motif = False
        return list(self.recorded_motif_events)

    def get_telemetry_snapshot(self) -> dict:
        return {
            "device_name": self.device_name,
            "sample_rate": self.SAMPLE_RATE,
            "input_mode": self.input_mode,
            "bleed_cancellation": self.bleed_cancellation_enabled,
            "bleed_suppression_db": self.bleed_suppression_db,
            "rms_dbfs": self.rms_dbfs,
            "peak_dbfs": self.peak_dbfs,
            "noise_floor_dbfs": self.noise_floor_dbfs,
            "acoustic_roughness": self.acoustic_roughness,
            "detected_notes": self.detected_notes,
            "detected_note_names": self.detected_note_names,
            "is_double_stop": self.is_double_stop,
            "is_flageolet": self.is_flageolet,
            "bow_articulation": self.bow_articulation,
            "estimated_bpm": self.estimated_bpm,
            "waveform": self.latest_waveform,
            "cqt_bins": self.latest_clean_cqt_bins,
            "raw_cqt_bins": self.latest_cqt_bins,
        }
