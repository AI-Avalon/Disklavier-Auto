"""
Layer 5: Physical Disklavier Safety Output & Lookahead Scheduler Layer.

Enforces non-negotiable hardware protection:
1. Hard 30-Voice Polyphony Cap & Voice Stealing (never exceeds 30 active voices even under 200 notes/s bursts).
2. Solenoid Thermal Protection & Velocity Clamp ([18, 112], min same-key interval 60ms, 10s rolling energy budget).
3. Continuous Pedal (CC#64) Thinning Filter (min 40ms interval, +/-3 deadband; passes 0 and 127 boundaries).
4. Note TTL Watchdog (10.0s max hold) & Immediate Emergency Panic (< 10ms all-notes-off + CC#123=0, CC#120=0, CC#64=0).
5. Velocity/Pitch VDL Physical Strike Lag Lookahead offset_ms(pitch, velocity) + L1 Echo Ledger notification.
"""

from __future__ import annotations

import ctypes
from collections import deque
from dataclasses import dataclass, field
import heapq
import math
import sys
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

try:
    import mido
except Exception:  # pragma: no cover
    mido = None  # type: ignore[assignment]


@dataclass
class OutputTelemetry:
    active_voices: int
    max_active_observed: int
    hard_limit: int
    voice_steals: int
    velocity_clamps: int
    thermal_throttles: int
    pedal_thinned: int
    ttl_expired: int
    panic_count: int
    last_panic_elapsed_ms: float
    pedal_cc64: int
    rolling_10s_energy: float
    energy_limit_10s: float
    total_notes_sent: int
    connected_port: Optional[str]


@dataclass(order=True)
class _QueuedOutputEvent:
    send_ts: float
    priority: int
    seq: int
    kind: str  # "note_on", "note_off", "cc64"
    pitch: int = field(compare=False, default=60)
    velocity: int = field(compare=False, default=64)
    channel: int = field(compare=False, default=0)
    cc_value: int = field(compare=False, default=0)
    duration_s: float = field(compare=False, default=0.3)
    token_id: int = field(compare=False, default=0)


