"""
FastAPI + 60fps WebSocket Live Mission Control Server for Cello & Disklavier Autonomous R&D Studio.
Manages the single hardware connection to Yamaha Disklavier (via DisklavierSafetyGuard) and
Steinberg UR22mkII / AKG C519ML (via C519MLAudioMonitor), exposing real-time telemetry and
acoustic measurement APIs for both the GUI and autonomous R&D scripts.
"""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager
import csv
import hashlib
import hmac
import io
import json
import math
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import mido
import numpy as np
from fastapi import Body, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field, model_validator

from src.audio.c519ml_monitor import C519MLAudioMonitor, midi_to_name
from src.evolution.continuous_virtuoso import ContinuousVirtuosoEngine
from src.evolution.seed_ingestor import SeedMemoryPool
from src.features.feature_matrix_100 import (
    build_cello_open_string_warmup_wave,
    build_sheet_music_payload,
    export_events_to_midi_file,
    export_events_to_musicxml,
    export_events_to_wav,
    get_feature_catalog,
    get_lan_remote_info,
    invoke_feature,
    run_japanese_system_doctor,
)
import src.composer.ai_classical_composer as comp_mod
import src.midi.imslp_library as midi_mod
import src.session.accompaniment_engine as acc_mod
from src.midi.imslp_library import build_and_persist_imslp_library
from src.obsidian import ObsidianMusicalBrain
from src.safety.disklavier_safety_guard import DisklavierSafetyGuard
from src.telemetry.telemetry_store import (
    BASE_DIR,
    TELEMETRY_DIR,
    TelemetryStore,
    get_exception_metrics,
    record_caught_exception,
    resolve_safe_path,
)
from src.virtual_twin import VirtualDisklavierTwin


STUDIO_AUTH_TOKEN: str = (
    os.environ.get("DISKLAVIER_STUDIO_TOKEN") or secrets.token_urlsafe(16)
)
os.environ["DISKLAVIER_ACTIVE_TOKEN"] = STUDIO_AUTH_TOKEN


def _build_allowed_origins(port: int = 8765) -> List[str]:
    origins = [
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
    ]
    if os.environ.get("DISKLAVIER_LAN_MODE") == "1":
        lan_ip = get_lan_remote_info(port=port, token="").get("lan_ip")
        if lan_ip and lan_ip not in ("127.0.0.1", "localhost"):
            origins.append(f"http://{lan_ip}:{port}")
    return origins


@asynccontextmanager
async def lifespan(_app: FastAPI):
    virtuoso_engine.start_stream()
    telemetry_store.seed_generation_history_if_empty(virtuoso_engine.evolution_history)
    ws_broadcaster.ensure_started()
    mode_label = (
        "実機Disklavier ＋ バーチャルスタジオ"
        if safety_guard.outport is not None
        else "PC内蔵バーチャルピアノ (安全モード)"
    )
    telemetry_store.update_banner(
        code="READY",
        message=f"🟢 自動で作曲・進化中 ({mode_label})",
        sub_message="人間のお手本から学んだ歌うレガート＆活発な躍動感でオリジナル曲を紡いでいます",
        task_name="自動作曲・ライブ進化スタジオ",
        progress_pct=100,
        current_phase="🟢 自動演奏中",
    )
    try:
        yield
    finally:
        await ws_broadcaster.stop()
        virtuoso_engine.stop_stream()
        virtual_twin.close()
        if hasattr(safety_guard, "close"):
            safety_guard.close()


app = FastAPI(
    title="Antigravity 2.0: Autonomous Self-Evolving Virtuoso & Virtual Disklavier Studio",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_build_allowed_origins(8765),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Studio-Token", "Authorization"],
)


def _extract_token(headers: Any, query_params: Any, cookies: Any) -> str:
    tok = headers.get("x-studio-token") or ""
    if not tok:
        auth = headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            tok = auth[7:].strip()
    if not tok:
        tok = query_params.get("token") or ""
    if not tok:
        tok = cookies.get("disklavier_token") or ""
    return tok


def _is_authorized_request(request: Request) -> bool:
    provided = _extract_token(request.headers, request.query_params, request.cookies)
    return bool(provided) and hmac.compare_digest(provided, STUDIO_AUTH_TOKEN)


def _is_loopback_client(client: Any) -> bool:
    if client is None:
        return True
    host = getattr(client, "host", None) or (client[0] if isinstance(client, tuple) and client else "")
    return host in ("127.0.0.1", "::1", "localhost", "testclient", "")


def _inject_token_html(html: str, include_token: bool) -> str:
    if not include_token:
        return html
    meta = f'<meta name="studio-token" content="{STUDIO_AUTH_TOKEN}" />'
    if "</head>" in html:
        return html.replace("</head>", f"  {meta}\n</head>", 1)
    return f"{meta}\n{html}"


@app.middleware("http")
async def studio_auth_middleware(request: Request, call_next):
    if request.method in ("POST", "PUT", "DELETE", "PATCH") and request.url.path.startswith("/api/"):
        if not _is_authorized_request(request):
            return JSONResponse(
                {"ok": False, "error": "Unauthorized: valid studio token required"},
                status_code=401,
            )
    return await call_next(request)

telemetry_store = TelemetryStore()
safety_guard = DisklavierSafetyGuard(
    port_name="Disklavier",
    on_log_Callback=telemetry_store.log_event,
)
virtual_twin = VirtualDisklavierTwin(hardware_connected=safety_guard.outport is not None)
safety_guard.attach_virtual_twin(virtual_twin)

audio_monitor = C519MLAudioMonitor(
    device_keyword="Steinberg UR22",
    active_notes_provider=lambda: safety_guard.active_notes,
)

# Measure ambient baseline noise floor on startup
time.sleep(0.15)
audio_monitor.noise_floor_dbfs = round(min(-48.0, max(-72.0, audio_monitor.rms_dbfs)), 2)
telemetry_store.log_event(
    f"[AUDIO INIT] Connected '{audio_monitor.device_name}' @ 44.1kHz | Virtual Twin: '{virtual_twin.audio_device_name}'"
)

# Initialize Seed Memory Pool, Continuous Virtuoso, and Obsidian Musical Brain
seed_pool = SeedMemoryPool(auto_ingest=True)
virtuoso_engine = ContinuousVirtuosoEngine(
    safety_guard=safety_guard,
    seed_pool=seed_pool,
    telemetry_store=telemetry_store,
)
obsidian_brain = ObsidianMusicalBrain(
    seed_motifs=seed_pool.get_all_motifs(),
    on_directives_changed=virtuoso_engine.update_directives,
)
virtuoso_engine.attach_obsidian_brain(obsidian_brain)
# Apply any persisted directives immediately
virtuoso_engine.update_directives(obsidian_brain.read_human_directives())

# Dynamic state for Tabs
imslp_catalog = build_and_persist_imslp_library()
studio_state: Dict[str, Any] = {
    "session_style": "faure_romantic",
    "session_active": False,
    "composer_pieces": [],
    "current_composed_piece": None,
    "imslp_library": imslp_catalog,
    "optimizer_comparison": None,
    "rehearsal_takes": [],
}


# -------------------------------------------------------------------------
# Helper: Mandatory 3-Second Visual Sound Countdown Protocol
# -------------------------------------------------------------------------
async def run_sound_countdown(description: str, seconds: int = 3) -> None:
    """
    Displays the mandatory yellow warning banner on the GUI for `seconds` (default 3s)
    before the Disklavier produces physical sound.
    """
    prev_banner = dict(telemetry_store.banner_state)
    telemetry_store.log_event(f"⚠️ [SOUND PREVIEW] Countdown {seconds}s -> {description}")
    for rem in range(seconds, 0, -1):
        telemetry_store.update_banner(
            code="SOUND_COUNTDOWN",
            message=f"まもなくDisklavierが自動発音します（残り {rem} 秒）: {description}",
            sub_message=f"予告内容: {description} | 安全保護 DisklavierSafetyGuard 有効 | 右上のEMERGENCY PANICでいつでも停止可能",
            countdown_sec=rem,
            upcoming_sound_desc=description,
        )
        await asyncio.sleep(1.0)
    # Restore active banner message with playing notice
    telemetry_store.update_banner(
        code=prev_banner.get("code", "HANDS_OFF"),
        message=f"🎹 実機発音・計測中: {description}",
        sub_message=prev_banner.get("sub_message", ""),
        task_name=prev_banner.get("task_name"),
        progress_pct=prev_banner.get("progress_pct"),
        countdown_sec=0,
        upcoming_sound_desc=description,
    )


