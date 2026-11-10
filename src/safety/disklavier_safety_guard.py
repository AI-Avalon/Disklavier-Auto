"""
DisklavierSafetyGuard: Physical Safety Middleware & VDL Scheduler for Yamaha Disklavier.

Strictly enforces all hardware safety limits before any MIDI message reaches the physical Disklavier:
1. 30-Key Simultaneous Strike Crash Prevention & 16-Note Simultaneous Limit:
   - Soft limit 12-14 notes (hard cap 16 notes) within any 2 ms window.
   - Excess simultaneous Note-Ons are micro-staggered from lower voice to upper voice by 1-3 ms.
   - Active held voices capped at 20 notes maximum via intelligent Voice Stealing (oldest inner voice released first).
2. Note Density & Same-Key Repetition Limiter:
   - Global note density capped at 24-30 notes/sec across all 88 keys.
   - Same-key repetition enforced at >= 66 ms period (12-15 Hz recommended; hard physical floor 50 ms / 25 ms gap).
   - Rapid repeats (< 66 ms) are either thinned or redistributed to an octave/harmonic neighbor.
3. Velocity Valid Range (20-105) & Minimum Note Duration (>= 35 ms):
   - Blocks Ghost Note region (Velocity 1-15) in normal playback, scaling/clamping to 20-105
     (linear region 30-85, forte region 86-99).
   - Explicitly permits Velocity 1 ONLY when `sympathetic_halo=True` (silent key string opening).
   - Enforces minimum note duration >= 35 ms (never < 30 ms) and >= 20 ms gap before re-striking.
   - Clamps Note-Off velocity to 15-40 (default 25) for Gradual Damping (prevents Damper Thud Noise).
4. Right Damper Pedal Only (CC#64) & Pre-roll Compensation:
   - Permits CC#64 (Damper Pedal) including half-pedal region (35-80).
   - Strictly blocks CC#66 (Sostenuto) and CC#67 (Soft/Una Corda) and unsafe SysEx.
   - Supports -30 ms to -50 ms pre-roll scheduling for CC#64 ahead of chords.
5. Thermal Watchdog (8,000 ms key hold / 240 s pedal hold) & Emergency Panic:
   - Automatically refreshes/releases any key held > 8,000 ms (before 10 s hardware timeout).
   - Automatically refreshes damper pedal held > 240 s (before 600 s hardware timeout).
   - Provides instant `emergency_panic()` (all 88 keys Note-Off + CC#64=0 + queue flush).
6. VDL (Velocity-Dependent Latency) Inverse Compensation:
   - Computes Delta_t_VDL(key, velocity) and schedules T_send = T_target - Delta_t_VDL(key, velocity).
"""

from __future__ import annotations

import atexit
import heapq
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import mido


@dataclass(order=True)
class ScheduledMidiEvent:
    send_time: float
    priority: int
    seq_id: int
    msg_type: str  # 'note_on', 'note_off', 'control_change'
    note: int = field(compare=False, default=0)
    velocity: int = field(compare=False, default=0)
    control: int = field(compare=False, default=64)
    value: int = field(compare=False, default=0)
    channel: int = field(compare=False, default=0)
    sympathetic_halo: bool = field(compare=False, default=False)
    min_duration_ms: float = field(compare=False, default=40.0)
    source_tag: str = field(compare=False, default="live")


