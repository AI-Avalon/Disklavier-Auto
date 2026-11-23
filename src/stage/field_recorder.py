"""
Field Blackbox Session Recorder & Offline Replayer (`src/stage/field_recorder.py`).

Synchronously captures all live streams during venue field tests into
`recordings/session_YYYYMMDD_HHMMSS/`:
  1. Disklavier MIDI IN / OUT (`midi_events.jsonl` + `disklavier_io.mid` Type-1 SMF)
  2. UR22mkII 2ch Audio (`ur22_audio.wav` 16-bit stereo PCM: ch0=Cello mic, ch1=Piano ref)
  3. 6-Stage Logic Trace (`logic_trace.jsonl`)
  4. Hybrid Network RTT & Failover Log (`network_latency.jsonl`)
  5. Synchronized Manifest (`session_manifest.json`)

Includes `FieldSessionReplayer` for hardware-free post-concert offline analysis.
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional
import wave

import numpy as np

try:
    import mido
except Exception:  # pragma: no cover
    mido = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECORDINGS_DIR = REPO_ROOT / "recordings"


class FieldSessionRecorder:
    """Thread-safe synchronized blackbox recorder for live stage and field verification."""

    def __init__(self, recordings_root: Optional[Path] = None, sample_rate: int = 44100) -> None:
        self.recordings_root = Path(recordings_root or DEFAULT_RECORDINGS_DIR)
        self.sample_rate = int(sample_rate)
        self._lock = threading.Lock()
        self.active: bool = False
        self.session_dir: Optional[Path] = None
        self.session_id: str = ""
        self.label: str = ""
        self._start_perf: float = 0.0
        self._start_iso: str = ""

        self._midi_fh: Any = None
        self._trace_fh: Any = None
        self._net_fh: Any = None
        self._wav_writer: Any = None

        self._midi_in_events: List[Dict[str, Any]] = []
        self._midi_out_events: List[Dict[str, Any]] = []
        self.midi_in_count: int = 0
        self.midi_out_count: int = 0
        self.audio_frames: int = 0
        self.trace_count: int = 0
        self.network_events_count: int = 0
        self.failover_events_count: int = 0
        self._last_manifest: Optional[Dict[str, Any]] = None

    def start(self, label: str = "field_test", recordings_root: Optional[Path] = None) -> Dict[str, Any]:
        with self._lock:
            if self.active and self.session_dir is not None:
                return self._status_locked()
            root = Path(recordings_root) if recordings_root is not None else self.recordings_root
            root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            cand_dir = root / f"session_{stamp}"
            idx = 1
            while cand_dir.exists():
                idx += 1
                cand_dir = root / f"session_{stamp}_{idx}"
            cand_dir.mkdir(parents=True, exist_ok=True)

            self.session_dir = cand_dir
            self.session_id = cand_dir.name
            self.label = str(label or "field_test")
            self._start_perf = time.perf_counter()
            self._start_iso = datetime.now().isoformat(timespec="seconds")

            self._midi_fh = (cand_dir / "midi_events.jsonl").open("a", encoding="utf-8")
            self._trace_fh = (cand_dir / "logic_trace.jsonl").open("a", encoding="utf-8")
            self._net_fh = (cand_dir / "network_latency.jsonl").open("a", encoding="utf-8")

            wf = wave.open(str(cand_dir / "ur22_audio.wav"), "wb")
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            self._wav_writer = wf

            self._midi_in_events.clear()
            self._midi_out_events.clear()
            self.midi_in_count = 0
            self.midi_out_count = 0
            self.audio_frames = 0
            self.trace_count = 0
            self.network_events_count = 0
            self.failover_events_count = 0
            self._last_manifest = None
            self.active = True
            return self._status_locked()

    def _rel_s(self) -> float:
        return round(max(0.0, time.perf_counter() - self._start_perf), 4)

    def record_midi(self, direction: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            if not self.active:
                return
            rel_s = self._rel_s()
            rec = {
                "rel_s": rel_s,
                "direction": "in" if str(direction).lower() == "in" else "out",
                "type": str(payload.get("type", "note_on")),
                "pitch": int(payload.get("pitch", payload.get("note", 0)) or 0),
                "velocity": int(payload.get("velocity", 0) or 0),
                "control": int(payload.get("control", 0) or 0),
                "value": int(payload.get("value", 0) or 0),
                "channel": int(payload.get("channel", 0) or 0),
            }
            if rec["direction"] == "in":
                self.midi_in_count += 1
                self._midi_in_events.append(rec)
            else:
                self.midi_out_count += 1
                self._midi_out_events.append(rec)
            if self._midi_fh is not None:
                try:
                    self._midi_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    self._midi_fh.flush()
                except Exception:
                    pass

    def record_audio_block(
        self,
        mic_ch: np.ndarray,
        ref_ch: Optional[np.ndarray] = None,
        sample_rate: Optional[int] = None,
    ) -> None:
        with self._lock:
            if not self.active or self._wav_writer is None:
                return
            try:
                m = np.asarray(mic_ch, dtype=np.float32).reshape(-1)
                if m.size == 0:
                    return
                if ref_ch is not None:
                    r = np.asarray(ref_ch, dtype=np.float32).reshape(-1)
                    if r.size < m.size:
                        r = np.pad(r, (0, m.size - r.size))
                    elif r.size > m.size:
                        r = r[: m.size]
                else:
                    r = np.zeros_like(m)
                stereo = np.stack([np.clip(m, -1.0, 1.0), np.clip(r, -1.0, 1.0)], axis=1)
                pcm = (stereo * 32767.0).astype("<i2")
                self._wav_writer.writeframes(pcm.tobytes())
                self.audio_frames += int(m.size)
            except Exception:
                pass

    def record_logic_trace(self, trace: Dict[str, Any]) -> None:
        with self._lock:
            if not self.active:
                return
            rec = {"rel_s": self._rel_s(), **trace}
            self.trace_count += 1
            if self._trace_fh is not None:
                try:
                    self._trace_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    self._trace_fh.flush()
                except Exception:
                    pass

    def record_network_event(self, event: Dict[str, Any]) -> None:
        with self._lock:
            if not self.active:
                return
            rec = {"rel_s": self._rel_s(), **event}
            self.network_events_count += 1
            if bool(event.get("failover_triggered", False)) or str(event.get("event")) == "failover":
                self.failover_events_count += 1
            if self._net_fh is not None:
                try:
                    self._net_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    self._net_fh.flush()
                except Exception:
                    pass

    @staticmethod
    def _write_midi_track(track: Any, events: List[Dict[str, Any]], tpq: int = 480, tempo_us: int = 500000) -> None:
        # 500000 us/beat -> 960 ticks/sec
        ticks_per_s = (tpq * 1_000_000.0) / float(tempo_us)
        last_tick = 0
        for ev in sorted(events, key=lambda x: float(x.get("rel_s", 0.0))):
            cur_tick = max(last_tick, int(round(float(ev.get("rel_s", 0.0)) * ticks_per_s)))
            delta = max(0, cur_tick - last_tick)
            last_tick = cur_tick
            etype = ev.get("type", "note_on")
            ch = max(0, min(15, int(ev.get("channel", 0))))
            if etype == "note_on":
                track.append(
                    mido.Message(
                        "note_on",
                        note=max(21, min(108, int(ev.get("pitch", 60)))),
                        velocity=max(0, min(127, int(ev.get("velocity", 64)))),
                        channel=ch,
                        time=delta,
                    )
                )
            elif etype == "note_off":
                track.append(
                    mido.Message(
                        "note_off",
                        note=max(21, min(108, int(ev.get("pitch", 60)))),
                        velocity=max(0, min(127, int(ev.get("velocity", 24)))),
                        channel=ch,
                        time=delta,
                    )
                )
            elif etype == "control_change":
                track.append(
                    mido.Message(
                        "control_change",
                        control=max(0, min(127, int(ev.get("control", 64)))),
                        value=max(0, min(127, int(ev.get("value", 0)))),
                        channel=ch,
                        time=delta,
                    )
                )

    def _export_smf(self, mid_path: Path) -> bool:
        if mido is None:
            return False
        try:
            mid = mido.MidiFile(type=1, ticks_per_beat=480)
            tr_in = mido.MidiTrack()
            tr_in.append(mido.MetaMessage("track_name", name="Disklavier_MIDI_IN", time=0))
            tr_in.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
            self._write_midi_track(tr_in, self._midi_in_events)
            tr_in.append(mido.MetaMessage("end_of_track", time=0))
            mid.tracks.append(tr_in)

            tr_out = mido.MidiTrack()
            tr_out.append(mido.MetaMessage("track_name", name="Disklavier_MIDI_OUT", time=0))
            self._write_midi_track(tr_out, self._midi_out_events)
            tr_out.append(mido.MetaMessage("end_of_track", time=0))
            mid.tracks.append(tr_out)

            mid.save(str(mid_path))
            return True
        except Exception:
            return False

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            if not self.active or self.session_dir is None:
                return self._last_manifest or self._status_locked()
            dur_s = self._rel_s()
            sdir = self.session_dir

            for fh in (self._midi_fh, self._trace_fh, self._net_fh):
                if fh is not None:
                    try:
                        fh.flush()
                        fh.close()
                    except Exception:
                        pass
            self._midi_fh = None
            self._trace_fh = None
            self._net_fh = None

            if self._wav_writer is not None:
                try:
                    if self.audio_frames == 0:
                        # Ensure a valid non-empty stereo WAV header + 20ms silent tail
                        sil = np.zeros((int(self.sample_rate * 0.02), 2), dtype="<i2")
                        self._wav_writer.writeframes(sil.tobytes())
                    self._wav_writer.close()
                except Exception:
                    pass
                self._wav_writer = None

            mid_saved = self._export_smf(sdir / "disklavier_io.mid")
            manifest = {
                "ok": True,
                "active": False,
                "session_id": self.session_id,
                "session_dir": str(sdir),
                "label": self.label,
                "started_at": self._start_iso,
                "ended_at": datetime.now().isoformat(timespec="seconds"),
                "duration_s": round(dur_s, 3),
                "midi_in_count": self.midi_in_count,
                "midi_out_count": self.midi_out_count,
                "audio_frames": self.audio_frames,
                "audio_duration_s": round(self.audio_frames / max(1, self.sample_rate), 3),
                "trace_count": self.trace_count,
                "network_events_count": self.network_events_count,
                "failover_events_count": self.failover_events_count,
                "files": {
                    "midi_jsonl": "midi_events.jsonl",
                    "midi_smf": "disklavier_io.mid" if mid_saved else None,
                    "audio_wav": "ur22_audio.wav",
                    "logic_trace_jsonl": "logic_trace.jsonl",
                    "network_latency_jsonl": "network_latency.jsonl",
                },
            }
            try:
                (sdir / "session_manifest.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass
            self.active = False
            self._last_manifest = manifest
            return dict(manifest)

    def _status_locked(self) -> Dict[str, Any]:
        return {
            "active": self.active,
            "session_id": self.session_id,
            "session_dir": str(self.session_dir) if self.session_dir else "",
            "label": self.label,
            "started_at": self._start_iso,
            "elapsed_s": self._rel_s() if self.active else 0.0,
            "midi_in_count": self.midi_in_count,
            "midi_out_count": self.midi_out_count,
            "audio_frames": self.audio_frames,
            "trace_count": self.trace_count,
            "network_events_count": self.network_events_count,
            "failover_events_count": self.failover_events_count,
        }

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return self._status_locked()


class FieldSessionReplayer:
    """Loads saved `recordings/session_*` directories for offline replay without hardware."""

    @staticmethod
    def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        items: List[Dict[str, Any]] = []
        for ln in path.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if not s:
                continue
            try:
                items.append(json.loads(s))
            except Exception:
                continue
        return items

    @classmethod
    def load_session(cls, session_dir: Path) -> Dict[str, Any]:
        sdir = Path(session_dir)
        manifest_p = sdir / "session_manifest.json"
        manifest = json.loads(manifest_p.read_text(encoding="utf-8")) if manifest_p.exists() else {}
        midi_events = cls._read_jsonl(sdir / "midi_events.jsonl")
        logic_traces = cls._read_jsonl(sdir / "logic_trace.jsonl")
        network_events = cls._read_jsonl(sdir / "network_latency.jsonl")

        wav_info: Dict[str, Any] = {"exists": False}
        wav_p = sdir / "ur22_audio.wav"
        if wav_p.exists():
            try:
                with wave.open(str(wav_p), "rb") as wf:
                    wav_info = {
                        "exists": True,
                        "channels": wf.getnchannels(),
                        "sample_rate": wf.getframerate(),
                        "frames": wf.getnframes(),
                        "duration_s": round(wf.getnframes() / max(1, wf.getframerate()), 3),
                    }
            except Exception as exc:
                wav_info = {"exists": True, "error": str(exc)}

        return {
            "session_dir": str(sdir),
            "manifest": manifest,
            "midi_events": midi_events,
            "logic_traces": logic_traces,
            "network_events": network_events,
            "wav_info": wav_info,
        }

    @classmethod
    def iter_timeline(cls, session_dir: Path) -> List[Dict[str, Any]]:
        loaded = cls.load_session(session_dir)
        timeline: List[Dict[str, Any]] = []
        for m in loaded["midi_events"]:
            timeline.append({"stream": "midi", "rel_s": float(m.get("rel_s", 0.0)), "data": m})
        for tr in loaded["logic_traces"]:
            timeline.append({"stream": "logic_trace", "rel_s": float(tr.get("rel_s", 0.0)), "data": tr})
        for nw in loaded["network_events"]:
            timeline.append({"stream": "network", "rel_s": float(nw.get("rel_s", 0.0)), "data": nw})
        timeline.sort(key=lambda item: item["rel_s"])
        return timeline