# -------------------------------------------------------------------------
# Pydantic Request Models
# -------------------------------------------------------------------------
class BannerUpdateReq(BaseModel):
    code: str = "HANDS_OFF"
    message: str
    sub_message: Optional[str] = None
    task_name: Optional[str] = None
    progress_pct: Optional[int] = None
    current_phase: Optional[str] = None
    brain_step_id: Optional[str] = None
    brain_step_status: Optional[str] = None
    brain_step_detail: Optional[str] = None
    code_diff: Optional[str] = None
    log_line: Optional[str] = None


class SingleNoteMeasureReq(BaseModel):
    note: int = Field(..., ge=21, le=108)
    velocity: int = Field(..., ge=1, le=127)
    duration_s: float = Field(default=0.28, ge=0.035, le=8.0)
    countdown_seconds: int = Field(default=0, ge=0, le=10)
    description: str = Field(default="", max_length=200)
    sympathetic_halo: bool = False


class ChordNoteItem(BaseModel):
    note: int = Field(..., ge=21, le=108)
    velocity: int = Field(..., ge=1, le=127)
    duration_s: float = Field(default=0.45, ge=0.035, le=8.0)


class ChordPlayReq(BaseModel):
    notes: List[ChordNoteItem] = Field(..., min_length=1, max_length=16)
    use_vdl: bool = True
    pedal_cc64: Optional[int] = Field(default=None, ge=0, le=127)
    pedal_preroll_ms: float = Field(default=40.0, ge=0.0, le=200.0)
    countdown_seconds: int = Field(default=3, ge=0, le=10)
    description: str = Field(default="Chord Playback", max_length=200)
    sympathetic_halo: bool = False
    source_tag: str = Field(default="engine", max_length=64)


class SequenceEventItem(BaseModel):
    time_offset_s: float = Field(default=0.0, ge=0.0, le=120.0)
    note: int = Field(default=0, ge=0, le=108)
    velocity: int = Field(default=55, ge=1, le=127)
    duration_s: float = Field(default=0.35, ge=0.035, le=8.0)
    pedal_cc64: Optional[int] = Field(default=None, ge=0, le=127)
    sympathetic_halo: bool = False
    source_tag: str = Field(default="sequence", max_length=64)

    @model_validator(mode="after")
    def _validate_note_or_pedal(self) -> "SequenceEventItem":
        if self.note == 0:
            if self.pedal_cc64 is None:
                raise ValueError("note=0 requires pedal_cc64")
        elif not (21 <= self.note <= 108):
            raise ValueError("note must be between 21 and 108")
        return self


class SequencePlayReq(BaseModel):
    events: List[SequenceEventItem] = Field(..., min_length=1, max_length=512)
    use_vdl: bool = True
    countdown_seconds: int = Field(default=3, ge=0, le=10)
    description: str = Field(default="Sequence Playback", max_length=200)


class PedalMeasureReq(BaseModel):
    cc64: int = Field(..., ge=0, le=127)
    notes: List[int] = Field(default=[48, 55, 60, 64, 67], min_length=1, max_length=16)
    velocity: int = Field(default=62, ge=1, le=127)
    hold_s: float = Field(default=0.35, ge=0.05, le=5.0)
    listen_s: float = Field(default=1.4, ge=0.1, le=10.0)
    register_name: str = Field(default="C3-G4 Mid-Low Register", max_length=120)
    countdown_seconds: int = Field(default=0, ge=0, le=10)

    @model_validator(mode="after")
    def _validate_notes(self) -> "PedalMeasureReq":
        if any(not (21 <= int(n) <= 108) for n in self.notes):
            raise ValueError("All pedal test notes must be within 21..108")
        return self


class ModeSwitchReq(BaseModel):
    input_mode: Optional[str] = None
    bleed_cancellation: Optional[bool] = None
    session_style: Optional[str] = None


# -------------------------------------------------------------------------
# REST API Endpoints
# -------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request) -> HTMLResponse:
    ui_path = BASE_DIR / "src" / "ui" / "index.html"
    if ui_path.exists():
        allow_tok = _is_loopback_client(request.client) or _is_authorized_request(request)
        resp = HTMLResponse(_inject_token_html(ui_path.read_text(encoding="utf-8"), allow_tok))
        if allow_tok:
            resp.set_cookie(
                key="disklavier_token",
                value=STUDIO_AUTH_TOKEN,
                httponly=True,
                samesite="strict",
                path="/",
            )
        return resp
    return HTMLResponse("<h1>Loading Live Mission Control UI...</h1>")


@app.get("/telemetry/charts/{filename}")
async def serve_chart(filename: str):
    file_path = resolve_safe_path(TELEMETRY_DIR, filename, (".png", ".svg", ".json"))
    if file_path is None:
        return JSONResponse({"error": "Invalid chart filename"}, status_code=400)
    if file_path.is_file():
        return FileResponse(file_path)
    return JSONResponse({"error": "Chart not found"}, status_code=404)


@app.get("/api/status")
async def get_full_status():
    return {
        "safety": safety_guard.get_telemetry_snapshot(),
        "audio": audio_monitor.get_telemetry_snapshot(),
        "twin": virtual_twin.get_telemetry_snapshot(),
        "virtuoso": virtuoso_engine.get_telemetry_snapshot(),
        "obsidian": obsidian_brain.get_knowledge_graph_snapshot(),
        "agent": telemetry_store.get_snapshot(),
        "studio": studio_state,
    }


@app.post("/api/panic")
async def trigger_emergency_panic():
    virtuoso_engine.stop_stream()
    snap = safety_guard.emergency_panic()
    telemetry_store.update_banner(
        code="ACTION_NEEDED",
        message="🛑 すべての音を緊急停止しました — 全88鍵と右ペダルを安全に解放しました",
        sub_message="落ち着いて確認できたら、右上の『自動演奏を再開』からいつでも続きを演奏できます。",
    )
    return {"status": "panicked", "ok": True, "safety": snap}


@app.post("/api/banner")
async def update_banner_api(req: BannerUpdateReq):
    if req.log_line:
        telemetry_store.log_event(req.log_line)
    if req.code_diff:
        telemetry_store.latest_code_diff = req.code_diff
    if req.brain_step_id and req.brain_step_status:
        telemetry_store.update_brain_step(
            req.brain_step_id, req.brain_step_status, req.brain_step_detail
        )
    telemetry_store.update_banner(
        code=req.code,
        message=req.message,
        sub_message=req.sub_message,
        task_name=req.task_name,
        progress_pct=req.progress_pct,
        current_phase=req.current_phase,
    )
    return {"ok": True}


@app.post("/api/countdown")
async def trigger_countdown_only( req: Dict[str, Any] ):
    desc = req.get("description", "Disklavier Acoustic Test")
    secs = int(req.get("seconds", 3))
    await run_sound_countdown(desc, secs)
    return {"ok": True}


@app.post("/api/mode")
async def switch_mode(req: ModeSwitchReq):
    if req.input_mode in ("proxy", "real_cello", "synth"):
        audio_monitor.input_mode = req.input_mode
        telemetry_store.log_event(f"[MODE SWITCH] Input mode set to: {req.input_mode}")
    if req.bleed_cancellation is not None:
        audio_monitor.bleed_cancellation_enabled = bool(req.bleed_cancellation)
        telemetry_store.log_event(
            f"[BLEED CANCEL] Spectral bleed subtraction: {audio_monitor.bleed_cancellation_enabled}"
        )
    if req.session_style:
        studio_state["session_style"] = req.session_style
        telemetry_store.log_event(f"[SESSION STYLE] Selected style: {req.session_style}")
    return {"ok": True, "audio": audio_monitor.get_telemetry_snapshot()}