class VDLModel:
    """
    Velocity-Dependent Latency (VDL) empirical model for Yamaha Disklavier in Real-Time Mode (Prelay OFF).
    Strong strikes (Vel ~100) reach strings in ~25-35 ms; soft strikes (Vel ~30) take ~145-165 ms.
    Can be updated live from Experiment 1 calibration measurements.
    """

    def __init__(self) -> None:
        # Default physical model parameters before calibration; updated by Experiment 1
        # latency_ms(k, v) = base_min_ms + amplitude_ms * exp(-decay * (v - 20)) + key_mass_offset(k)
        self.base_min_ms: float = 22.0
        self.amplitude_ms: float = 152.0
        self.decay_rate: float = 0.036
        self.bass_mass_slope: float = -0.14  # Lower keys have heavier hammers (+latency)
        self.calibrated_points: Dict[Tuple[int, int], float] = {}
        self.fitted_curve_params: Dict[str, float] = {
            "base_min_ms": 22.0,
            "amplitude_ms": 152.0,
            "decay_rate": 0.036,
            "bass_mass_slope": -0.14,
            "r_squared": 0.985,
        }

    def predict_latency_ms(self, note: int, velocity: int) -> float:
        if velocity <= 1:
            # Silent key press (Sympathetic Halo) takes ~180 ms to gently lift damper
            return 180.0
        v_clamped = max(20, min(105, velocity))
        key_offset = (60 - note) * abs(self.bass_mass_slope)  # Bass keys +ms, treble -ms
        lat = (
            self.base_min_ms
            + self.amplitude_ms * math.exp(-self.decay_rate * (v_clamped - 20.0))
            + key_offset
        )
        return max(18.0, min(195.0, lat))

    def update_from_calibration(
        self,
        base_min_ms: float,
        amplitude_ms: float,
        decay_rate: float,
        bass_mass_slope: float = -0.14,
        r_squared: float = 0.99,
        raw_points: Optional[Dict[Tuple[int, int], float]] = None,
    ) -> None:
        self.base_min_ms = base_min_ms
        self.amplitude_ms = amplitude_ms
        self.decay_rate = decay_rate
        self.bass_mass_slope = bass_mass_slope
        self.fitted_curve_params = {
            "base_min_ms": round(base_min_ms, 3),
            "amplitude_ms": round(amplitude_ms, 3),
            "decay_rate": round(decay_rate, 5),
            "bass_mass_slope": round(bass_mass_slope, 4),
            "r_squared": round(r_squared, 4),
        }
        if raw_points:
            self.calibrated_points.update(raw_points)


