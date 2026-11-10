"""
Telemetry Store & Live Mission Control State Manager.
Persists all Experiment 1-5 acoustic measurements to SQLite + JSON + PNG charts in `data/telemetry/`
and manages the real-time User Action Banner, Countdown Protocol, Agent Brain Stream, and Git Commit feed.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional


BASE_DIR = Path(__file__).resolve().parents[2]
TELEMETRY_DIR = BASE_DIR / "data" / "telemetry"
TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = TELEMETRY_DIR / "rd_studio.sqlite"
SUMMARY_JSON_PATH = TELEMETRY_DIR / "experiments_summary.json"


class TelemetryStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._init_db()

        # Global Top Banner State
        # status_code: "HANDS_OFF" (green), "SOUND_COUNTDOWN" (yellow), "READY" (blue), "ACTION_NEEDED" (red)
        self.banner_state: Dict[str, Any] = {
            "code": "HANDS_OFF",
            "badge": "🟢 [HANDS OFF / 自動実験・開発中]",
            "message": "現在Antigravityがハードウェア初期化とLive Mission Control GUIを起動中です。そのまま見守ってください。",
            "sub_message": "Steinberg UR22mkII (C519ML) & Yamaha Disklavier 接続確認済み",
            "countdown_sec": 0,
            "upcoming_sound_desc": "",
            "task_name": "Milestone 1: Hardware & Safety Guard Initialization",
            "progress_pct": 15,
            "current_phase": "Milestone 1",
        }

        # Live Agent Brain & Terminal Stream (last 120 lines)
        self.terminal_logs: deque = deque(maxlen=150)
        self.brain_steps: List[Dict[str, Any]] = [
            {
                "id": "M1",
                "title": "M1: Hardware & SafetyGuard & Live GUI",
                "status": "in_progress",
                "detail": "UR22mkII + Disklavier connected, 60fps WebSocket active",
            },
            {
                "id": "M2",
                "title": "M2: Exp 1 (VDL Curve) & Exp 2 (Half-Pedal RT60)",
                "status": "pending",
                "detail": "Real acoustic sweep with 3s countdown",
            },
            {
                "id": "M3",
                "title": "M3: IMSLP Classical Library & 5-Stage MIDI Optimizer",
                "status": "pending",
                "detail": "Bach, Fauré, Saint-Saëns, Brahms, Debussy",
            },
            {
                "id": "M4",
                "title": "M4: Exp 3 (Piano-as-Cello Proxy & Bleed Cancellation)",
                "status": "pending",
                "detail": "Double-stops, Flageolet & Spectral Subtraction",
            },
            {
                "id": "M5",
                "title": "M5: Exp 4 (Voicing/Melody-Lead/Halo) & Exp 5 (AI Composer)",
                "status": "pending",
                "detail": "Autonomous AB acoustic evaluation & self-tuning",
            },
            {
                "id": "M6",
                "title": "M6: Full 6-Tab Verification & R&D Encyclopedia",
                "status": "pending",
                "detail": "Final report & all systems ready",
            },
        ]
        self.latest_code_diff: str = (
            "+ class DisklavierSafetyGuard:\n"
            "+     SOFT_SIMULTANEOUS_LIMIT = 12\n"
            "+     HARD_SIMULTANEOUS_LIMIT = 16\n"
            "+     MAX_ACTIVE_VOICES = 20\n"
            "+     MAX_NOTES_PER_SEC = 28\n"
            "+     VALID_VEL_MIN, VALID_VEL_MAX = 20, 105\n"
        )

        # Load existing experiment summary if available
        self.experiments_data: Dict[str, Any] = self._load_summary_json()

    def _init_db(self) -> None:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vdl_measurements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    midi_note INTEGER,
                    note_name TEXT,
                    velocity INTEGER,
                    measured_latency_ms REAL,
                    predicted_latency_ms REAL,
                    peak_dbfs REAL,
                    rms_dbfs REAL,
                    compensated_error_ms REAL,
                    mode TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pedal_measurements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    cc64_value INTEGER,
                    register_name TEXT,
                    rt60_s REAL,
                    acoustic_roughness REAL,
                    clarity_score REAL,
                    recommended_max_cc64 INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS experiment_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    experiment_id TEXT,
                    title TEXT,
                    metrics_json TEXT,
                    notes TEXT
                )
                """
            )
            conn.commit()

    def _load_summary_json(self) -> Dict[str, Any]:
        if SUMMARY_JSON_PATH.exists():
            try:
                return json.loads(SUMMARY_JSON_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "exp1_vdl": {"status": "waiting", "points": [], "chord_sync_test": []},
            "exp2_pedal": {"status": "waiting", "points": [], "optimal_curve": {}},
            "exp3_proxy_bleed": {"status": "waiting", "trials": [], "avg_snr_gain_db": 0.0},
            "exp4_accompaniment_ab": {"status": "waiting", "ab_results": []},
            "exp5_ai_composer": {"status": "waiting", "iterations": []},
            "autonomous_ideas": [],
        }

    def save_summary_json(self) -> None:
        with self._lock:
            SUMMARY_JSON_PATH.write_text(
                json.dumps(self.experiments_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    def log_event(self, message: str) -> None:
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        with self._lock:
            self.terminal_logs.append(line)

    def update_banner(
        self,
        code: str,
        message: str,
        sub_message: Optional[str] = None,
        task_name: Optional[str] = None,
        progress_pct: Optional[int] = None,
        countdown_sec: int = 0,
        upcoming_sound_desc: str = "",
        current_phase: Optional[str] = None,
    ) -> None:
        badges = {
            "HANDS_OFF": "🟢 [HANDS OFF / 自動実験・開発中]",
            "SOUND_COUNTDOWN": f"🟡 [⚠️ PIANO SOUND IN {countdown_sec}.. / 発音予告]",
            "READY": "🔵 [READY TO INTERACT / 操作可能]",
            "ALL_READY": "🔵 [ALL SYSTEMS READY / 全機能完了]",
            "ACTION_NEEDED": "🔴 [ACTION NEEDED / 要確認]",
        }
        with self._lock:
            self.banner_state["code"] = code
            self.banner_state["badge"] = badges.get(code, badges["HANDS_OFF"])
            self.banner_state["message"] = message
            if sub_message is not None:
                self.banner_state["sub_message"] = sub_message
            if task_name is not None:
                self.banner_state["task_name"] = task_name
            if progress_pct is not None:
                self.banner_state["progress_pct"] = int(progress_pct)
            self.banner_state["countdown_sec"] = countdown_sec
            self.banner_state["upcoming_sound_desc"] = upcoming_sound_desc
            if current_phase is not None:
                self.banner_state["current_phase"] = current_phase

    def update_brain_step(
        self, step_id: str, status: str, detail: Optional[str] = None
    ) -> None:
        with self._lock:
            for s in self.brain_steps:
                if s["id"] == step_id:
                    s["status"] = status
                    if detail is not None:
                        s["detail"] = detail
                    break

    def record_vdl_point(
        self,
        midi_note: int,
        note_name: str,
        velocity: int,
        measured_latency_ms: float,
        predicted_latency_ms: float,
        peak_dbfs: float,
        rms_dbfs: float,
        compensated_error_ms: float = 0.0,
        mode: str = "single",
    ) -> None:
        now = time.time()
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO vdl_measurements
                (timestamp, midi_note, note_name, velocity, measured_latency_ms,
                 predicted_latency_ms, peak_dbfs, rms_dbfs, compensated_error_ms, mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    midi_note,
                    note_name,
                    velocity,
                    measured_latency_ms,
                    predicted_latency_ms,
                    peak_dbfs,
                    rms_dbfs,
                    compensated_error_ms,
                    mode,
                ),
            )
            conn.commit()
        self.log_event(
            f"[CALIB Exp1] Note={midi_note} ({note_name}) Vel={velocity} -> Latency={measured_latency_ms:.1f}ms, Peak={peak_dbfs:.1f}dBFS"
        )

    def record_pedal_point(
        self,
        cc64_value: int,
        register_name: str,
        rt60_s: float,
        acoustic_roughness: float,
        clarity_score: float,
        recommended_max_cc64: int,
    ) -> None:
        now = time.time()
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO pedal_measurements
                (timestamp, cc64_value, register_name, rt60_s, acoustic_roughness, clarity_score, recommended_max_cc64)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    cc64_value,
                    register_name,
                    rt60_s,
                    acoustic_roughness,
                    clarity_score,
                    recommended_max_cc64,
                ),
            )
            conn.commit()
        self.log_event(
            f"[CALIB Exp2] CC#64={cc64_value} ({register_name}) -> RT60={rt60_s:.2f}s, Roughness={acoustic_roughness:.3f}, Clarity={clarity_score:.1f}"
        )

    def record_experiment_run(
        self, experiment_id: str, title: str, metrics: dict, notes: str = ""
    ) -> None:
        now = time.time()
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO experiment_runs (timestamp, experiment_id, title, metrics_json, notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    now,
                    experiment_id,
                    title,
                    json.dumps(metrics, ensure_ascii=False),
                    notes,
                ),
            )
            conn.commit()

    def get_git_commits(self, limit: int = 8) -> List[Dict[str, str]]:
        try:
            out = subprocess.check_output(
                [
                    "git",
                    "log",
                    f"-n{limit}",
                    "--pretty=format:%h|%ad|%s",
                    "--date=format:%H:%M:%S",
                ],
                cwd=str(BASE_DIR),
                text=True,
                stderr=subprocess.DEVNULL,
            )
            commits = []
            for line in out.strip().splitlines():
                parts = line.split("|", 2)
                if len(parts) == 3:
                    commits.append(
                        {"hash": parts[0], "time": parts[1], "message": parts[2]}
                    )
            return commits
        except Exception:
            return []

    def get_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "banner": dict(self.banner_state),
                "brain_steps": list(self.brain_steps),
                "terminal_logs": list(self.terminal_logs)[-45:],
                "latest_code_diff": self.latest_code_diff,
                "git_commits": self.get_git_commits(8),
                "experiments": self.experiments_data,
            }