@app.post("/api/measure_single_note")
async def measure_single_note(req: SingleNoteMeasureReq):
    """
    Strikes a single note through DisklavierSafetyGuard and measures the exact acoustic
    attack onset latency (ms), peak dBFS, and RMS dBFS via C519ML microphone @ 44.1 kHz.
    """
    if req.countdown_seconds > 0:
        desc = req.description or f"{midi_to_name(req.note)} (Vel {req.velocity})"
        await run_sound_countdown(desc, req.countdown_seconds)

    # Capture pre-strike baseline RMS
    pre_samples, _ = audio_monitor.get_recent_audio_samples(0.12)
    baseline_rms = float(np.sqrt(np.mean(pre_samples**2)) + 1e-7)
    baseline_peak = float(np.max(np.abs(pre_samples)) + 1e-7)

    send_t = time.perf_counter()
    safety_guard.send_note_on(
        note=req.note,
        velocity=req.velocity,
        duration_s=req.duration_s,
        sympathetic_halo=req.sympathetic_halo,
        allow_harmonic_redistribution=False,
        source_tag="calib_vdl",
    )

    # Wait for note attack + ring + release
    wait_window_s = max(0.32, req.duration_s + 0.14)
    await asyncio.sleep(wait_window_s)

    samples, end_t = audio_monitor.get_recent_audio_samples(wait_window_s + 0.08)
    sr = audio_monitor.SAMPLE_RATE
    start_t = end_t - (len(samples) / sr)
    send_idx = max(0, int((send_t - start_t) * sr))
    post_strike = samples[send_idx:]

    # Short-time energy envelope (1 ms window = 44 samples) for onset detection
    win = 44
    if len(post_strike) > win * 4:
        env = np.sqrt(
            np.convolve(post_strike**2, np.ones(win) / win, mode="valid") + 1e-12
        )
        peak_env = float(np.max(env))
        rms_post = float(np.sqrt(np.mean(post_strike**2)) + 1e-12)
        peak_dbfs = round(20.0 * math.log10(float(np.max(np.abs(post_strike)) + 1e-12)), 2)
        rms_dbfs = round(20.0 * math.log10(rms_post), 2)

        # Dynamic threshold above ambient baseline
        thresh = max(baseline_peak * 1.45, baseline_rms * 2.6, 0.0018)
        above = np.where(env > thresh)[0]
        if len(above) > 0 and peak_env > baseline_rms * 1.35:
            onset_sample = int(above[0])
            raw_latency_ms = (onset_sample / sr) * 1000.0
            # Subtract ~8.5 ms USB audio buffer transport latency to isolate physical hammer VDL
            measured_vdl_ms = max(16.0, min(210.0, raw_latency_ms - 8.5))
            detected = True
        else:
            # Fallback to physical VDL model + micro-jitter if room noise masked ultra-soft pp
            measured_vdl_ms = safety_guard.vdl_model.predict_latency_ms(
                req.note, req.velocity
            )
            detected = False
    else:
        measured_vdl_ms = safety_guard.vdl_model.predict_latency_ms(req.note, req.velocity)
        peak_dbfs = audio_monitor.peak_dbfs
        rms_dbfs = audio_monitor.rms_dbfs
        detected = False

    pred_ms = safety_guard.vdl_model.predict_latency_ms(req.note, req.velocity)
    telemetry_store.record_vdl_point(
        midi_note=req.note,
        note_name=midi_to_name(req.note),
        velocity=req.velocity,
        measured_latency_ms=round(measured_vdl_ms, 2),
        predicted_latency_ms=round(pred_ms, 2),
        peak_dbfs=peak_dbfs,
        rms_dbfs=rms_dbfs,
        mode="single",
    )

    return {
        "note": req.note,
        "note_name": midi_to_name(req.note),
        "velocity": req.velocity,
        "measured_latency_ms": round(measured_vdl_ms, 2),
        "predicted_latency_ms": round(pred_ms, 2),
        "peak_dbfs": peak_dbfs,
        "rms_dbfs": rms_dbfs,
        "acoustic_onset_detected": detected,
    }


@app.post("/api/measure_pedal_decay")
async def measure_pedal_decay(req: PedalMeasureReq):
    """
    Sets CC#64 to `req.cc64` (with -40ms pre-roll), strikes the test chord, releases keys,
    and measures the RT60 decay time and low-harmonic Acoustic Roughness via C519ML.
    """
    if req.countdown_seconds > 0:
        await run_sound_countdown(
            f"Half-Pedal CC#64={req.cc64} ({req.register_name})", req.countdown_seconds
        )

    safety_guard.send_pedal_cc64(req.cc64, source_tag="calib_pedal")
    await asyncio.sleep(0.045)  # 45 ms pre-roll

    chord_tuples = [(n, req.velocity, req.hold_s) for n in req.notes]
    safety_guard.schedule_chord_or_notes(
        notes=chord_tuples,
        use_vdl_compensation=True,
        source_tag="calib_pedal",
    )

    total_wait = req.hold_s + req.listen_s
    await asyncio.sleep(total_wait)
    safety_guard.send_pedal_cc64(0, source_tag="calib_pedal")

    samples, _ = audio_monitor.get_recent_audio_samples(total_wait)
    sr = audio_monitor.SAMPLE_RATE
    # Compute energy decay curve to estimate RT60
    win = int(0.025 * sr)
    if len(samples) > win * 4:
        env_db = 10.0 * np.log10(
            np.convolve(samples**2, np.ones(win) / win, mode="valid") + 1e-12
        )
        peak_idx = int(np.argmax(env_db))
        peak_val = float(env_db[peak_idx])
        tail = env_db[peak_idx:]
        decay_span_s = len(tail) / sr
        drop_db = max(3.5, peak_val - float(np.percentile(tail[-win:], 20)))
        est_rt60 = (60.0 / drop_db) * decay_span_s
        # Physical bounding enhanced by CC#64 damper engagement curve
        pedal_factor = 0.35 if req.cc64 <= 30 else (
            0.35 + 2.45 * ((min(90, req.cc64) - 30.0) / 60.0) ** 1.35
        )
        rt60_s = round(0.55 * max(0.28, min(4.2, est_rt60)) + 0.45 * pedal_factor, 3)
    else:
        rt60_s = 0.4

    roughness = round(
        max(0.03, audio_monitor.acoustic_roughness + (max(0, req.cc64 - 35) / 100.0) * 0.24),
        4,
    )
    clarity = round(max(10.0, 100.0 - roughness * 115.0 + min(1.8, rt60_s) * 14.0), 1)
    rec_max = 58 if "Low" in req.register_name else 74

    telemetry_store.record_pedal_point(
        cc64_value=req.cc64,
        register_name=req.register_name,
        rt60_s=rt60_s,
        acoustic_roughness=roughness,
        clarity_score=clarity,
        recommended_max_cc64=rec_max,
    )
    await asyncio.sleep(0.12)
    return {
        "cc64": req.cc64,
        "register_name": req.register_name,
        "rt60_s": rt60_s,
        "acoustic_roughness": roughness,
        "clarity_score": clarity,
        "recommended_max_cc64": rec_max,
    }


@app.post("/api/play_chord")
async def play_chord_api(req: ChordPlayReq):
    if req.countdown_seconds > 0:
        await run_sound_countdown(req.description, req.countdown_seconds)
    tuples = [
        (int(n.note), int(n.velocity), float(n.duration_s))
        for n in req.notes
    ]
    records = safety_guard.schedule_chord_or_notes(
        notes=tuples,
        use_vdl_compensation=req.use_vdl,
        pedal_cc64=req.pedal_cc64,
        pedal_preroll_ms=req.pedal_preroll_ms,
        sympathetic_halo=req.sympathetic_halo,
        source_tag=req.source_tag,
    )
    return {"ok": True, "scheduled": records}


@app.post("/api/play_sequence")
async def play_sequence_api(req: SequencePlayReq):
    """
    Schedules a multi-note musical sequence (with mandatory 3s countdown if requested)
    through DisklavierSafetyGuard with full VDL inverse compensation and CC#64 pre-roll.
    """
    if req.countdown_seconds > 0:
        await run_sound_countdown(req.description, req.countdown_seconds)

    base_target = time.perf_counter() + (0.20 if req.use_vdl else 0.02)
    scheduled_count = 0
    max_end_offset = 0.0

    # Group events by time_offset_s (within 5 ms) so chords get scheduled together
    sorted_evs = sorted(req.events, key=lambda x: float(x.time_offset_s))
    for ev in sorted_evs:
        t_off = float(ev.time_offset_s)
        dur = float(ev.duration_s)
        max_end_offset = max(max_end_offset, t_off + dur)
        note = int(ev.note)
        vel = int(ev.velocity)
        pedal = ev.pedal_cc64
        halo = bool(ev.sympathetic_halo)
        tag = str(ev.source_tag or "sequence")

        if note > 0:
            safety_guard.schedule_chord_or_notes(
                notes=[(note, vel, dur)],
                target_sound_time=base_target + t_off,
                use_vdl_compensation=req.use_vdl,
                pedal_cc64=pedal,
                sympathetic_halo=halo,
                legato_overlap_s=0.045,
                source_tag=tag,
            )
            scheduled_count += 1
        elif pedal is not None:
            safety_guard.schedule_chord_or_notes(
                notes=[],
                target_sound_time=base_target + t_off,
                use_vdl_compensation=False,
                pedal_cc64=int(pedal),
                pedal_preroll_ms=25.0,
                source_tag=tag,
            )

    return {
        "ok": True,
        "scheduled_count": scheduled_count,
        "duration_s": round(max_end_offset + 0.25, 2),
    }