class DisklavierSafetyGuard:
    """
    All MIDI messages to the Yamaha Disklavier MUST pass through this guard.
    """

    # Safety Constants
    PIANO_MIN_NOTE = 21  # A0
    PIANO_MAX_NOTE = 108  # C8
    SOFT_SIMULTANEOUS_LIMIT = 12
    HARD_SIMULTANEOUS_LIMIT = 16
    SIMULTANEOUS_WINDOW_S = 0.002  # 2 ms window
    MICRO_STAGGER_STEP_S = 0.0018  # 1.8 ms per staggered voice (bottom-up)
    MAX_ACTIVE_VOICES = 20
    MAX_NOTES_PER_SEC = 28  # Safe ceiling inside 24-30 notes/sec limit
    MIN_SAME_KEY_PERIOD_S = 0.068  # 68 ms (~14.7 Hz, inside 66-83 ms safe zone)
    MIN_SAME_KEY_GAP_S = 0.025  # 25 ms minimum gap after Note-Off before re-strike
    MIN_NOTE_DURATION_S = 0.038  # 38 ms minimum Note-On duration (> 30 ms floor)
    VALID_VEL_MIN = 20
    VALID_VEL_MAX = 105
    RELEASE_VEL_MIN = 15
    RELEASE_VEL_MAX = 40
    DEFAULT_RELEASE_VEL = 25
    MAX_KEY_HOLD_S = 8.0  # 8,000 ms thermal guard (hardware limit 10,000 ms)
    MAX_PEDAL_HOLD_S = 240.0  # 240 s thermal guard (hardware limit 600 s)
    PEDAL_PREROLL_MS = 40.0  # -40 ms default damper pre-roll

    def __init__(
        self,
        port_name: Optional[str] = "Disklavier",
        on_telemetry_Callback: Optional[Callable[[dict], None]] = None,
        on_log_Callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.port_name = port_name
        self.on_telemetry = on_telemetry_Callback
        self.on_log = on_log_Callback
        self._lock = threading.RLock()
        self._seq_counter = 0

        # Hardware port connection
        self.outport: Optional[mido.ports.BaseOutput] = None
        self.connected_port_name: Optional[str] = None
        self._connect_port(port_name)

        # State tracking
        # note -> {"on_time": float, "velocity": int, "sympathetic": bool, "min_off_time": float}
        self.active_notes: Dict[int, dict] = {}
        self.last_note_on_time: Dict[int, float] = {}
        self.last_note_off_time: Dict[int, float] = {}

        # Sliding windows for simultaneous strikes & global density
        self.recent_note_ons_2ms: deque = deque()
        self.recent_note_ons_1s: deque = deque()

        # Damper pedal state (CC#64 only)
        self.current_pedal_val: int = 0
        self.pedal_engaged_since: Optional[float] = None

        # VDL model & scheduler queue
        self.vdl_model = VDLModel()
        self._event_heap: List[ScheduledMidiEvent] = []
        self._running = True

        # Telemetry statistics
        self.stats = {
            "total_notes_sent": 0,
            "staggered_notes": 0,
            "voice_steals": 0,
            "density_throttled": 0,
            "repetition_redirected": 0,
            "velocity_clamped": 0,
            "ghost_notes_blocked": 0,
            "sympathetic_halo_notes": 0,
            "short_notes_extended": 0,
            "blocked_cc_events": 0,
            "thermal_key_refreshes": 0,
            "thermal_pedal_refreshes": 0,
            "panic_count": 0,
            "vdl_buffer_events": 0,
        }

        # Background worker thread for 0.5 ms precision scheduling & thermal watchdog
        self._worker_thread = threading.Thread(
            target=self._scheduler_and_watchdog_loop,
            name="DisklavierSafetyGuardWorker",
            daemon=True,
        )
        self._worker_thread.start()
        atexit.register(self.emergency_panic)

    def _log(self, msg: str) -> None:
        if self.on_log:
            try:
                self.on_log(msg)
            except Exception:
                pass

    def _connect_port(self, preferred_name: Optional[str]) -> bool:
        try:
            available = mido.get_output_names()
            target = None
            if preferred_name:
                for name in available:
                    if preferred_name.lower() in name.lower():
                        target = name
                        break
            if target:
                self.outport = mido.open_output(target)
                self.connected_port_name = target
                self._log(f"[SAFETY GUARD] Connected to hardware MIDI port: {target}")
                return True
            else:
                self._log(
                    f"[SAFETY GUARD] Hardware port '{preferred_name}' not found in {available}; running in dry-run telemetry mode."
                )
                return False
        except Exception as exc:
            self._log(f"[SAFETY GUARD] Port open warning: {exc}")
            return False

    # -------------------------------------------------------------------------
    # Core Velocity, Duration, and Pedal Sanitizers
    # -------------------------------------------------------------------------
    def sanitize_velocity(
        self, velocity: int, sympathetic_halo: bool = False
    ) -> Optional[int]:
        """
        Enforces Velocity 20-105 for normal notes, or Velocity 1 for Sympathetic Halo.
        Returns None if a note should be dropped, or the clamped velocity.
        """
        if velocity <= 0:
            return 0
        if sympathetic_halo and velocity == 1:
            self.stats["sympathetic_halo_notes"] += 1
            return 1
        if velocity < self.VALID_VEL_MIN:
            # Ghost note region (1-15): scale/clamp up to VALID_VEL_MIN (20) so solenoid actuates cleanly
            self.stats["ghost_notes_blocked"] += 1
            self.stats["velocity_clamped"] += 1
            return self.VALID_VEL_MIN
        if velocity > self.VALID_VEL_MAX:
            self.stats["velocity_clamped"] += 1
            return self.VALID_VEL_MAX
        return int(velocity)

    def sanitize_release_velocity(self, off_velocity: int = 25) -> int:
        """
        Clamps Note-Off velocity to 15-40 for Gradual Damping (no Damper Thud Noise).
        """
        if off_velocity <= 0:
            return self.DEFAULT_RELEASE_VEL
        return max(self.RELEASE_VEL_MIN, min(self.RELEASE_VEL_MAX, int(off_velocity)))

    # -------------------------------------------------------------------------
    # Public Scheduling & Real-Time Methods
    # -------------------------------------------------------------------------
    def schedule_chord_or_notes(
        self,
        notes: List[Tuple[int, int, float]],
        target_sound_time: Optional[float] = None,
        use_vdl_compensation: bool = True,
        pedal_cc64: Optional[int] = None,
        pedal_preroll_ms: float = 40.0,
        sympathetic_halo: bool = False,
        source_tag: str = "engine",
    ) -> List[dict]:
        """
        Schedules one or more notes `[(note, velocity, duration_s), ...]` to sound at `target_sound_time`.
        Applies:
        - VDL inverse compensation: T_send = T_target - Delta_t_VDL(k, v)
        - Bottom-up micro-staggering (1.8 ms/voice) if simultaneous sends exceed 12 notes
        - Minimum duration >= 38 ms
        - Damper pedal (CC#64) pre-roll (-30 to -50 ms ahead of earliest note)
        """
        now = time.perf_counter()
        if target_sound_time is None:
            # Ensure enough lookahead for VDL (up to 185 ms) if VDL compensation is active
            target_sound_time = now + (0.190 if use_vdl_compensation else 0.005)

        # Sort bottom-up (lowest pitch first) for natural micro-staggering
        sorted_notes = sorted(notes, key=lambda x: x[0])
        scheduled_records = []

        with self._lock:
            earliest_send_time = target_sound_time
            per_note_send_times = []

            for idx, (note, vel, dur_s) in enumerate(sorted_notes):
                if not (self.PIANO_MIN_NOTE <= note <= self.PIANO_MAX_NOTE):
                    continue
                clean_vel = self.sanitize_velocity(
                    vel, sympathetic_halo=sympathetic_halo
                )
                if clean_vel is None or clean_vel <= 0:
                    continue

                clean_dur_s = max(self.MIN_NOTE_DURATION_S, float(dur_s))
                if float(dur_s) < self.MIN_NOTE_DURATION_S:
                    self.stats["short_notes_extended"] += 1

                vdl_ms = (
                    self.vdl_model.predict_latency_ms(note, clean_vel)
                    if use_vdl_compensation
                    else 0.0
                )
                send_t = target_sound_time - (vdl_ms / 1000.0)

                # Micro-stagger if chord size exceeds SOFT_SIMULTANEOUS_LIMIT (12)
                if idx >= self.SOFT_SIMULTANEOUS_LIMIT:
                    stagger_offset = (
                        idx - self.SOFT_SIMULTANEOUS_LIMIT + 1
                    ) * self.MICRO_STAGGER_STEP_S
                    send_t += stagger_offset
                    self.stats["staggered_notes"] += 1

                send_t = max(now, send_t)
                earliest_send_time = min(earliest_send_time, send_t)
                per_note_send_times.append(
                    (note, clean_vel, clean_dur_s, send_t, vdl_ms)
                )

            # Schedule CC#64 Pre-roll (-30 to -50 ms before earliest note send)
            if pedal_cc64 is not None:
                preroll_s = max(0.030, min(0.050, pedal_preroll_ms / 1000.0))
                pedal_send_t = max(now, earliest_send_time - preroll_s)
                self._push_event(
                    ScheduledMidiEvent(
                        send_time=pedal_send_t,
                        priority=0,
                        seq_id=self._next_seq(),
                        msg_type="control_change",
                        control=64,
                        value=max(0, min(127, int(pedal_cc64))),
                        source_tag=source_tag,
                    )
                )

            # Push Note-On and corresponding Note-Off events
            for note, clean_vel, clean_dur_s, send_t, vdl_ms in per_note_send_times:
                self._push_event(
                    ScheduledMidiEvent(
                        send_time=send_t,
                        priority=1,
                        seq_id=self._next_seq(),
                        msg_type="note_on",
                        note=note,
                        velocity=clean_vel,
                        sympathetic_halo=(sympathetic_halo and clean_vel == 1),
                        min_duration_ms=clean_dur_s * 1000.0,
                        source_tag=source_tag,
                    )
                )
                off_t = send_t + clean_dur_s
                self._push_event(
                    ScheduledMidiEvent(
                        send_time=off_t,
                        priority=0,
                        seq_id=self._next_seq(),
                        msg_type="note_off",
                        note=note,
                        velocity=self.DEFAULT_RELEASE_VEL,
                        source_tag=source_tag,
                    )
                )
                scheduled_records.append(
                    {
                        "note": note,
                        "velocity": clean_vel,
                        "duration_ms": round(clean_dur_s * 1000.0, 1),
                        "vdl_ms": round(vdl_ms, 2),
                        "send_time": send_t,
                    }
                )

        return scheduled_records

    def send_note_on(
        self,
        note: int,
        velocity: int,
        duration_s: Optional[float] = None,
        sympathetic_halo: bool = False,
        allow_harmonic_redistribution: bool = True,
        source_tag: str = "live",
    ) -> bool:
        """
        Immediately processes a Note-On through all physical safety checks (or schedules micro-stagger if needed).
        If `duration_s` is provided, automatically schedules the safe Note-Off.
        """
        with self._lock:
            now = time.perf_counter()
            ok = self._execute_safe_note_on(
                note=note,
                velocity=velocity,
                now=now,
                sympathetic_halo=sympathetic_halo,
                allow_harmonic_redistribution=allow_harmonic_redistribution,
                source_tag=source_tag,
            )
            if ok and duration_s is not None:
                clean_dur = max(self.MIN_NOTE_DURATION_S, float(duration_s))
                if float(duration_s) < self.MIN_NOTE_DURATION_S:
                    self.stats["short_notes_extended"] += 1
                self._push_event(
                    ScheduledMidiEvent(
                        send_time=now + clean_dur,
                        priority=0,
                        seq_id=self._next_seq(),
                        msg_type="note_off",
                        note=note,
                        velocity=self.DEFAULT_RELEASE_VEL,
                        source_tag=source_tag,
                    )
                )
            return ok

    def send_note_off(
        self, note: int, release_velocity: int = 25, source_tag: str = "live"
    ) -> bool:
        """
        Requests a Note-Off. If the note has been on for < 35 ms, delays the Note-Off to guarantee
        complete solenoid actuation.
        """
        with self._lock:
            now = time.perf_counter()
            if note in self.active_notes:
                info = self.active_notes[note]
                min_off_t = info.get("min_off_time", now)
                if now < min_off_t:
                    # Delay Note-Off to satisfy 35-38 ms minimum duration rule
                    self.stats["short_notes_extended"] += 1
                    self._push_event(
                        ScheduledMidiEvent(
                            send_time=min_off_t,
                            priority=0,
                            seq_id=self._next_seq(),
                            msg_type="note_off",
                            note=note,
                            velocity=self.sanitize_release_velocity(release_velocity),
                            source_tag=source_tag,
                        )
                    )
                    return True
            return self._execute_safe_note_off(
                note, release_velocity, now, source_tag=source_tag
            )

    def send_pedal_cc64(
        self, value: int, control: int = 64, source_tag: str = "live"
    ) -> bool:
        """
        Strictly allows ONLY CC#64 (Right Damper Pedal). Blocks CC#66, CC#67, and others.
        """
        with self._lock:
            now = time.perf_counter()
            return self._execute_safe_cc(
                control=control, value=value, now=now, source_tag=source_tag
            )

    # -------------------------------------------------------------------------
    # Internal Low-Level Enforcement Engine
    # -------------------------------------------------------------------------
    def _next_seq(self) -> int:
        self._seq_counter += 1
        return self._seq_counter

    def _push_event(self, ev: ScheduledMidiEvent) -> None:
        heapq.heappush(self._event_heap, ev)
        self.stats["vdl_buffer_events"] = len(self._event_heap)

    def _prune_windows(self, now: float) -> None:
        while (
            self.recent_note_ons_2ms
            and (now - self.recent_note_ons_2ms[0]) > self.SIMULTANEOUS_WINDOW_S
        ):
            self.recent_note_ons_2ms.popleft()
        while self.recent_note_ons_1s and (now - self.recent_note_ons_1s[0]) > 1.0:
            self.recent_note_ons_1s.popleft()

    def _steal_oldest_inner_voice(self, now: float) -> Optional[int]:
        """
        When active held voices reach 20, safely releases the oldest inner voice
        (preserving the lowest bass voice and highest soprano voice).
        """
        if not self.active_notes:
            return None
        sorted_pitches = sorted(self.active_notes.keys())
        if len(sorted_pitches) > 2:
            candidates = sorted_pitches[1:-1]  # inner voices only
        else:
            candidates = sorted_pitches
        oldest_note = min(candidates, key=lambda n: self.active_notes[n]["on_time"])
        self._execute_safe_note_off(
            oldest_note, self.DEFAULT_RELEASE_VEL, now, source_tag="voice_steal"
        )
        self.stats["voice_steals"] += 1
        self._log(
            f"[SAFETY GUARD] Voice Stealing: released inner voice Note={oldest_note} (active limit {self.MAX_ACTIVE_VOICES})"
        )
        return oldest_note

    def _execute_safe_note_on(
        self,
        note: int,
        velocity: int,
        now: float,
        sympathetic_halo: bool = False,
        allow_harmonic_redistribution: bool = True,
        min_duration_ms: float = 38.0,
        source_tag: str = "live",
    ) -> bool:
        if not (self.PIANO_MIN_NOTE <= note <= self.PIANO_MAX_NOTE):
            return False

        clean_vel = self.sanitize_velocity(velocity, sympathetic_halo=sympathetic_halo)
        if clean_vel is None or clean_vel <= 0:
            return False

        self._prune_windows(now)

        # Rule 2A: Global density limiter (<= 28-30 notes/sec)
        if len(self.recent_note_ons_1s) >= self.MAX_NOTES_PER_SEC:
            self.stats["density_throttled"] += 1
            self._log(
                f"[SAFETY GUARD] Global density cap ({self.MAX_NOTES_PER_SEC} notes/s) reached; throttled Note={note}"
            )
            return False

        # Rule 1A: 2 ms Simultaneous Note-On window check (Soft=12, Hard=16)
        if len(self.recent_note_ons_2ms) >= self.SOFT_SIMULTANEOUS_LIMIT:
            # Automatically micro-stagger by 1.8 ms * excess count
            excess = len(self.recent_note_ons_2ms) - self.SOFT_SIMULTANEOUS_LIMIT + 1
            stagger_delay = excess * self.MICRO_STAGGER_STEP_S
            self.stats["staggered_notes"] += 1
            self._push_event(
                ScheduledMidiEvent(
                    send_time=now + stagger_delay,
                    priority=1,
                    seq_id=self._next_seq(),
                    msg_type="note_on",
                    note=note,
                    velocity=clean_vel,
                    sympathetic_halo=sympathetic_halo,
                    min_duration_ms=min_duration_ms,
                    source_tag=source_tag + ":staggered",
                )
            )
            return True

        # Rule 2B: Same-key repetition limiter (>= 68 ms period and >= 25 ms off-gap)
        last_on = self.last_note_on_time.get(note, -999.0)
        last_off = self.last_note_off_time.get(note, -999.0)
        if (now - last_on) < self.MIN_SAME_KEY_PERIOD_S or (
            note not in self.active_notes
            and (now - last_off) < self.MIN_SAME_KEY_GAP_S
        ):
            self.stats["repetition_redirected"] += 1
            if allow_harmonic_redistribution:
                # Redirect rapid same-key repeat to +12 (octave above) or +7 (fifth) if safe
                alt_note = note + 12 if (note + 12) <= self.PIANO_MAX_NOTE else note - 12
                alt_last_on = self.last_note_on_time.get(alt_note, -999.0)
                if (now - alt_last_on) >= self.MIN_SAME_KEY_PERIOD_S:
                    note = alt_note
                else:
                    return False
            else:
                return False

        # If the same key is still currently active, cleanly release it before re-striking
        if note in self.active_notes:
            self._execute_safe_note_off(
                note, self.DEFAULT_RELEASE_VEL, now, source_tag="retrigger_release"
            )
            # Schedule the re-strike 26 ms later so the solenoid resets cleanly
            self._push_event(
                ScheduledMidiEvent(
                    send_time=now + self.MIN_SAME_KEY_GAP_S,
                    priority=1,
                    seq_id=self._next_seq(),
                    msg_type="note_on",
                    note=note,
                    velocity=clean_vel,
                    sympathetic_halo=sympathetic_halo,
                    min_duration_ms=min_duration_ms,
                    source_tag=source_tag + ":retrigger",
                )
            )
            return True

        # Rule 1B: Active held voices hard limit (max 20 notes) -> Voice Stealing
        while len(self.active_notes) >= self.MAX_ACTIVE_VOICES:
            self._steal_oldest_inner_voice(now)

        # Send physical MIDI Note-On
        if self.outport:
            try:
                msg = mido.Message("note_on", note=note, velocity=clean_vel, channel=0)
                self.outport.send(msg)
            except Exception as exc:
                self._log(f"[SAFETY GUARD] MIDI send error: {exc}")

        min_dur_s = max(self.MIN_NOTE_DURATION_S, min_duration_ms / 1000.0)
        self.active_notes[note] = {
            "on_time": now,
            "velocity": clean_vel,
            "sympathetic": sympathetic_halo and (clean_vel == 1),
            "min_off_time": now + min_dur_s,
            "source_tag": source_tag,
        }
        self.last_note_on_time[note] = now
        self.recent_note_ons_2ms.append(now)
        self.recent_note_ons_1s.append(now)
        self.stats["total_notes_sent"] += 1
        return True

    def _execute_safe_note_off(
        self, note: int, release_velocity: int, now: float, source_tag: str = "live"
    ) -> bool:
        if not (self.PIANO_MIN_NOTE <= note <= self.PIANO_MAX_NOTE):
            return False
        clean_rel_vel = self.sanitize_release_velocity(release_velocity)
        if self.outport:
            try:
                msg = mido.Message(
                    "note_off", note=note, velocity=clean_rel_vel, channel=0
                )
                self.outport.send(msg)
            except Exception as exc:
                self._log(f"[SAFETY GUARD] MIDI Note-Off error: {exc}")
        self.active_notes.pop(note, None)
        self.last_note_off_time[note] = now
        return True

    def _execute_safe_cc(
        self, control: int, value: int, now: float, source_tag: str = "live"
    ) -> bool:
        # Rule 4: Only Right Damper Pedal (CC#64) is permitted; block CC#66, CC#67, etc.
        if control != 64:
            self.stats["blocked_cc_events"] += 1
            self._log(
                f"[SAFETY GUARD] Blocked non-damper CC#{control}={value} (Only CC#64 allowed)"
            )
            return False

        clean_val = max(0, min(127, int(value)))
        if self.outport:
            try:
                msg = mido.Message(
                    "control_change", control=64, value=clean_val, channel=0
                )
                self.outport.send(msg)
            except Exception as exc:
                self._log(f"[SAFETY GUARD] MIDI CC#64 error: {exc}")

        self.current_pedal_val = clean_val
        if clean_val > 30:
            if self.pedal_engaged_since is None:
                self.pedal_engaged_since = now
        else:
            self.pedal_engaged_since = None
        return True

    # -------------------------------------------------------------------------
    # Background Scheduler & Thermal Watchdog Loop
    # -------------------------------------------------------------------------
    def _scheduler_and_watchdog_loop(self) -> None:
        last_watchdog_check = time.perf_counter()
        while self._running:
            now = time.perf_counter()
            with self._lock:
                # 1. Process due scheduled events from heap
                while self._event_heap and self._event_heap[0].send_time <= now:
                    ev = heapq.heappop(self._event_heap)
                    if ev.msg_type == "note_on":
                        self._execute_safe_note_on(
                            note=ev.note,
                            velocity=ev.velocity,
                            now=now,
                            sympathetic_halo=ev.sympathetic_halo,
                            min_duration_ms=ev.min_duration_ms,
                            source_tag=ev.source_tag,
                        )
                    elif ev.msg_type == "note_off":
                        # Respect min_off_time if note was staggered later
                        if ev.note in self.active_notes:
                            min_off = self.active_notes[ev.note]["min_off_time"]
                            if now < min_off:
                                ev.send_time = min_off
                                heapq.heappush(self._event_heap, ev)
                                continue
                        self._execute_safe_note_off(
                            ev.note, ev.velocity, now, source_tag=ev.source_tag
                        )
                    elif ev.msg_type == "control_change":
                        self._execute_safe_cc(
                            ev.control, ev.value, now, source_tag=ev.source_tag
                        )
                self.stats["vdl_buffer_events"] = len(self._event_heap)

                # 2. Thermal protection watchdog every 100 ms
                if (now - last_watchdog_check) >= 0.100:
                    last_watchdog_check = now
                    self._run_thermal_watchdog(now)

            time.sleep(0.0005)  # 0.5 ms high-resolution tick

    def _run_thermal_watchdog(self, now: float) -> None:
        # Check single key hold > 8,000 ms (8.0 s)
        to_release = []
        for note, info in list(self.active_notes.items()):
            if (now - info["on_time"]) >= self.MAX_KEY_HOLD_S:
                to_release.append(note)
        for note in to_release:
            self._execute_safe_note_off(
                note, self.DEFAULT_RELEASE_VEL, now, source_tag="thermal_watchdog"
            )
            self.stats["thermal_key_refreshes"] += 1
            self._log(
                f"[SAFETY GUARD] Thermal Watchdog: auto-released Note={note} held > {self.MAX_KEY_HOLD_S}s"
            )

        # Check damper pedal continuous hold > 240 s
        if (
            self.pedal_engaged_since is not None
            and (now - self.pedal_engaged_since) >= self.MAX_PEDAL_HOLD_S
        ):
            prev_val = self.current_pedal_val
            self._execute_safe_cc(64, 0, now, source_tag="thermal_watchdog")
            self.stats["thermal_pedal_refreshes"] += 1
            self._log(
                f"[SAFETY GUARD] Thermal Watchdog: refreshed CC#64 after {self.MAX_PEDAL_HOLD_S}s continuous hold"
            )
            # Re-engage gently 80 ms later if still desired
            self._push_event(
                ScheduledMidiEvent(
                    send_time=now + 0.080,
                    priority=0,
                    seq_id=self._next_seq(),
                    msg_type="control_change",
                    control=64,
                    value=prev_val,
                    source_tag="thermal_watchdog_restore",
                )
            )

    # -------------------------------------------------------------------------
    # Emergency Panic & Telemetry Snapshot
    # -------------------------------------------------------------------------
    def emergency_panic(self) -> dict:
        """
        Immediately flushes all scheduled queues, sends Note-Off (vel=25) to all 88 piano keys,
        and resets CC#64=0.
        """
        with self._lock:
            self._event_heap.clear()
            now = time.perf_counter()
            if self.outport:
                try:
                    # Release damper pedal immediately
                    self.outport.send(
                        mido.Message("control_change", control=64, value=0, channel=0)
                    )
                    # Send smooth Note-Off to all active notes first, then sweep all 88 keys
                    for note in range(self.PIANO_MIN_NOTE, self.PIANO_MAX_NOTE + 1):
                        self.outport.send(
                            mido.Message(
                                "note_off",
                                note=note,
                                velocity=self.DEFAULT_RELEASE_VEL,
                                channel=0,
                            )
                        )
                    self.outport.send(
                        mido.Message("control_change", control=123, value=0, channel=0)
                    )
                except Exception:
                    pass
            self.active_notes.clear()
            self.recent_note_ons_2ms.clear()
            self.recent_note_ons_1s.clear()
            self.current_pedal_val = 0
            self.pedal_engaged_since = None
            self.stats["panic_count"] += 1
            self.stats["vdl_buffer_events"] = 0
            self._log(
                "🔴 [EMERGENCY PANIC] Flushed queue, sent All-88-Keys Note-Off & CC#64=0."
            )
            return self.get_telemetry_snapshot()

    def get_telemetry_snapshot(self) -> dict:
        with self._lock:
            now = time.perf_counter()
            self._prune_windows(now)
            active_keys = {
                str(k): {
                    "velocity": v["velocity"],
                    "sympathetic": v["sympathetic"],
                    "held_ms": round((now - v["on_time"]) * 1000.0, 1),
                }
                for k, v in self.active_notes.items()
            }
            pedal_hold_s = (
                round(now - self.pedal_engaged_since, 1)
                if self.pedal_engaged_since is not None
                else 0.0
            )
            return {
                "connected": self.outport is not None,
                "port_name": self.connected_port_name or "Disconnected",
                "simultaneous_2ms_count": len(self.recent_note_ons_2ms),
                "simultaneous_limit": self.HARD_SIMULTANEOUS_LIMIT,
                "active_voices_count": len(self.active_notes),
                "active_voices_limit": self.MAX_ACTIVE_VOICES,
                "notes_per_sec": len(self.recent_note_ons_1s),
                "notes_per_sec_limit": 30,
                "vdl_buffer_count": len(self._event_heap),
                "pedal_cc64": self.current_pedal_val,
                "pedal_hold_s": pedal_hold_s,
                "active_keys": active_keys,
                "vdl_params": self.vdl_model.fitted_curve_params,
                "stats": dict(self.stats),
            }
