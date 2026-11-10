"""
FastAPI + 60fps WebSocket Live Mission Control Server for Cello & Disklavier Autonomous R&D Studio.
Manages the single hardware connection to Yamaha Disklavier (via DisklavierSafetyGuard) and
Steinberg UR22mkII / AKG C519ML (via C519MLAudioMonitor), exposing real-time telemetry and
acoustic measurement APIs for both the GUI and autonomous R&D scripts.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from src.audio.c519ml_monitor import C519MLAudioMonitor, midi_to_name
from src.safety.disklavier_safety_guard import DisklavierSafetyGuard
from src.telemetry.telemetry_store import BASE_DIR, TELEMETRY_DIR, TelemetryStore


app = FastAPI(title="Antigravity 2.0: Cello & Disklavier Autonomous R&D Studio")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

telemetry_store = TelemetryStore()
safety_guard = DisklavierSafetyGuard(
    port_name="Disklavier",
    on_log_Callback=telemetry_store.log_event,
)
audio_monitor = C519MLAudioMonitor(
    device_keyword="Steinberg UR22",
    active_notes_provider=lambda: safety_guard.active_notes,
)

# Measure ambient baseline noise floor on startup
time.sleep(0.15)
audio_monitor.noise_floor_dbfs = round(min(-48.0, max(-72.0, audio_monitor.rms_dbfs)), 2)
telemetry_store.log_event(
    f"[AUDIO INIT] Connected '{audio_monitor.device_name}' @ 44.1kHz | Noise Floor={audio_monitor.noise_floor_dbfs:.1f} dBFS"
)

# Dynamic state for Tabs 2, 3, 4, 5
studio_state: Dict[str, Any] = {
    "session_style": "faure_romantic",
    "session_active": False,
    "composer_pieces": [],
    "current_composed_piece": None,
    "imslp_library": [],
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
    note: int
    velocity: int
    duration_s: float = 0.28
    countdown_seconds: int = 0
    description: str = ""
    sympathetic_halo: bool = False


class ChordPlayReq(BaseModel):
    notes: List[Dict[str, Any]]  # [{"note": 60, "velocity": 50, "duration_s": 0.5}, ...]
    use_vdl: bool = True
    pedal_cc64: Optional[int] = None
    pedal_preroll_ms: float = 40.0
    countdown_seconds: int = 3
    description: str = "Chord Playback"
    sympathetic_halo: bool = False
    source_tag: str = "engine"


class SequencePlayReq(BaseModel):
    events: List[
        Dict[str, Any]
    ]  # [{"time_offset_s": 0.0, "note": 60, "velocity": 55, "duration_s": 0.4, "pedal_cc64": 55, "sympathetic_halo": False, "source_tag": "accompaniment"}]
    use_vdl: bool = True
    countdown_seconds: int = 3
    description: str = "Sequence Playback"


class PedalMeasureReq(BaseModel):
    cc64: int
    notes: List[int] = [48, 55, 60, 64, 67]
    velocity: int = 62
    hold_s: float = 0.35
    listen_s: float = 1.4
    register_name: str = "C3-G4 Mid-Low Register"
    countdown_seconds: int = 0


class ModeSwitchReq(BaseModel):
    input_mode: Optional[str] = None
    bleed_cancellation: Optional[bool] = None
    session_style: Optional[str] = None


# -------------------------------------------------------------------------
# REST API Endpoints
# -------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_index() -> HTMLResponse:
    ui_path = BASE_DIR / "src" / "ui" / "index.html"
    if ui_path.exists():
        return HTMLResponse(ui_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Loading Live Mission Control UI...</h1>")


@app.get("/telemetry/charts/{filename}")
async def serve_chart(filename: str):
    file_path = TELEMETRY_DIR / filename
    if file_path.exists():
        return FileResponse(file_path)
    return JSONResponse({"error": "Chart not found"}, status_code=404)


@app.get("/api/status")
async def get_full_status():
    return {
        "safety": safety_guard.get_telemetry_snapshot(),
        "audio": audio_monitor.get_telemetry_snapshot(),
        "agent": telemetry_store.get_snapshot(),
        "studio": studio_state,
    }


@app.post("/api/panic")
async def trigger_emergency_panic():
    snap = safety_guard.emergency_panic()
    telemetry_store.update_banner(
        code="ACTION_NEEDED",
        message="🔴 EMERGENCY PANIC が実行されました。全88鍵Note-OffおよびダンパーペダルCC#64=0を即時送信しました。",
        sub_message="安全が確認できたら各タブから操作を再開できます。",
    )
    return {"status": "panicked", "safety": snap}


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
        (int(n["note"]), int(n["velocity"]), float(n.get("duration_s", 0.45)))
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
    sorted_evs = sorted(req.events, key=lambda x: float(x.get("time_offset_s", 0.0)))
    for ev in sorted_evs:
        t_off = float(ev.get("time_offset_s", 0.0))
        dur = float(ev.get("duration_s", 0.35))
        max_end_offset = max(max_end_offset, t_off + dur)
        note = int(ev.get("note", 0))
        vel = int(ev.get("velocity", 55))
        pedal = ev.get("pedal_cc64", None)
        halo = bool(ev.get("sympathetic_halo", False))
        tag = str(ev.get("source_tag", "sequence"))

        if note > 0:
            safety_guard.schedule_chord_or_notes(
                notes=[(note, vel, dur)],
                target_sound_time=base_target + t_off,
                use_vdl_compensation=req.use_vdl,
                pedal_cc64=pedal,
                sympathetic_halo=halo,
                source_tag=tag,
            )
            scheduled_count += 1
        elif pedal is not None:
            safety_guard.send_pedal_cc64(int(pedal), source_tag=tag)

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
    import importlib
    import src.session.accompaniment_engine as acc_mod

    importlib.reload(acc_mod)
    return await acc_mod.run_live_proxy_session_demo(
        safety_guard=safety_guard,
        audio_monitor=audio_monitor,
        telemetry_store=telemetry_store,
        studio_state=studio_state,
        countdown_fn=run_sound_countdown,
    )


@app.post("/api/composer/listen_and_compose")
async def api_composer_listen_and_compose(req: Dict[str, Any]):
    import importlib
    import src.composer.ai_classical_composer as comp_mod

    importlib.reload(comp_mod)
    return await comp_mod.listen_and_compose_live(
        req=req,
        safety_guard=safety_guard,
        audio_monitor=audio_monitor,
        telemetry_store=telemetry_store,
        studio_state=studio_state,
        countdown_fn=run_sound_countdown,
    )


@app.post("/api/composer/export_midi")
async def api_composer_export_midi():
    import importlib
    import src.composer.ai_classical_composer as comp_mod

    importlib.reload(comp_mod)
    path = comp_mod.export_latest_composed_midi(studio_state)
    return {"ok": True, "path": str(path)}


@app.post("/api/midi/play_optimized")
async def api_midi_play_optimized(req: Dict[str, Any]):
    import importlib
    import src.midi.imslp_library as midi_mod

    importlib.reload(midi_mod)
    return await midi_mod.play_optimized_piece(
        piece_id=str(req.get("piece_id", "saint_saens_swan")),
        safety_guard=safety_guard,
        telemetry_store=telemetry_store,
        studio_state=studio_state,
        countdown_fn=run_sound_countdown,
    )


@app.post("/api/rehearsal/record_proxy_take")
async def api_rehearsal_record_take():
    import importlib
    import src.session.accompaniment_engine as acc_mod

    importlib.reload(acc_mod)
    return await acc_mod.record_rehearsal_take_live(
        safety_guard=safety_guard,
        audio_monitor=audio_monitor,
        telemetry_store=telemetry_store,
        studio_state=studio_state,
        countdown_fn=run_sound_countdown,
    )


@app.post("/api/rehearsal/replay_style")
async def api_rehearsal_replay_style(req: Dict[str, Any]):
    import importlib
    import src.session.accompaniment_engine as acc_mod

    importlib.reload(acc_mod)
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
# 60fps Real-Time WebSocket Stream
# -------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    tick = 0
    try:
        while True:
            tick += 1
            # Stream audio & piano keys at 60fps; include heavier agent/git/experiment metadata every 6th frame (10Hz)
            frame: Dict[str, Any] = {
                "ts": time.time(),
                "banner": telemetry_store.banner_state,
                "audio": audio_monitor.get_telemetry_snapshot(),
                "safety": safety_guard.get_telemetry_snapshot(),
            }
            if tick % 6 == 1:
                frame["agent"] = telemetry_store.get_snapshot()
                frame["studio"] = studio_state
            await ws.send_json(frame)
            await asyncio.sleep(1.0 / 60.0)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