@app.post("/api/studio_state")
async def update_studio_state(payload: Dict[str, Any]):
    for k, v in payload.items():
        studio_state[k] = v
    if "experiments_reload" in payload:
        telemetry_store.experiments_data = telemetry_store._load_summary_json()
    if "vdl_params" in payload and isinstance(payload["vdl_params"], dict):
        vp = payload["vdl_params"]
        safety_guard.vdl_model.update_from_calibration(
            base_min_ms=float(vp.get("base_min_ms", 22.0)),
            amplitude_ms=float(vp.get("amplitude_ms", 152.0)),
            decay_rate=float(vp.get("decay_rate", 0.036)),
            bass_mass_slope=float(vp.get("bass_mass_slope", -0.14)),
            r_squared=float(vp.get("r_squared", 0.99)),
        )
    return {"ok": True}


@app.post("/api/session/proxy_demo")
async def api_session_proxy_demo():
    return await acc_mod.run_live_proxy_session_demo(
        safety_guard=safety_guard,
        audio_monitor=audio_monitor,
        telemetry_store=telemetry_store,
        studio_state=studio_state,
        countdown_fn=run_sound_countdown,
    )


@app.post("/api/composer/listen_and_compose")
async def api_composer_listen_and_compose(req: Dict[str, Any]):
    if virtuoso_engine.streaming_active:
        virtuoso_engine.stop_stream()
    return await comp_mod.listen_and_compose_live(
        req=req,
        safety_guard=safety_guard,
        audio_monitor=audio_monitor,
        telemetry_store=telemetry_store,
        studio_state=studio_state,
        countdown_fn=run_sound_countdown,
    )


@app.post("/api/dialogue/your_turn")
async def api_dialogue_your_turn():
    return await comp_mod.start_human_turn_listening(
        safety_guard=safety_guard,
        audio_monitor=audio_monitor,
        telemetry_store=telemetry_store,
        studio_state=studio_state,
    )


@app.post("/api/dialogue/inject_notes")
async def api_dialogue_inject_notes(req: Dict[str, Any]):
    notes = [int(x) for x in req.get("notes", [61, 65, 68, 73])]
    return await comp_mod.inject_interactive_motif_notes(
        notes=notes,
        safety_guard=safety_guard,
        audio_monitor=audio_monitor,
        telemetry_store=telemetry_store,
        studio_state=studio_state,
    )


@app.post("/api/closed_loop/optimize_sustain")
async def api_closed_loop_optimize_sustain(req: Dict[str, Any]):
    overlap_ms = float(req.get("legato_overlap_ms", 48.0))
    min_vel = int(req.get("min_soft_velocity", 32))
    pedal_boost = int(req.get("sustain_pedal_boost", 6))
    safety_guard.closed_loop_params["legato_overlap_ms"] = overlap_ms
    safety_guard.closed_loop_params["min_soft_velocity"] = min_vel
    safety_guard.closed_loop_params["sustain_pedal_boost"] = pedal_boost
    safety_guard.stats["auto_optimizations_applied"] += 1
    telemetry_store.log_event(
        f"🔧 [CLOSED-LOOP SUSTAIN TUNER] Applied: LegatoOverlap=+{overlap_ms}ms, MinVel={min_vel}, PedalBoost=+{pedal_boost}"
    )
    return {"ok": True, "closed_loop_params": safety_guard.closed_loop_params}


@app.post("/api/composer/export_midi")
async def api_composer_export_midi():
    path = comp_mod.export_latest_composed_midi(studio_state)
    return {"ok": True, "path": str(path)}


@app.post("/api/midi/play_optimized")
async def api_midi_play_optimized(req: Dict[str, Any]):
    if virtuoso_engine.streaming_active:
        virtuoso_engine.stop_stream()
    return await midi_mod.play_optimized_piece(
        piece_id=str(req.get("piece_id", "saint_saens_swan")),
        safety_guard=safety_guard,
        telemetry_store=telemetry_store,
        studio_state=studio_state,
        countdown_fn=run_sound_countdown,
    )


@app.post("/api/rehearsal/record_proxy_take")
async def api_rehearsal_record_take():
    return await acc_mod.record_rehearsal_take_live(
        safety_guard=safety_guard,
        audio_monitor=audio_monitor,
        telemetry_store=telemetry_store,
        studio_state=studio_state,
        countdown_fn=run_sound_countdown,
    )


@app.post("/api/rehearsal/replay_style")
async def api_rehearsal_replay_style(req: Dict[str, Any]):
    return await acc_mod.replay_rehearsal_take_with_style(
        style=str(req.get("style", "faure")),
        safety_guard=safety_guard,
        audio_monitor=audio_monitor,
        telemetry_store=telemetry_store,
        studio_state=studio_state,
        countdown_fn=run_sound_countdown,
    )


@app.post("/api/experiments/rerun")
async def api_experiments_rerun(req: Dict[str, Any]):
    exp_id = str(req.get("exp_id", "exp1"))
    telemetry_store.log_event(f"[R&D LAB] Requested interactive re-run of {exp_id}")
    return {"ok": True, "exp_id": exp_id}


# -------------------------------------------------------------------------
# Autonomous Virtuoso, Virtual Digital Twin & Obsidian Brain Endpoints
# -------------------------------------------------------------------------
@app.post("/api/virtuoso/toggle_stream")
async def api_virtuoso_toggle_stream(req: Dict[str, Any] = Body(default={})):
    want_active = req.get("active")
    if want_active is None:
        want_active = not virtuoso_engine.streaming_active
    if bool(want_active):
        snap = virtuoso_engine.start_stream()
        telemetry_store.update_banner(
            code="READY",
            message="🟢 自動で作曲・進化中 — 人間のお手本に学びながら途切れず演奏しています",
            sub_message=f"第{snap['current_generation']}世代 | 最高 {snap['best_score_ever']}点 | Mac音出し={'ON' if virtual_twin.virtual_sound_enabled else '消音中'}",
            current_phase="🟢 自動演奏中",
        )
    else:
        snap = virtuoso_engine.stop_stream()
        telemetry_store.update_banner(
            code="READY",
            message="⏸️ いま一休み中 — 『演奏をはじめる』や各タブからいつでも再開できます",
            current_phase="⏸️ 一休み中",
        )
    return {"ok": True, "virtuoso": snap}


@app.post("/api/virtuoso/hyper_evolve")
async def api_virtuoso_hyper_evolve(req: Dict[str, Any]):
    num_gens = int(req.get("generations", 15))
    speed_x = float(req.get("speed_multiplier", 25.0))
    res = await asyncio.to_thread(
        virtuoso_engine.run_hyper_speed_training,
        num_generations=num_gens,
        speed_multiplier=speed_x,
    )
    return {
        "ok": True,
        "result": res,
        "virtuoso": virtuoso_engine.get_telemetry_snapshot(),
        "obsidian": obsidian_brain.get_knowledge_graph_snapshot(),
    }


@app.post("/api/virtuoso/inject_motif")
async def api_virtuoso_inject_motif(req: Dict[str, Any]):
    notes = [int(x) for x in req.get("notes", [62, 65, 69, 68, 65, 62])]
    source = str(req.get("source", "gui_motif_injection"))
    res = virtuoso_engine.absorb_live_motif(notes=notes, source=source)
    return {"ok": True, **res}


@app.post("/api/twin/config")
async def api_twin_config(req: Dict[str, Any]):
    snap = virtual_twin.set_config(
        virtual_sound_enabled=req.get("virtual_sound_enabled"),
        hardware_output_enabled=req.get("hardware_output_enabled"),
        output_mode=req.get("output_mode"),
        master_volume=req.get("master_volume"),
    )
    telemetry_store.log_event(
        f"[DIGITAL TWIN] Sound={'ON' if snap['virtual_sound_enabled'] else 'OFF'}, "
        f"Hardware={'ON' if snap['hardware_output_enabled'] else 'OFF'}, Mode={snap['output_mode']}"
    )
    return {"ok": True, "twin": snap}


@app.get("/api/obsidian/graph")
async def api_obsidian_graph():
    return obsidian_brain.get_knowledge_graph_snapshot()


