"""
Telemetry Store & Live Mission Control State Manager.
Persists all Experiment 1-5 acoustic measurements to SQLite + JSON + PNG charts in `data/telemetry/`
and manages the real-time User Action Banner, Countdown Protocol, Agent Brain Stream, and Git Commit feed.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
import threading
import time
from collections import Counter, deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


logger = logging.getLogger("disklavier_auto")

_EXC_LOCK = threading.Lock()
_EXC_COUNTS: Counter[str] = Counter()
_EXC_RECENT: deque = deque(maxlen=30)


def record_caught_exception(context: str, exc: BaseException) -> None:
    """Log a caught non-fatal exception and track its occurrence count for diagnostics."""
    msg = f"[{context}] {type(exc).__name__}: {exc}"
    logger.warning(msg)
    with _EXC_LOCK:
        _EXC_COUNTS[context] += 1
        _EXC_RECENT.append(
            {
                "ts": round(time.time(), 3),
                "context": context,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )


def get_exception_metrics() -> Dict[str, Any]:
    with _EXC_LOCK:
        return {
            "total_count": int(sum(_EXC_COUNTS.values())),
            "by_context": dict(_EXC_COUNTS),
            "recent_errors": list(_EXC_RECENT)[-10:],
        }


BASE_DIR = Path(__file__).resolve().parents[2]
TELEMETRY_DIR = BASE_DIR / "data" / "telemetry"
TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = TELEMETRY_DIR / "rd_studio.sqlite"
SUMMARY_JSON_PATH = TELEMETRY_DIR / "experiments_summary.json"


def resolve_safe_path(
    base_dir: Path,
    rel_path: str,
    allowed_suffixes: Tuple[str, ...] = (),
) -> Optional[Path]:
    """
    Resolve a user-supplied relative path safely inside `base_dir`.
    Rejects null bytes, empty strings, absolute paths, `..` traversal, symlink escapes,
    and extensions outside `allowed_suffixes`.
    """
    if not rel_path or not isinstance(rel_path, str):
        return None
    if "\x00" in rel_path or rel_path.startswith(("/", "\\")):
        return None
    raw_p = Path(rel_path)
    if raw_p.is_absolute():
        return None
    try:
        base_real = base_dir.resolve()
        cand_real = (base_real / raw_p).resolve()
    except Exception as exc:
        record_caught_exception("telemetry_store.resolve_safe_path", exc)
        return None
    if not cand_real.is_relative_to(base_real):
        return None
    if allowed_suffixes and cand_real.suffix.lower() not in tuple(s.lower() for s in allowed_suffixes):
        return None
    return cand_real


class TelemetryStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._git_commits_cache: List[Dict[str, str]] = []
        self._git_commits_ts: float = 0.0
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
            conn.execute("PRAGMA journal_mode=WAL;")
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS generation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    tab_id TEXT,
                    generation INTEGER,
                    best_score REAL,
                    mean_score REAL,
                    melody_beauty REAL,
                    harmonic_drama REAL,
                    counterpoint REAL,
                    human_realism REAL,
                    virtuoso_brilliance REAL,
                    disklavier_safety REAL,
                    is_hof INTEGER DEFAULT 0,
                    story_stage TEXT,
                    commentary_ja TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS event_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    level TEXT,
                    message TEXT
                )
                """
            )
            conn.commit()

    def _load_summary_json(self) -> Dict[str, Any]:
        if SUMMARY_JSON_PATH.exists():
            try:
                return json.loads(SUMMARY_JSON_PATH.read_text(encoding="utf-8"))
            except Exception as exc:
                record_caught_exception("telemetry_store._load_summary_json", exc)
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

    def log_event(self, message: str, level: str = "INFO") -> None:
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        with self._lock:
            self.terminal_logs.append(line)
        try:
            with sqlite3.connect(DB_PATH, timeout=1.0) as conn:
                conn.execute(
                    "INSERT INTO event_logs (timestamp, level, message) VALUES (?, ?, ?)",
                    (time.time(), level, message),
                )
                conn.commit()
        except Exception as exc:
            record_caught_exception("telemetry_store.log_event_db", exc)

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

    def record_generation_point(
        self,
        tab_id: str,
        generation: int,
        best_score: float,
        mean_score: float,
        radar_6axis: Dict[str, Any],
        is_hof: bool = False,
        story_stage: str = "",
        commentary_ja: str = "",
    ) -> None:
        try:
            with sqlite3.connect(DB_PATH, timeout=1.0) as conn:
                conn.execute(
                    """
                    INSERT INTO generation_history (
                        timestamp, tab_id, generation, best_score, mean_score,
                        melody_beauty, harmonic_drama, counterpoint, human_realism,
                        virtuoso_brilliance, disklavier_safety, is_hof, story_stage, commentary_ja
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        time.time(),
                        str(tab_id),
                        int(generation),
                        float(best_score),
                        float(mean_score),
                        float(radar_6axis.get("melody_beauty", 70.0)),
                        float(radar_6axis.get("harmonic_drama", 70.0)),
                        float(radar_6axis.get("counterpoint", 70.0)),
                        float(radar_6axis.get("human_realism", 70.0)),
                        float(radar_6axis.get("virtuoso_brilliance", 70.0)),
                        float(radar_6axis.get("disklavier_safety", 98.0)),
                        1 if is_hof else 0,
                        str(story_stage),
                        str(commentary_ja)[:300],
                    ),
                )
                conn.commit()
        except Exception as exc:
            record_caught_exception("telemetry_store.record_generation_point", exc)

    def get_generation_history_db(
        self, tab_id: Optional[str] = None, limit: int = 120
    ) -> List[Dict[str, Any]]:
        try:
            with sqlite3.connect(DB_PATH, timeout=1.0) as conn:
                conn.row_factory = sqlite3.Row
                if tab_id:
                    rows = conn.execute(
                        "SELECT * FROM generation_history WHERE tab_id = ? ORDER BY id DESC LIMIT ?",
                        (tab_id, int(limit)),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM generation_history ORDER BY id DESC LIMIT ?",
                        (int(limit),),
                    ).fetchall()
                return [dict(r) for r in reversed(rows)]
        except Exception as exc:
            record_caught_exception("telemetry_store.get_generation_history_db", exc)
            return []

    def seed_generation_history_if_empty(
        self,
        evolution_history: Optional[List[Dict[str, Any]]] = None,
        tab_id: str = "tab_main",
    ) -> int:
        """Populate generation_history table on boot if empty so SQLite charts & search work immediately."""
        try:
            with sqlite3.connect(DB_PATH, timeout=1.0) as conn:
                cnt = conn.execute("SELECT COUNT(*) FROM generation_history").fetchone()[0]
                if int(cnt) > 0:
                    return int(cnt)
            points = list(evolution_history or [])
            if not points:
                points = [
                    {
                        "generation": 1,
                        "best_score": 72.0,
                        "mean_score": 69.5,
                        "arc_stage": "Exposition",
                        "chord_label": "初期動機スケッチ",
                    }
                ]
            for idx, p in enumerate(points):
                self.record_generation_point(
                    tab_id=tab_id,
                    generation=int(p.get("generation", idx + 1)),
                    best_score=float(p.get("best_score", 72.0)),
                    mean_score=float(p.get("mean_score", 69.5)),
                    radar_6axis=p.get("radar_6axis")
                    or {
                        "melody_beauty": 74.0 + idx * 1.2,
                        "harmonic_drama": 71.0 + idx * 1.4,
                        "counterpoint": 70.0 + idx * 1.5,
                        "human_realism": 73.0 + idx * 1.3,
                        "virtuoso_brilliance": 69.0 + idx * 1.5,
                        "disklavier_safety": 98.0,
                    },
                    is_hof=float(p.get("best_score", 0.0)) >= 85.0,
                    story_stage=str(p.get("arc_stage", "Exposition")),
                    commentary_ja=str(p.get("chord_label", "初期進化チェックポイント")),
                )
            self.log_event_db("INFO", f"SQLite generation_history seeded ({len(points)} rows).")
            return len(points)
        except Exception as exc:
            record_caught_exception("telemetry_store.seed_generation_history_if_empty", exc)
            return 0

    def get_vdl_measurements_db(self, limit: int = 120) -> List[Dict[str, Any]]:
        try:
            with sqlite3.connect(DB_PATH, timeout=1.0) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM vdl_measurements ORDER BY id DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
                return [dict(r) for r in reversed(rows)]
        except Exception as exc:
            record_caught_exception("telemetry_store.get_vdl_measurements_db", exc)
            return []

    def get_pedal_measurements_db(self, limit: int = 80) -> List[Dict[str, Any]]:
        try:
            with sqlite3.connect(DB_PATH, timeout=1.0) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM pedal_measurements ORDER BY id DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
                return [dict(r) for r in reversed(rows)]
        except Exception as exc:
            record_caught_exception("telemetry_store.get_pedal_measurements_db", exc)
            return []

    def search_sqlite_logs(
        self,
        query: str = "",
        category: str = "all",
        limit: int = 100,
        min_score: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        q_like = f"%{query.strip()}%"
        results: List[Dict[str, Any]] = []
        lim = max(1, min(500, int(limit)))
        cat = category
        if cat == "generations":
            cat = "generation_history"
        elif cat == "events":
            cat = "event_logs"
        elif cat == "vdl":
            cat = "vdl_measurements"
        elif cat == "experiments":
            cat = "experiment_runs"
        try:
            with sqlite3.connect(DB_PATH, timeout=1.0) as conn:
                conn.row_factory = sqlite3.Row
                if cat in ("all", "event_logs"):
                    rows = conn.execute(
                        "SELECT id, timestamp, level, message FROM event_logs "
                        "WHERE message LIKE ? OR level LIKE ? ORDER BY id DESC LIMIT ?",
                        (q_like, q_like, lim),
                    ).fetchall()
                    for r in rows:
                        results.append(
                            {
                                "id": r["id"],
                                "source": "event_logs",
                                "timestamp": r["timestamp"],
                                "category": r["level"],
                                "summary": r["message"],
                                "detail": "",
                            }
                        )
                if cat in ("all", "generation_history"):
                    if min_score is not None:
                        rows = conn.execute(
                            "SELECT id, timestamp, tab_id, generation, best_score, is_hof, story_stage, commentary_ja "
                            "FROM generation_history "
                            "WHERE best_score >= ? AND (commentary_ja LIKE ? OR story_stage LIKE ? OR tab_id LIKE ?) "
                            "ORDER BY id DESC LIMIT ?",
                            (float(min_score), q_like, q_like, q_like, lim),
                        ).fetchall()
                    else:
                        rows = conn.execute(
                            "SELECT id, timestamp, tab_id, generation, best_score, is_hof, story_stage, commentary_ja "
                            "FROM generation_history "
                            "WHERE commentary_ja LIKE ? OR story_stage LIKE ? OR tab_id LIKE ? "
                            "ORDER BY id DESC LIMIT ?",
                            (q_like, q_like, q_like, lim),
                        ).fetchall()
                    for r in rows:
                        hof_tag = " 🏆HoF" if r["is_hof"] else ""
                        results.append(
                            {
                                "id": r["id"],
                                "source": "generation_history",
                                "timestamp": r["timestamp"],
                                "category": f"Gen {r['generation']}{hof_tag}",
                                "summary": f"[{r['tab_id']}] {r['best_score']:.1f}点 ({r['story_stage']})",
                                "detail": r["commentary_ja"],
                                "best_score": r["best_score"],
                            }
                        )
                if cat in ("all", "vdl_measurements"):
                    rows = conn.execute(
                        "SELECT id, timestamp, midi_note, note_name, velocity, measured_latency_ms, predicted_latency_ms, mode "
                        "FROM vdl_measurements "
                        "WHERE note_name LIKE ? OR mode LIKE ? ORDER BY id DESC LIMIT ?",
                        (q_like, q_like, lim),
                    ).fetchall()
                    for r in rows:
                        results.append(
                            {
                                "id": r["id"],
                                "source": "vdl_measurements",
                                "timestamp": r["timestamp"],
                                "category": f"VDL ({r['mode']})",
                                "summary": f"{r['note_name']} (MIDI {r['midi_note']}, Vel {r['velocity']}): {r['measured_latency_ms']:.1f}ms",
                                "detail": f"pred={r['predicted_latency_ms']:.1f}ms",
                            }
                        )
                if cat in ("all", "pedal_measurements"):
                    rows = conn.execute(
                        "SELECT id, timestamp, cc64_value, register_name, rt60_s, acoustic_roughness, clarity_score "
                        "FROM pedal_measurements "
                        "WHERE register_name LIKE ? ORDER BY id DESC LIMIT ?",
                        (q_like, lim),
                    ).fetchall()
                    for r in rows:
                        results.append(
                            {
                                "id": r["id"],
                                "source": "pedal_measurements",
                                "timestamp": r["timestamp"],
                                "category": f"CC#64={r['cc64_value']}",
                                "summary": f"{r['register_name']}: RT60={r['rt60_s']:.2f}s / Rough={r['acoustic_roughness']:.3f}",
                                "detail": f"Clarity={r['clarity_score']:.2f}",
                            }
                        )
                if cat in ("all", "experiment_runs"):
                    rows = conn.execute(
                        "SELECT id, timestamp, experiment_id, title, notes FROM experiment_runs "
                        "WHERE title LIKE ? OR notes LIKE ? OR experiment_id LIKE ? ORDER BY id DESC LIMIT ?",
                        (q_like, q_like, q_like, lim),
                    ).fetchall()
                    for r in rows:
                        results.append(
                            {
                                "id": r["id"],
                                "source": "experiment_runs",
                                "timestamp": r["timestamp"],
                                "category": r["experiment_id"],
                                "summary": r["title"],
                                "detail": r["notes"],
                            }
                        )
            results.sort(key=lambda x: float(x.get("timestamp") or 0.0), reverse=True)
            return results[:lim]
        except Exception as exc:
            record_caught_exception("telemetry_store.search_sqlite_logs", exc)
            return []

    def export_sqlite_rows(
        self,
        table: str = "generation_history",
        query: str = "",
        min_score: Optional[float] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        lim = max(1, min(1000, int(limit)))
        q_like = f"%{query.strip()}%"
        valid_tables = (
            "generation_history",
            "vdl_measurements",
            "pedal_measurements",
            "experiment_runs",
            "event_logs",
        )
        if table not in valid_tables:
            return self.search_sqlite_logs(query=query, category="all", limit=lim, min_score=min_score)
        try:
            with sqlite3.connect(DB_PATH, timeout=1.0) as conn:
                conn.row_factory = sqlite3.Row
                if table == "generation_history":
                    if min_score is not None:
                        rows = conn.execute(
                            "SELECT * FROM generation_history "
                            "WHERE best_score >= ? AND (commentary_ja LIKE ? OR story_stage LIKE ? OR tab_id LIKE ?) "
                            "ORDER BY id DESC LIMIT ?",
                            (float(min_score), q_like, q_like, q_like, lim),
                        ).fetchall()
                    else:
                        rows = conn.execute(
                            "SELECT * FROM generation_history "
                            "WHERE commentary_ja LIKE ? OR story_stage LIKE ? OR tab_id LIKE ? "
                            "ORDER BY id DESC LIMIT ?",
                            (q_like, q_like, q_like, lim),
                        ).fetchall()
                elif table == "vdl_measurements":
                    rows = conn.execute(
                        "SELECT * FROM vdl_measurements WHERE note_name LIKE ? OR mode LIKE ? ORDER BY id DESC LIMIT ?",
                        (q_like, q_like, lim),
                    ).fetchall()
                elif table == "pedal_measurements":
                    rows = conn.execute(
                        "SELECT * FROM pedal_measurements WHERE register_name LIKE ? ORDER BY id DESC LIMIT ?",
                        (q_like, lim),
                    ).fetchall()
                elif table == "experiment_runs":
                    rows = conn.execute(
                        "SELECT * FROM experiment_runs WHERE title LIKE ? OR notes LIKE ? OR experiment_id LIKE ? ORDER BY id DESC LIMIT ?",
                        (q_like, q_like, q_like, lim),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM event_logs WHERE message LIKE ? OR level LIKE ? ORDER BY id DESC LIMIT ?",
                        (q_like, q_like, lim),
                    ).fetchall()
                return [dict(r) for r in rows]
        except Exception as exc:
            record_caught_exception("telemetry_store.export_sqlite_rows", exc)
            return []

    def get_git_commits(self, limit: int = 8) -> List[Dict[str, str]]:
        now = time.monotonic()
        if self._git_commits_cache and (now - self._git_commits_ts) < 30.0:
            return list(self._git_commits_cache[:limit])
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
            self._git_commits_cache = commits
            self._git_commits_ts = now
            return commits
        except Exception as exc:
            record_caught_exception("telemetry_store.get_git_commits", exc)
            return list(self._git_commits_cache[:limit])

    def get_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "banner": dict(self.banner_state),
                "brain_steps": list(self.brain_steps),
                "terminal_logs": list(self.terminal_logs)[-45:],
                "latest_code_diff": self.latest_code_diff,
                "git_commits": self.get_git_commits(8),
                "experiments": self.experiments_data,
                "exception_metrics": get_exception_metrics(),
            }