class DisklavierStageOutputLayer:
    """
    Independent high-precision Disklavier output safety filter & lookahead scheduler.
    All outgoing MIDI passes through this layer.
    """

    HARD_POLYPHONY_LIMIT = 30
    PIANO_MIN_MIDI = 21
    PIANO_MAX_MIDI = 108
    VEL_MIN = 18
    VEL_MAX = 112
    MIN_SAME_KEY_RETRIGGER_S = 0.060  # 60 ms
    PEDAL_MIN_INTERVAL_S = 0.040      # 40 ms
    PEDAL_DEADBAND = 3                # +/-3 deadband
    NOTE_TTL_S = 10.0                 # 10 s max hold watchdog
    ENERGY_WINDOW_S = 10.0
    MAX_10S_ENERGY = 28000.0          # ~280 notes at vel 100 in 10s

    def __init__(
        self,
        port_name: Optional[str] = None,
        midi_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
        echo_register_cb: Optional[Callable[[int, float], None]] = None,
        start_worker: bool = True,
    ) -> None:
        self._lock = threading.RLock()
        self._cv = threading.Condition(self._lock)
        self._midi_sink = midi_sink
        self._echo_register_cb = echo_register_cb
        self.outport: Any = None
        self.connected_port_name: Optional[str] = None

        # Active sounding notes tracked by pitch -> dict(on_ts, velocity, channel, token_id)
        self.active_notes: Dict[int, Dict[str, Any]] = {}
        self.key_tokens: Dict[int, int] = {}
        self.last_key_on_ts: Dict[int, float] = {}
        self.energy_history: Deque[Tuple[float, float]] = deque()
        self.rolling_energy: float = 0.0

        # Pedal thinning state
        self.current_pedal_val: int = 0
        self.last_pedal_sent_ts: float = 0.0

        # Telemetry & safety counters
        self.max_active_observed: int = 0
        self.voice_steals: int = 0
        self.velocity_clamps: int = 0
        self.thermal_throttles: int = 0
        self.pedal_thinned: int = 0
        self.ttl_expired: int = 0
        self.panic_count: int = 0
        self.last_panic_elapsed_ms: float = 0.0
        self.total_notes_sent: int = 0
        self.sent_events_log: Deque[Dict[str, Any]] = deque(maxlen=256)

        # Lookahead queue
        self._heap: List[_QueuedOutputEvent] = []
        self._seq: int = 0
        self._running: bool = True
        self._win_timer_enabled = False
        if sys.platform == "win32":
            try:
                ctypes.windll.winmm.timeBeginPeriod(1)
                self._win_timer_enabled = True
            except Exception:
                pass

        if port_name:
            self._open_midi_port(port_name)

        self._worker: Optional[threading.Thread] = None
        if start_worker:
            self._worker = threading.Thread(
                target=self._scheduler_loop,
                name="StageLayer5OutputScheduler",
                daemon=True,
            )
            self._worker.start()

    def _open_midi_port(self, preferred: str) -> None:
        if mido is None:
            return
        try:
            outs = mido.get_output_names()
            for kw in (preferred.lower(), "disklavier", "yamaha", "usb-midi"):
                match = next((o for o in outs if kw in o.lower()), None)
                if match:
                    self.outport = mido.open_output(match)
                    self.connected_port_name = match
                    return
        except Exception:
            self.outport = None

    def set_echo_register_callback(self, cb: Optional[Callable[[int, float], None]]) -> None:
        with self._lock:
            self._echo_register_cb = cb

    def set_midi_sink(self, sink: Optional[Callable[[Dict[str, Any]], None]]) -> None:
        with self._lock:
            self._midi_sink = sink

    @staticmethod
    def offset_ms(pitch: int, velocity: int) -> float:
        """
        Physical strike lag lookahead offset (ms) for Yamaha Disklavier (Delay In = OFF).
        Softer notes and heavier bass hammers have longer mechanical travel time.
        """
        v = max(18, min(112, int(velocity)))
        p = max(21, min(108, int(pitch)))
        base_min = 24.0
        amp = 108.0
        decay = 0.032
        bass_offset = max(0.0, (60 - p) * 0.14)
        lag = base_min + amp * math.exp(-decay * (v - 18.0)) + bass_offset
        return round(max(18.0, min(160.0, lag)), 2)

    def _emit_raw(self, payload: Dict[str, Any]) -> None:
        self.sent_events_log.append(payload)
        if self.outport is not None and mido is not None:
            try:
                mtype = payload["type"]
                if mtype == "note_on":
                    self.outport.send(
                        mido.Message(
                            "note_on",
                            note=int(payload["pitch"]),
                            velocity=int(payload["velocity"]),
                            channel=int(payload.get("channel", 0)),
                        )
                    )
                elif mtype == "note_off":
                    self.outport.send(
                        mido.Message(
                            "note_off",
                            note=int(payload["pitch"]),
                            velocity=int(payload.get("velocity", 24)),
                            channel=int(payload.get("channel", 0)),
                        )
                    )
                elif mtype == "control_change":
                    self.outport.send(
                        mido.Message(
                            "control_change",
                            control=int(payload["control"]),
                            value=int(payload["value"]),
                            channel=int(payload.get("channel", 0)),
                        )
                    )
            except Exception:
                pass
        if self._midi_sink is not None:
            try:
                self._midi_sink(payload)
            except Exception:
                pass

    def _prune_energy(self, now: float) -> None:
        cutoff = now - self.ENERGY_WINDOW_S
        while self.energy_history and self.energy_history[0][0] < cutoff:
            _, e = self.energy_history.popleft()
            self.rolling_energy = max(0.0, self.rolling_energy - e)

    def _clamp_velocity(self, velocity: int) -> int:
        v = int(velocity)
        if v < self.VEL_MIN:
            self.velocity_clamps += 1
            return self.VEL_MIN
        if v > self.VEL_MAX:
            self.velocity_clamps += 1
            return self.VEL_MAX
        return v

    def _steal_one_voice(self, now: float) -> None:
        if not self.active_notes:
            return
        # Steal oldest / lowest-velocity active voice
        victim_pitch, victim_info = min(
            self.active_notes.items(),
            key=lambda kv: (kv[1]["on_ts"], kv[1]["velocity"]),
        )
        self.voice_steals += 1
        self._send_note_off_locked(victim_pitch, now, channel=int(victim_info.get("channel", 0)))

    def _send_note_off_locked(self, pitch: int, now: float, channel: int = 0) -> None:
        self.active_notes.pop(pitch, None)
        self._emit_raw(
            {
                "type": "note_off",
                "pitch": pitch,
                "velocity": 24,
                "channel": channel,
                "ts": now,
            }
        )

    def send_note_on_immediate(
        self,
        pitch: int,
        velocity: int,
        duration_s: float = 0.35,
        channel: int = 0,
        now: Optional[float] = None,
    ) -> bool:
        """
        Immediately filter and dispatch a Note-On through all physical protection gates.
        Guarantees active polyphony NEVER exceeds HARD_POLYPHONY_LIMIT (30).
        """
        ts = now if now is not None else time.perf_counter()
        p = max(self.PIANO_MIN_MIDI, min(self.PIANO_MAX_MIDI, int(pitch)))
        with self._lock:
            self._prune_energy(ts)

            # 1. Same-key solenoid thermal protection (min 60ms retrigger)
            last_t = self.last_key_on_ts.get(p)
            if last_t is not None and (ts - last_t) < self.MIN_SAME_KEY_RETRIGGER_S:
                self.thermal_throttles += 1
                return False

            # 2. 10s rolling window energy cap
            v = self._clamp_velocity(velocity)
            if self.rolling_energy + v > self.MAX_10S_ENERGY:
                self.thermal_throttles += 1
                return False

            # 3. If pitch is already active, send note_off first before re-striking
            if p in self.active_notes:
                self._send_note_off_locked(p, ts, channel=channel)

            # 4. Enforce hard 30-voice polyphony cap with voice stealing BEFORE Note-On
            while len(self.active_notes) >= self.HARD_POLYPHONY_LIMIT:
                self._steal_one_voice(ts)

            # 5. Register active voice & energy
            tok = self.key_tokens.get(p, 0) + 1
            self.key_tokens[p] = tok
            self.active_notes[p] = {
                "on_ts": ts,
                "velocity": v,
                "channel": channel,
                "token_id": tok,
                "ttl_deadline": ts + self.NOTE_TTL_S,
            }
            self.last_key_on_ts[p] = ts
            self.energy_history.append((ts, float(v)))
            self.rolling_energy += float(v)
            self.total_notes_sent += 1
            if len(self.active_notes) > self.max_active_observed:
                self.max_active_observed = len(self.active_notes)

            # Notify L1 Echo Suppression Ledger BEFORE/AS Note-On leaves
            if self._echo_register_cb is not None:
                try:
                    self._echo_register_cb(p, ts)
                except Exception:
                    pass

            self._emit_raw(
                {
                    "type": "note_on",
                    "pitch": p,
                    "velocity": v,
                    "channel": channel,
                    "ts": ts,
                }
            )

            # Schedule automatic Note-Off after duration_s (capped by NOTE_TTL_S)
            safe_dur = max(0.035, min(self.NOTE_TTL_S - 0.05, float(duration_s)))
            self._seq += 1
            heapq.heappush(
                self._heap,
                _QueuedOutputEvent(
                    send_ts=ts + safe_dur,
                    priority=0,
                    seq=self._seq,
                    kind="note_off",
                    pitch=p,
                    channel=channel,
                    token_id=tok,
                ),
            )
            self._cv.notify_all()
            return True

    def send_note_off_immediate(
        self,
        pitch: int,
        channel: int = 0,
        token_id: Optional[int] = None,
        now: Optional[float] = None,
    ) -> bool:
        ts = now if now is not None else time.perf_counter()
        p = max(self.PIANO_MIN_MIDI, min(self.PIANO_MAX_MIDI, int(pitch)))
        with self._lock:
            cur = self.active_notes.get(p)
            if cur is None:
                return False
            if token_id is not None and cur.get("token_id") != token_id:
                return False
            self._send_note_off_locked(p, ts, channel=channel)
            return True

    def send_pedal_cc64(self, value: int, channel: int = 0, now: Optional[float] = None) -> bool:
        """
        Continuous pedal thinning filter:
        - Minimum 40ms send interval (unless full release to 0)
        - Deadband +/-3 (unless boundary values 0 or 127)
        """
        ts = now if now is not None else time.perf_counter()
        val = max(0, min(127, int(value)))
        with self._lock:
            is_boundary = val in (0, 127) and val != self.current_pedal_val
            dt = ts - self.last_pedal_sent_ts
            diff = abs(val - self.current_pedal_val)

            if not is_boundary:
                if dt < self.PEDAL_MIN_INTERVAL_S or diff < self.PEDAL_DEADBAND:
                    self.pedal_thinned += 1
                    return False

            self.current_pedal_val = val
            self.last_pedal_sent_ts = ts
            self._emit_raw(
                {
                    "type": "control_change",
                    "control": 64,
                    "value": val,
                    "channel": channel,
                    "ts": ts,
                }
            )
            return True

    def schedule_phrase(
        self,
        events: List[Dict[str, Any]],
        base_sound_ts: Optional[float] = None,
        use_vdl_lookahead: bool = True,
    ) -> int:
        """
        Schedule a list of note/CC events with physical strike lag lookahead offset_ms(pitch, velocity).
        """
        base = base_sound_ts if base_sound_ts is not None else (time.perf_counter() + 0.16)
        count = 0
        with self._lock:
            for ev in events:
                pitch = int(ev.get("pitch", ev.get("note", 0)) or 0)
                t_off = float(ev.get("time_offset_s", ev.get("onset_s", 0.0)))
                if pitch <= 0:
                    if "pedal_cc64" in ev or ev.get("control") == 64:
                        val = int(ev.get("pedal_cc64", ev.get("val", ev.get("value", 0))))
                        self._seq += 1
                        heapq.heappush(
                            self._heap,
                            _QueuedOutputEvent(
                                send_ts=max(time.perf_counter(), base + t_off - 0.030),
                                priority=2,
                                seq=self._seq,
                                kind="cc64",
                                cc_value=val,
                            ),
                        )
                        count += 1
                    continue

                vel = max(self.VEL_MIN, min(self.VEL_MAX, int(ev.get("velocity", 64))))
                dur = max(0.035, float(ev.get("duration_s", 0.35)))
                lag_s = (self.offset_ms(pitch, vel) / 1000.0) if use_vdl_lookahead else 0.0
                target_sound = base + t_off
                send_t = max(time.perf_counter(), target_sound - lag_s)

                self._seq += 1
                heapq.heappush(
                    self._heap,
                    _QueuedOutputEvent(
                        send_ts=send_t,
                        priority=1,
                        seq=self._seq,
                        kind="note_on",
                        pitch=pitch,
                        velocity=vel,
                        duration_s=dur,
                    ),
                )
                count += 1
            self._cv.notify_all()
        return count

    def step_ttl_watchdog(self, now: Optional[float] = None) -> int:
        ts = now if now is not None else time.perf_counter()
        expired = 0
        with self._lock:
            for p, info in list(self.active_notes.items()):
                if ts >= float(info.get("ttl_deadline", ts + 1.0)):
                    self._send_note_off_locked(p, ts, channel=int(info.get("channel", 0)))
                    self.ttl_expired += 1
                    expired += 1
        return expired

    def panic(self) -> Dict[str, Any]:
        """
        Emergency Panic: clears queue, sends Note-Off for all active notes + CC#123=0,
        CC#120=0, CC#64=0 across all channels in < 10 ms.
        """
        t_start = time.perf_counter()
        with self._lock:
            self._heap.clear()
            active_copy = list(self.active_notes.items())
            self.active_notes.clear()
            self.current_pedal_val = 0

            # Note-off all active notes immediately
            for p, info in active_copy:
                self._emit_raw(
                    {
                        "type": "note_off",
                        "pitch": p,
                        "velocity": 24,
                        "channel": int(info.get("channel", 0)),
                        "ts": t_start,
                    }
                )

            # Send CC#64=0, CC#123=0 (All Notes Off), CC#120=0 (All Sound Off) on channel 0..15 if hardware connected, else channel 0
            channels = range(16) if self.outport is not None else (0,)
            for ch in channels:
                for cc in (64, 123, 120):
                    self._emit_raw(
                        {
                            "type": "control_change",
                            "control": cc,
                            "value": 0,
                            "channel": ch,
                            "ts": t_start,
                        }
                    )

            elapsed_ms = (time.perf_counter() - t_start) * 1000.0
            self.panic_count += 1
            self.last_panic_elapsed_ms = round(elapsed_ms, 3)
            self._cv.notify_all()
            return {
                "ok": True,
                "active_voices": len(self.active_notes),
                "elapsed_ms": self.last_panic_elapsed_ms,
                "panic_count": self.panic_count,
            }

    def _scheduler_loop(self) -> None:
        last_ttl = time.perf_counter()
        while self._running:
            with self._lock:
                now = time.perf_counter()
                while self._heap and self._heap[0].send_ts <= now:
                    ev = heapq.heappop(self._heap)
                    if ev.kind == "note_on":
                        self.send_note_on_immediate(
                            ev.pitch, ev.velocity, ev.duration_s, ev.channel, now=now
                        )
                    elif ev.kind == "note_off":
                        self.send_note_off_immediate(
                            ev.pitch, ev.channel, token_id=ev.token_id, now=now
                        )
                    elif ev.kind == "cc64":
                        self.send_pedal_cc64(ev.cc_value, ev.channel, now=now)
                    now = time.perf_counter()

                if now - last_ttl >= 0.1:
                    last_ttl = now
                    self.step_ttl_watchdog(now)

                if not self._running:
                    break
                if self._heap:
                    rem = self._heap[0].send_ts - time.perf_counter()
                    if rem > 0.004:
                        self._cv.wait(timeout=min(0.050, rem - 0.003))
                    elif rem > 0.0:
                        time.sleep(0)
                else:
                    self._cv.wait(timeout=0.050)

    def get_telemetry(self) -> OutputTelemetry:
        with self._lock:
            now = time.perf_counter()
            self._prune_energy(now)
            return OutputTelemetry(
                active_voices=len(self.active_notes),
                max_active_observed=self.max_active_observed,
                hard_limit=self.HARD_POLYPHONY_LIMIT,
                voice_steals=self.voice_steals,
                velocity_clamps=self.velocity_clamps,
                thermal_throttles=self.thermal_throttles,
                pedal_thinned=self.pedal_thinned,
                ttl_expired=self.ttl_expired,
                panic_count=self.panic_count,
                last_panic_elapsed_ms=self.last_panic_elapsed_ms,
                pedal_cc64=self.current_pedal_val,
                rolling_10s_energy=round(self.rolling_energy, 1),
                energy_limit_10s=self.MAX_10S_ENERGY,
                total_notes_sent=self.total_notes_sent,
                connected_port=self.connected_port_name,
            )

    def close(self) -> None:
        with self._lock:
            self._running = False
            self._cv.notify_all()
        if self._worker is not None and self._worker.is_alive():
            self._worker.join(timeout=0.5)
        try:
            self.panic()
        except Exception:
            pass
        with self._lock:
            if self.outport is not None:
                try:
                    self.outport.close()
                except Exception:
                    pass
                self.outport = None
            if self._win_timer_enabled:
                try:
                    ctypes.windll.winmm.timeEndPeriod(1)
                except Exception:
                    pass
                self._win_timer_enabled = False