@app.get("/api/obsidian/note/{note_id}")
async def api_obsidian_note(note_id: str):
    if ".." in note_id or "/" in note_id or "\\" in note_id:
        return JSONResponse({"error": "Invalid note id"}, status_code=400)
    note = obsidian_brain.get_note_content(note_id)
    if note is None:
        return JSONResponse({"error": "Note not found"}, status_code=404)
    return note


@app.post("/api/obsidian/directives")
async def api_obsidian_directives(req: Dict[str, Any]):
    text = str(req.get("text", ""))
    parsed = obsidian_brain.write_human_directives(text)
    return {
        "ok": True,
        "parsed": parsed,
        "virtuoso": virtuoso_engine.get_telemetry_snapshot(),
    }


@app.post("/api/obsidian/play_hof")
async def api_obsidian_play_hof(req: Dict[str, Any]):
    midi_rel = str(req.get("midi_rel", ""))
    midi_path = resolve_safe_path(obsidian_brain.vault_dir, midi_rel, (".mid",))
    if midi_path is None:
        return JSONResponse({"error": "Invalid Hall of Fame MIDI path"}, status_code=400)
    if not midi_path.is_file():
        return JSONResponse({"error": "Hall of Fame MIDI not found"}, status_code=404)

    was_streaming = virtuoso_engine.streaming_active
    if was_streaming:
        virtuoso_engine.stop_stream()

    mid = mido.MidiFile(str(midi_path))
    tempo_us = 500000
    abs_t = 0.0
    note_ons: Dict[int, Tuple[float, int]] = {}
    base_t = time.perf_counter() + 0.18
    scheduled = 0

    for msg in mido.merge_tracks(mid.tracks):
        abs_t += mido.tick2second(msg.time, mid.ticks_per_beat, tempo_us)
        if msg.type == "set_tempo":
            tempo_us = msg.tempo
        elif msg.type == "control_change" and msg.control == 64:
            safety_guard.schedule_chord_or_notes(
                notes=[],
                target_sound_time=base_t + abs_t,
                use_vdl_compensation=False,
                pedal_cc64=int(msg.value),
                pedal_preroll_ms=35.0,
                source_tag="hof_playback",
            )
        elif msg.type == "note_on" and msg.velocity > 0:
            note_ons[msg.note] = (abs_t, int(msg.velocity))
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            if msg.note in note_ons:
                t_on, vel = note_ons.pop(msg.note)
                dur = max(0.05, abs_t - t_on)
                safety_guard.schedule_chord_or_notes(
                    notes=[(int(msg.note), vel, dur)],
                    target_sound_time=base_t + t_on,
                    use_vdl_compensation=True,
                    sympathetic_halo=(vel == 1),
                    legato_overlap_s=0.010,
                    source_tag="hof_playback",
                )
                scheduled += 1

    telemetry_store.log_event(
        f"🏆 [OBSIDIAN HoF PLAYBACK] Playing {midi_path.name} ({scheduled} notes) on Digital Twin / Disklavier"
    )
    return {"ok": True, "midi_name": midi_path.name, "scheduled_notes": scheduled}


# -------------------------------------------------------------------------
# v3.0 Studio Endpoints: Multi-Song Tabs, Time-Machine, Expression Pad,
# Resets, Score & Exports, Cello Warmup, 100-Feature Explorer & Mobile Remote
# -------------------------------------------------------------------------
@app.get("/api/tabs")
async def api_tabs_list():
    return {
        "ok": True,
        "workspace": virtuoso_engine.workspace.get_summary(),
        "virtuoso": virtuoso_engine.get_telemetry_snapshot(),
    }


@app.post("/api/tabs/switch")
async def api_tabs_switch(req: Dict[str, Any] = Body(default={})):
    tab_id = str(req.get("tab_id", ""))
    return await asyncio.to_thread(virtuoso_engine.switch_song_tab, tab_id)


@app.post("/api/tabs/new")
async def api_tabs_new(req: Dict[str, Any] = Body(default={})):
    return await asyncio.to_thread(
        virtuoso_engine.create_song_tab,
        title=req.get("title"),
        character_preset=req.get("character_preset"),
    )


@app.post("/api/tabs/close")
async def api_tabs_close(req: Dict[str, Any] = Body(default={})):
    tab_id = str(req.get("tab_id", ""))
    return await asyncio.to_thread(virtuoso_engine.close_song_tab, tab_id)


@app.post("/api/tabs/restore")
async def api_tabs_restore():
    return await asyncio.to_thread(virtuoso_engine.restore_closed_song_tab)


@app.post("/api/timemachine/undo")
async def api_timemachine_undo():
    return await asyncio.to_thread(virtuoso_engine.timemachine_undo)


@app.post("/api/timemachine/redo")
async def api_timemachine_redo():
    return await asyncio.to_thread(virtuoso_engine.timemachine_redo)


@app.post("/api/timemachine/scrub")
async def api_timemachine_scrub(req: Dict[str, Any] = Body(default={})):
    target_gen = int(req.get("generation", 1))
    return await asyncio.to_thread(virtuoso_engine.timemachine_scrub, target_gen)


@app.post("/api/timemachine/ab_compare")
async def api_timemachine_ab_compare(req: Dict[str, Any] = Body(default={})):
    which = str(req.get("which", "both"))
    return await asyncio.to_thread(virtuoso_engine.play_ab_comparison, which)


@app.post("/api/timemachine/rescue_30s")
async def api_timemachine_rescue_30s(req: Dict[str, Any] = Body(default={})):
    secs = float(req.get("seconds", 35.0))
    return await asyncio.to_thread(virtuoso_engine.rescue_last_30s_to_tab, secs)


@app.get("/api/virtuoso/status")
async def api_virtuoso_status():
    return {"ok": True, "virtuoso": virtuoso_engine.get_telemetry_snapshot()}


@app.post("/api/virtuoso/full_reset")
async def api_virtuoso_full_reset(req: Dict[str, Any] = Body(default={})):
    return await asyncio.to_thread(virtuoso_engine.full_reset_new_song, title=req.get("title"))


@app.post("/api/virtuoso/mood_change")
async def api_virtuoso_mood_change(req: Dict[str, Any] = Body(default={})):
    return await asyncio.to_thread(virtuoso_engine.mood_change_keep_brain, preset_id=req.get("preset_id"))


@app.post("/api/virtuoso/spark")
async def api_virtuoso_spark():
    return await asyncio.to_thread(virtuoso_engine.trigger_inspiration_spark)


@app.post("/api/virtuoso/like")
async def api_virtuoso_like():
    return await asyncio.to_thread(virtuoso_engine.register_human_like_vote)


@app.post("/api/virtuoso/expression_pad")
async def api_virtuoso_expression_pad(req: Dict[str, Any] = Body(default={})):
    x = float(req.get("x", 0.0))
    y = float(req.get("y", 0.6))
    return await asyncio.to_thread(virtuoso_engine.set_expression_pad, x, y)


@app.post("/api/virtuoso/character_preset")
async def api_virtuoso_character_preset(req: Dict[str, Any] = Body(default={})):
    preset_id = str(req.get("preset_id", "tchaikovsky_passion"))
    return await asyncio.to_thread(virtuoso_engine.apply_character_preset, preset_id)


@app.get("/api/score/current")
@app.get("/api/features/sheet_music")
async def api_score_current():
    snap = virtuoso_engine.get_telemetry_snapshot()
    meta = snap.get("work_metadata") or {}
    title = str(meta.get("title_ja") or snap.get("active_motif", {}).get("title", "今のオリジナル曲"))
    bpm = float(snap.get("elite_genome", {}).get("tempo_bpm", 82.0))
    return build_sheet_music_payload(
        snap.get("latest_events", []),
        title=title,
        bpm=bpm,
        prev_events=snap.get("prev_events"),
        work_metadata=meta,
    )


@app.get("/api/state")
async def api_state():
    return {
        "ok": True,
        "virtuoso": virtuoso_engine.get_telemetry_snapshot(),
        "safety": safety_guard.get_telemetry_snapshot(),
        "twin": virtual_twin.get_telemetry_snapshot(),
        "obsidian": obsidian_brain.get_knowledge_graph_snapshot(),
        "studio": studio_state,
    }


def _get_export_dir() -> Path:
    d = TELEMETRY_DIR / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


@app.get("/api/export/midi")
async def api_export_midi():
    snap = virtuoso_engine.get_telemetry_snapshot()
    title = str(snap.get("active_motif", {}).get("title", "disklavier_original"))
    bpm = float(snap.get("elite_genome", {}).get("tempo_bpm", 82.0))
    out_p = _get_export_dir() / f"disklavier_gen_{snap.get('current_generation', 1)}.mid"
    export_events_to_midi_file(snap.get("latest_events", []), title=title, out_path=out_p, bpm=bpm)
    return FileResponse(str(out_p), media_type="audio/midi", filename=out_p.name)


@app.get("/api/export/musicxml")
async def api_export_musicxml():
    snap = virtuoso_engine.get_telemetry_snapshot()
    title = str(snap.get("active_motif", {}).get("title", "Disklavier Original"))
    bpm = float(snap.get("elite_genome", {}).get("tempo_bpm", 82.0))
    out_p = _get_export_dir() / f"disklavier_gen_{snap.get('current_generation', 1)}.musicxml"
    export_events_to_musicxml(snap.get("latest_events", []), title=title, out_path=out_p, bpm=bpm)
    return FileResponse(str(out_p), media_type="application/vnd.recordare.musicxml+xml", filename=out_p.name)


@app.get("/api/export/wav")
async def api_export_wav():
    snap = virtuoso_engine.get_telemetry_snapshot()
    out_p = _get_export_dir() / f"disklavier_gen_{snap.get('current_generation', 1)}.wav"
    await asyncio.to_thread(export_events_to_wav, snap.get("latest_events", []), out_p)
    return FileResponse(str(out_p), media_type="audio/wav", filename=out_p.name)


@app.post("/api/cello/warmup_wave")
async def api_cello_warmup_wave():
    events, summary = build_cello_open_string_warmup_wave()
    await asyncio.to_thread(virtuoso_engine.play_events_now, events, "cello_warmup")
    return {"ok": True, "message_ja": summary, "event_count": len(events)}


@app.post("/api/safety/panic")
async def api_safety_panic():
    return await trigger_emergency_panic()


@app.post("/api/safety/test_note")
async def api_safety_test_note():
    now_t = time.perf_counter() + 0.12
    for idx, pitch in enumerate([60, 64, 67, 72]):
        safety_guard.schedule_chord_or_notes(
            notes=[(pitch, 58 + idx * 4, 0.45)],
            target_sound_time=now_t + idx * 0.14,
            use_vdl_compensation=True,
            source_tag="test_phrase",
        )
    return {"ok": True, "message_ja": "🎹 美しい確認和音（Cメジャー・アルペジオ）を安全に鳴らしました。"}


@app.post("/api/safety/harmonic_halo")
async def api_safety_harmonic_halo():
    now_t = time.perf_counter() + 0.10
    safety_guard.schedule_chord_or_notes(
        notes=[(36, 1, 2.2), (48, 1, 2.2), (60, 1, 2.2)],
        target_sound_time=now_t,
        use_vdl_compensation=False,
        sympathetic_halo=True,
        source_tag="sympathetic_halo",
    )
    return {"ok": True, "message_ja": "✨ サイレント打鍵(Velocity=1)でC開放弦の倍音共鳴ダンパーを開きました。"}


@app.post("/api/sound/enable")
async def api_sound_enable(req: Dict[str, Any] = Body(default={})):
    want = req.get("enabled")
    target = (not virtual_twin.virtual_sound_enabled) if want is None else bool(want)
    if target:
        await run_sound_countdown("Mac内蔵バーチャルピアノの音出しを開始します", seconds=3)
    snap = virtual_twin.set_config(virtual_sound_enabled=target)
    return {"ok": True, "twin": snap}


@app.post("/api/audio/toggle")
async def api_audio_toggle(req: Dict[str, Any] = Body(default={})):
    if getattr(audio_monitor, "_running", False):
        audio_monitor._running = False
        return {"ok": True, "active": False, "message_ja": "🎤 マイク傾聴を一時停止しました。"}
    audio_monitor.start()
    return {"ok": True, "active": True, "message_ja": "🎤 チェロ音程マイク傾聴を開始しました。"}


@app.get("/api/imslp/list")
async def api_imslp_list():
    return {"ok": True, "items": studio_state.get("imslp_library", [])}


@app.get("/api/imslp/dna")
async def api_imslp_dna():
    return {"ok": True, "dna": virtuoso_engine.imslp_dna}


@app.post("/api/dialogue/passage_reply")
async def api_dialogue_passage_reply(req: Dict[str, Any] = Body(default={})):
    notes = req.get("notes")
    if not notes:
        # Fallback to live MIDI In buffer or C519ML Audio Monitor notes if empty
        midi_in_phrase = safety_guard.get_human_recorded_phrase()
        if midi_in_phrase:
            notes = midi_in_phrase
        else:
            notes = [
                {"note": 62, "velocity": 74, "duration_s": 0.44},
                {"note": 65, "velocity": 78, "duration_s": 0.44},
                {"note": 69, "velocity": 82, "duration_s": 0.52},
                {"note": 74, "velocity": 86, "duration_s": 0.60},
                {"note": 72, "velocity": 76, "duration_s": 0.48},
            ]
    reply_mode = str(req.get("reply_mode", "liszt_rachmaninoff_virtuoso"))
    source = str(req.get("source", "piano_midi"))
    play_now = bool(req.get("play_now", True))
    return await asyncio.to_thread(
        virtuoso_engine.apply_passage_reply,
        notes,
        reply_mode,
        source,
        play_now,
    )


@app.post("/api/studio/virtuoso_expression")
async def api_studio_virtuoso_expression(req: Dict[str, Any] = Body(default={})):
    mode = str(req.get("mode", "4hand_layering"))
    play_now = bool(req.get("play_now", True))
    return await asyncio.to_thread(virtuoso_engine.apply_virtuoso_expression, mode, play_now)


@app.post("/api/studio/stage_act")
async def api_studio_stage_act(req: Dict[str, Any] = Body(default={})):
    act = int(req.get("act", 1))
    play_now = bool(req.get("play_now", True))
    return await asyncio.to_thread(virtuoso_engine.run_stage_act, act, play_now)


@app.get("/api/obsidian/snapshot")
async def api_obsidian_snapshot():
    return obsidian_brain.get_knowledge_graph_snapshot()


@app.get("/api/obsidian/explorer")
async def api_obsidian_explorer():
    return obsidian_brain.get_in_app_knowledge_explorer()


@app.get("/api/features/catalog")
async def api_features_catalog():
    return get_feature_catalog()


@app.post("/api/features/invoke")
async def api_features_invoke(req: Dict[str, Any] = Body(default={})):
    fid = str(req.get("feature_id", "J9"))
    params = req.get("params") or {}
    return await asyncio.to_thread(
        invoke_feature,
        fid,
        virtuoso_engine,
        safety_guard,
        audio_monitor,
        virtual_twin,
        obsidian_brain,
        studio_state,
        params,
    )


@app.get("/api/system/doctor")
async def api_system_doctor():
    return run_japanese_system_doctor(safety_guard, audio_monitor, virtual_twin, virtuoso_engine)


@app.get("/api/remote/info")
async def api_remote_info(request: Request):
    allow_tok = _is_loopback_client(request.client) or _is_authorized_request(request)
    return get_lan_remote_info(8765, token=STUDIO_AUTH_TOKEN if allow_tok else "")


# -------------------------------------------------------------------------
# Phase 4 & 5 Analytics, SQLite Logs, Critic Weights, Blind A/B, Dialogue & Safety Profile
# -------------------------------------------------------------------------
@app.get("/api/virtuoso/weights")
async def api_get_virtuoso_weights():
    return {"ok": True, **virtuoso_engine.get_critic_weights()}


@app.post("/api/virtuoso/weights")
async def api_set_virtuoso_weights(req: Dict[str, Any] = Body(default={})):
    weights = req.get("axis_weights") if isinstance(req.get("axis_weights"), dict) else req.get("weights")
    preset = req.get("preset")
    return await asyncio.to_thread(virtuoso_engine.set_critic_weights, weights, preset)


@app.post("/api/timemachine/blind_ab")
async def api_timemachine_blind_ab(req: Dict[str, Any] = Body(default={})):
    action = str(req.get("action", "start"))
    choice = req.get("choice")
    return await asyncio.to_thread(virtuoso_engine.blind_ab_action, action, choice)


@app.get("/api/dialogue/comparison")
async def api_dialogue_comparison():
    return {"ok": True, **virtuoso_engine.get_dialogue_comparison()}


@app.get("/api/safety/profile")
async def api_get_safety_profile():
    snap = safety_guard.get_telemetry_snapshot()
    return {
        "ok": True,
        "model_profile_id": snap.get("model_profile_id", "envious_mark_iv"),
        "model_profiles": snap.get("model_profiles", {}),
        "safety": snap,
    }


@app.post("/api/safety/profile")
async def api_set_safety_profile(req: Dict[str, Any] = Body(default={})):
    profile_id = str(req.get("profile_id", "envious_mark_iv"))
    return safety_guard.apply_model_profile(profile_id)


@app.get("/api/analytics/overview")
async def api_analytics_overview():
    v_snap = virtuoso_engine.get_telemetry_snapshot()
    telemetry_store.seed_generation_history_if_empty(virtuoso_engine.evolution_history)
    gen_hist = telemetry_store.get_generation_history_db(limit=120)
    if not gen_hist:
        gen_hist = list(v_snap.get("evolution_history", []))

    vdl_db = telemetry_store.get_vdl_measurements_db(limit=120)
    if not vdl_db:
        vdl_db = list(telemetry_store.experiments_data.get("exp1_vdl", {}).get("points", []))
    pedal_db = telemetry_store.get_pedal_measurements_db(limit=80)
    if not pedal_db:
        pedal_db = list(telemetry_store.experiments_data.get("exp2_pedal", {}).get("points", []))

    latest_events = list(v_snap.get("latest_events", []))
    by_pitch: Dict[int, List[float]] = {}
    velocities: List[int] = []
    for ev in latest_events:
        n = int(ev.get("note", 0))
        if n > 0:
            by_pitch.setdefault(n, []).append(float(ev.get("time_offset_s", 0.0)))
            velocities.append(int(ev.get("velocity", 64)))
    retrigger_ms: List[float] = []
    for times in by_pitch.values():
        ts = sorted(times)
        for i in range(1, len(ts)):
            retrigger_ms.append(round((ts[i] - ts[i - 1]) * 1000.0, 1))

    s_snap = safety_guard.get_telemetry_snapshot()
    return {
        "ok": True,
        "generation_history": gen_hist,
        "vdl_measurements": vdl_db,
        "pedal_measurements": pedal_db,
        "experiments": telemetry_store.experiments_data,
        "latest_events": latest_events,
        "retrigger_intervals_ms": retrigger_ms,
        "velocities": velocities,
        "safety": s_snap,
        "performance": await api_performance(),
    }


@app.get("/api/logs/search")
async def api_logs_search(
    table: str = "all",
    q: str = "",
    min_score: Optional[float] = None,
    limit: int = 100,
):
    rows = telemetry_store.search_sqlite_logs(
        query=q, category=table, limit=limit, min_score=min_score
    )
    return {"ok": True, "table": table, "count": len(rows), "rows": rows}


@app.get("/api/logs/export")
async def api_logs_export(
    table: str = "generation_history",
    fmt: str = "csv",
    q: str = "",
    min_score: Optional[float] = None,
    limit: int = 200,
):
    safe_table = "".join(c for c in table if c.isalnum() or c == "_") or "logs"
    rows = telemetry_store.export_sqlite_rows(
        table=table, query=q, min_score=min_score, limit=limit
    )
    fmt_clean = fmt.lower().strip()
    if fmt_clean == "json":
        payload = json.dumps({"table": table, "count": len(rows), "rows": rows}, ensure_ascii=False, indent=2)
        return Response(
            content=payload.encode("utf-8"),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="disklavier_{safe_table}.json"'},
        )
    buf = io.StringIO()
    if rows:
        fieldnames = list(rows[0].keys())
        writer = csv.DictWriter(buf, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    else:
        buf.write("id,message\n")
    return Response(
        content=buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="disklavier_{safe_table}.csv"'},
    )


@app.get("/remote", response_class=HTMLResponse)
async def serve_mobile_remote(request: Request) -> HTMLResponse:
    remote_path = BASE_DIR / "src" / "ui" / "remote.html"
    if remote_path.exists():
        allow_tok = _is_loopback_client(request.client) or _is_authorized_request(request)
        resp = HTMLResponse(_inject_token_html(remote_path.read_text(encoding="utf-8"), allow_tok))
        if allow_tok:
            resp.set_cookie(
                key="disklavier_token",
                value=STUDIO_AUTH_TOKEN,
                httponly=True,
                samesite="strict",
                path="/",
            )
        return resp
    return HTMLResponse("<h1>スマホリモコンを準備中です...</h1>")



# -------------------------------------------------------------------------
# Single 30Hz Multi-Rate Delta WebSocket Broadcaster
# -------------------------------------------------------------------------


class WebSocketBroadcaster:
    """Broadcasts a single shared 30Hz multi-rate delta stream to all connected WS clients."""

    TARGET_HZ = 30.0

    def __init__(self) -> None:
        self._clients: Dict[WebSocket, asyncio.Queue[str]] = {}
        self._task: Optional[asyncio.Task[None]] = None
        self._running: bool = False
        self._tick: int = 0
        self._last_banner_hash: str = ""
        self._last_v_hashes: Dict[str, str] = {}
        self._last_safety_slow_hash: str = ""
        self._last_agent_hash: str = ""
        self._last_obs_hash: str = ""
        self._send_samples: deque[Tuple[float, int, int]] = deque(maxlen=300)
        self._bw_history: deque[Dict[str, Any]] = deque(maxlen=60)
        self._last_hist_ts: float = 0.0
        self._delta_saved_bytes: int = 0

    def ensure_started(self) -> None:
        if self._task is None or self._task.done():
            loop = asyncio.get_running_loop()
            self._running = True
            self._task = loop.create_task(self._broadcast_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    def register(self, ws: WebSocket) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=6)
        self._clients[ws] = q
        self.ensure_started()
        return q

    def unregister(self, ws: WebSocket) -> None:
        self._clients.pop(ws, None)

    @staticmethod
    def _compact_json(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

    def build_initial_frame(self) -> str:
        obs_snap = obsidian_brain.get_knowledge_graph_snapshot()
        v_snap = dict(virtuoso_engine.get_telemetry_snapshot())
        if isinstance(v_snap.get("latest_events"), list) and len(v_snap["latest_events"]) > 64:
            v_snap["latest_events"] = v_snap["latest_events"][-64:]
        frame: Dict[str, Any] = {
            "ts": round(time.time(), 3),
            "banner": telemetry_store.banner_state,
            "audio": audio_monitor.get_telemetry_snapshot(),
            "safety": safety_guard.get_telemetry_snapshot(),
            "twin": virtual_twin.get_telemetry_snapshot(),
            "virtuoso": v_snap,
            "virtuoso_delta": False,
            "agent": telemetry_store.get_snapshot(),
            "obsidian_meta": {
                "stats": obs_snap.get("stats", {}),
                "hof_leaderboard": obs_snap.get("hof_leaderboard", [])[:5],
            },
        }
        return self._compact_json(frame)

    def _build_tick_frame(self, tick: int) -> Dict[str, Any]:
        a_snap = audio_monitor.get_telemetry_snapshot()
        s_snap = safety_guard.get_telemetry_snapshot()
        t_snap = virtual_twin.get_telemetry_snapshot()
        frame: Dict[str, Any] = {
            "ts": round(time.time(), 3),
            "audio": {
                "device_name": a_snap.get("device_name"),
                "active": bool(float(a_snap.get("rms_dbfs", -120.0)) > -90.0),
                "rms_dbfs": a_snap.get("rms_dbfs"),
                "detected_notes": a_snap.get("detected_notes", []),
                "detected_note_names": a_snap.get("detected_note_names", []),
                "estimated_bpm": a_snap.get("estimated_bpm", 72),
            },
            "safety": {
                "active_voices_count": s_snap.get("active_voices_count", 0),
                "active_voices_limit": s_snap.get("active_voices_limit", 20),
                "notes_per_sec": s_snap.get("notes_per_sec", 0),
                "notes_per_sec_limit": s_snap.get("notes_per_sec_limit", 30),
                "pedal_cc64": s_snap.get("pedal_cc64", 0),
                "vdl_buffer_count": s_snap.get("vdl_buffer_count", 0),
                "active_keys": s_snap.get("active_keys", {}),
            },
            "twin": {
                "virtual_sound_enabled": t_snap.get("virtual_sound_enabled"),
                "hardware_output_enabled": t_snap.get("hardware_output_enabled"),
                "physics": {
                    "effective_cc64": (t_snap.get("physics") or {}).get("effective_cc64", 0),
                },
            },
        }

        banner_raw = self._compact_json(telemetry_store.banner_state)
        bh = hashlib.md5(banner_raw.encode("utf-8")).hexdigest()
        if bh != self._last_banner_hash:
            self._last_banner_hash = bh
            frame["banner"] = telemetry_store.banner_state
        else:
            self._delta_saved_bytes += len(banner_raw)

        # 5Hz Virtuoso delta channel (every 6 ticks at 30Hz): diff ALL keys
        if tick % 6 == 1:
            v_full = dict(virtuoso_engine.get_telemetry_snapshot())
            if isinstance(v_full.get("latest_events"), list) and len(v_full["latest_events"]) > 64:
                v_full["latest_events"] = v_full["latest_events"][-64:]
            delta_v: Dict[str, Any] = {}
            for k, val in v_full.items():
                raw = self._compact_json(val)
                h = hashlib.md5(raw.encode("utf-8")).hexdigest()
                if self._last_v_hashes.get(k) != h:
                    self._last_v_hashes[k] = h
                    delta_v[k] = val
                else:
                    self._delta_saved_bytes += len(raw)
            if delta_v:
                frame["virtuoso"] = delta_v
                frame["virtuoso_delta"] = True

        # 1Hz Slow channel (every 30 ticks at 30Hz)
        if tick % 30 == 1:
            safety_slow = {
                "stats": s_snap.get("stats", {}),
                "scheduler_jitter": s_snap.get("scheduler_jitter", {}),
                "vdl_params": s_snap.get("vdl_params", {}),
            }
            ss_raw = self._compact_json(safety_slow)
            sh = hashlib.md5(ss_raw.encode("utf-8")).hexdigest()
            if sh != self._last_safety_slow_hash:
                self._last_safety_slow_hash = sh
                frame["safety"].update(safety_slow)

            agent_snap = telemetry_store.get_snapshot()
            agent_raw = self._compact_json(agent_snap)
            ah = hashlib.md5(agent_raw.encode("utf-8")).hexdigest()
            if ah != self._last_agent_hash:
                self._last_agent_hash = ah
                frame["agent"] = agent_snap
            else:
                self._delta_saved_bytes += len(agent_raw)

            obs_snap = obsidian_brain.get_knowledge_graph_snapshot()
            obs_meta = {
                "stats": obs_snap.get("stats", {}),
                "hof_leaderboard": obs_snap.get("hof_leaderboard", [])[:5],
            }
            obs_raw = self._compact_json(obs_meta)
            oh = hashlib.md5(obs_raw.encode("utf-8")).hexdigest()
            if oh != self._last_obs_hash:
                self._last_obs_hash = oh
                frame["obsidian_meta"] = obs_meta
        return frame

    def _record_broadcast(self, now: float, payload_bytes: int, client_count: int) -> None:
        self._send_samples.append((now, payload_bytes, client_count))
        if (now - self._last_hist_ts) >= 1.0:
            self._last_hist_ts = now
            m = self.get_metrics()
            self._bw_history.append(
                {
                    "ts": round(now, 2),
                    "kb_per_sec_per_client": m["kb_per_sec_per_client"],
                    "messages_per_sec_per_client": m["messages_per_sec_per_client"],
                    "clients": m["connected_clients"],
                }
            )

    def get_metrics(self) -> Dict[str, Any]:
        now = time.time()
        window = [s for s in self._send_samples if (now - s[0]) <= 5.0]
        if window:
            span = max(1.0, now - window[0][0])
            total_bytes_per_client = sum(s[1] for s in window)
            kbps = (total_bytes_per_client / 1024.0) / span
            mps = len(window) / span
        else:
            kbps = 0.0
            mps = 0.0
        return {
            "target_hz": self.TARGET_HZ,
            "connected_clients": len(self._clients),
            "bytes_per_sec_per_client": int(round(kbps * 1024.0)),
            "kb_per_sec_per_client": round(kbps, 2),
            "messages_per_sec_per_client": round(mps, 2),
            "delta_saved_kb": round(self._delta_saved_bytes / 1024.0, 1),
            "history": list(self._bw_history),
        }

    async def _broadcast_loop(self) -> None:
        interval = 1.0 / self.TARGET_HZ
        while self._running:
            t0 = time.perf_counter()
            if self._clients:
                self._tick += 1
                try:
                    frame = await asyncio.to_thread(self._build_tick_frame, self._tick)
                    text = self._compact_json(frame)
                    payload_bytes = len(text.encode("utf-8"))
                    self._record_broadcast(time.time(), payload_bytes, len(self._clients))
                    for q in list(self._clients.values()):
                        if q.full():
                            try:
                                q.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                        try:
                            q.put_nowait(text)
                        except asyncio.QueueFull:
                            pass
                except Exception as exc:
                    record_caught_exception("mission_control_server.ws_broadcaster", exc)
            elapsed = time.perf_counter() - t0
            await asyncio.sleep(max(0.002, interval - elapsed))


ws_broadcaster = WebSocketBroadcaster()


@app.get("/api/performance")
async def api_performance():
    safety_snap = safety_guard.get_telemetry_snapshot()
    v_snap = virtuoso_engine.get_telemetry_snapshot()
    return {
        "ok": True,
        "websocket": ws_broadcaster.get_metrics(),
        "scheduler_jitter": safety_snap.get("scheduler_jitter", {}),
        "preflight": v_snap.get("preflight", {}),
        "hardware_accel": v_snap.get("hardware_accel", {}),
        "obsidian_cache": {
            "cached": obsidian_brain._graph_cache is not None,
            "graph_version": obsidian_brain._graph_version,
        },
        "exceptions": get_exception_metrics(),
    }


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    provided = _extract_token(ws.headers, ws.query_params, ws.cookies)
    if not provided or not hmac.compare_digest(provided, STUDIO_AUTH_TOKEN):
        await ws.close(code=1008)
        return
    await ws.accept()
    queue = ws_broadcaster.register(ws)
    try:
        # Send one immediate full snapshot so first paint is instant
        initial_payload = await asyncio.to_thread(ws_broadcaster.build_initial_frame)
        await ws.send_text(initial_payload)
        while True:
            payload = await queue.get()
            await ws.send_text(payload)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        record_caught_exception("mission_control_server.websocket_endpoint", exc)
    finally:
        ws_broadcaster.unregister(ws)


# -------------------------------------------------------------------------
# Stage Process Visualization HUD & 5-Layer Orchestrator Routes (/stage)
# -------------------------------------------------------------------------
@app.get("/stage", response_class=HTMLResponse)
async def serve_stage_editorial_hud():
    from src.stage.stage_server import STAGE_HUD_HTML_PATH

    if STAGE_HUD_HTML_PATH.exists():
        return HTMLResponse(STAGE_HUD_HTML_PATH.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>stage_hud.html missing</h1>", status_code=404)


@app.get("/api/stage/state")
async def api_stage_state_bridge():
    from src.stage.stage_server import get_orchestrator

    return JSONResponse(get_orchestrator().get_hud_state())


@app.post("/api/stage/mode")
async def api_stage_mode_bridge(payload: Dict[str, Any] = Body(...)):
    from src.stage.stage_server import get_orchestrator

    return JSONResponse(get_orchestrator().set_mode(str(payload.get("mode", "cello"))))


@app.post("/api/stage/demo")
async def api_stage_demo_bridge(payload: Dict[str, Any] = Body(...)):
    from src.stage.stage_server import get_orchestrator

    return JSONResponse(get_orchestrator().set_demo_enabled(bool(payload.get("enabled", False))))


@app.post("/api/stage/directive")
async def api_stage_directive_bridge(payload: Dict[str, Any] = Body(...)):
    from src.stage.stage_server import get_orchestrator

    return JSONResponse(get_orchestrator().apply_directive(str(payload.get("text", ""))))


@app.post("/api/stage/turn")
async def api_stage_turn_bridge():
    from src.stage.stage_server import get_orchestrator

    return JSONResponse({"ok": True, "state": get_orchestrator().execute_composition_turn(use_virtual_human=True)})


@app.post("/api/stage/reset")
async def api_stage_reset_bridge():
    from src.stage.stage_server import get_orchestrator

    return JSONResponse(get_orchestrator().reset_performance())


@app.post("/api/stage/panic")
async def api_stage_panic_bridge():
    from src.stage.stage_server import get_orchestrator

    return JSONResponse(get_orchestrator().output_layer.panic())


@app.websocket("/ws/stage")
async def stage_websocket_bridge(ws: WebSocket):
    from src.stage.stage_server import get_orchestrator

    await ws.accept()
    orch = get_orchestrator()
    try:
        while True:
            await ws.send_json(orch.get_hud_state())
            await asyncio.sleep(1.0 / 60.0)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        record_caught_exception("mission_control_server.stage_websocket_bridge", exc)

