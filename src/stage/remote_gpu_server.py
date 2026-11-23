"""
Home RTX 3070 Remote GPU Inference Server (`src/stage/remote_gpu_server.py`).

Decouples heavy LLM / NotaGen / 4-candidate parallel composition inference on the
home Windows RTX 3070 PC from the ultra-low-latency venue M1 MacBook Pro (which runs
MIDI I/O, UR22 audio, 30-voice safety guard, and Stage/Studio HUD).

Security:
  - Enforces Bearer token authentication via `DISKLAVIER_API_TOKEN` on all inference
    and health endpoints (`GET /api/v1/health`, `POST /api/v1/infer/deliberate`,
    `WS /ws/v1/stream_compose`).
  - If `DISKLAVIER_API_TOKEN` is unset, logs an explicit security warning and generates
    a temporary local token (written to `data/telemetry/remote_gpu_token.txt`).
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.stage.co_composition_architect import CoCompositionArchitect
from src.stage.config_scanner import DEFAULT_CONFIG_PATH, scan_ollama_environment
from src.stage.corpus_phrase_model import CorpusPhraseModelManager
from src.stage.layer1_perception import PerceptionSnapshot, midi_to_note_name
from src.stage.layer2_intent_llm import MusicalIntentEngine
from src.stage.layer3_candidate_composer import MultipleCandidateComposer, ZeroConfigCorpusLoader
from src.stage.logic_trace import build_logic_trace
from src.stage.notagen_live_engine import NotaGenLiveEngine
from src.stage.token_economizer import TokenEconomizer
from src.stage.vram_manager import RTX3070VRAMManager

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[2]
EPHEMERAL_TOKEN_PATH = REPO_ROOT / "data" / "telemetry" / "remote_gpu_token.txt"


def _load_remote_config() -> Dict[str, Any]:
    if yaml is not None and DEFAULT_CONFIG_PATH.exists():
        try:
            data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def _resolve_api_token(explicit_token: Optional[str] = None) -> tuple[str, bool]:
    """Resolves Bearer API token, generating a secure ephemeral token if none configured."""
    tok = (explicit_token if explicit_token is not None else os.environ.get("DISKLAVIER_API_TOKEN", "")).strip()
    if tok:
        return tok, False
    generated = secrets.token_urlsafe(24)
    print(
        "[SECURITY WARNING] DISKLAVIER_API_TOKEN is not set! "
        "Generated ephemeral local Bearer token for this session."
    )
    print(f"[SECURITY WARNING] Temporary DISKLAVIER_API_TOKEN={generated}")
    try:
        EPHEMERAL_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        EPHEMERAL_TOKEN_PATH.write_text(generated, encoding="utf-8")
        print(f"[SECURITY WARNING] Token saved locally to {EPHEMERAL_TOKEN_PATH}")
    except Exception:
        pass
    return generated, True


class RemoteDeliberateRequest(BaseModel):
    mode: str = "cello"
    turn_id: int = 1
    perception: Dict[str, Any] = Field(default_factory=dict)
    human_notes: List[Dict[str, Any]] = Field(default_factory=list)
    directive_hints: Dict[str, Any] = Field(default_factory=dict)
    macro_plan: Optional[Dict[str, Any]] = None
    explicit_retake: bool = False
    use_ollama: Optional[bool] = None
    compressed_context: Optional[str] = None


class RemoteGPUInferenceEngine:
    """Thread-safe GPU inference orchestrator (no physical audio/MIDI device access)."""

    def __init__(
        self,
        use_ollama: Optional[bool] = None,
        corpus_dirs: Optional[List[Path]] = None,
    ) -> None:
        cfg = _load_remote_config()
        llm_cfg = cfg.get("llm", {})
        selected_model = str(llm_cfg.get("selected_model") or "qwen3.5:4b")

        if use_ollama is None:
            info = scan_ollama_environment()
            ollama_ready = bool(info.get("running", False))
            if info.get("selected_model"):
                selected_model = str(info["selected_model"])
        else:
            ollama_ready = bool(use_ollama)

        self.vram_manager = RTX3070VRAMManager(ollama_model=selected_model)
        self.economizer = TokenEconomizer()
        self.intent_engine = MusicalIntentEngine(
            model_name=selected_model,
            use_ollama=ollama_ready,
            timeout_s=6.0,
            economizer=self.economizer,
        )
        self.corpus_loader = ZeroConfigCorpusLoader(search_dirs=corpus_dirs)
        self.phrase_model = CorpusPhraseModelManager()
        self.composer = MultipleCandidateComposer(
            corpus_loader=self.corpus_loader,
            phrase_model=self.phrase_model,
        )
        self.architect = CoCompositionArchitect()
        self.notagen_engine = NotaGenLiveEngine(
            vram_manager=self.vram_manager,
            ollama_model=selected_model,
            output_layer=None,
            audio_synth=None,
            obsidian_brain=None,
            phrase_model=self.phrase_model,
            use_ollama=ollama_ready,
        )
        self.composer.anticipatory_engine = self.notagen_engine.anticipatory_engine
        self.composer.midigpt_engine = self.notagen_engine.midigpt_engine

        self._lock = threading.Lock()
        self.inference_queue_length: int = 0
        self.total_requests_served: int = 0
        self.started_at_iso: str = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def get_health(self) -> Dict[str, Any]:
        probe = self.vram_manager.probe_vram(force=True)
        vram_status = self.vram_manager.get_status()
        ng_status = self.notagen_engine.get_status()
        loaded_models: List[str] = []
        if self.intent_engine.use_ollama:
            loaded_models.append(f"ollama:{self.intent_engine.model_name}")
        if self.phrase_model.loaded:
            loaded_models.append(f"phrase_model:{self.phrase_model.device}")
        loaded_models.append(f"anticipatory:{ng_status.get('anticipatory_device', 'cpu')}")
        loaded_models.append("midigpt:cpu")
        if ng_status.get("notagen_checkpoint_present"):
            loaded_models.append(f"notagen:{ng_status.get('notagen_quantization', 'int8')}")

        with self._lock:
            q_len = self.inference_queue_length
            served = self.total_requests_served

        return {
            "ok": True,
            "service": "disklavier-remote-gpu",
            "gpu_name": str(probe.get("device_name", "CPU (No CUDA GPU)")),
            "cuda_available": bool(probe.get("cuda_available", False)),
            "vram_used_gb": float(probe.get("used_gb", 0.0)),
            "vram_free_gb": float(probe.get("free_gb", 0.0)),
            "vram_total_gb": float(probe.get("total_gb", 8.0)),
            "vram_profile": vram_status.get("active_profile", "realtime_dialogue"),
            "vram_owner": vram_status.get("owner", "idle"),
            "loaded_models": loaded_models,
            "ollama_model": self.intent_engine.model_name,
            "ollama_available": bool(self.intent_engine.use_ollama),
            "inference_queue_length": q_len,
            "total_requests_served": served,
            "server_time_iso": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "server_epoch_ms": int(time.time() * 1000),
            "started_at_iso": self.started_at_iso,
        }

    def run_deliberate(self, req: RemoteDeliberateRequest) -> Dict[str, Any]:
        with self._lock:
            self.inference_queue_length += 1
        t0 = time.perf_counter()
        try:
            p_raw = dict(req.perception or {})
            human_notes = list(req.human_notes or [])
            if not human_notes:
                fallback_p = int(p_raw.get("midi_pitch") or 50)
                human_notes = [{"pitch": fallback_p, "velocity": 72, "onset_s": 0.0, "duration_s": 0.4}]

            last_p = int(p_raw.get("midi_pitch") or human_notes[-1].get("pitch", 50))
            snap = PerceptionSnapshot(
                mode="piano" if str(req.mode).lower() == "piano" else "cello",
                timestamp=float(p_raw.get("timestamp") or time.perf_counter()),
                f0_hz=float(p_raw.get("f0_hz") or round(440.0 * (2.0 ** ((last_p - 69) / 12.0)), 2)),
                midi_pitch=last_p,
                note_name=str(p_raw.get("note_name") or midi_to_note_name(last_p)),
                vibrato_cents=float(p_raw.get("vibrato_cents") or 0.0),
                vibrato_rate_hz=float(p_raw.get("vibrato_rate_hz") or 0.0),
                articulation=str(p_raw.get("articulation") or "legato"),
                attack_slope=float(p_raw.get("attack_slope") or 0.5),
                rms_level=float(p_raw.get("rms_level") or 0.12),
                rms_dbfs=float(p_raw.get("rms_dbfs") or -18.0),
                breath_gauge=float(p_raw.get("breath_gauge") or 1.0),
                turn_ready=bool(p_raw.get("turn_ready", True)),
                turn_reason=str(p_raw.get("turn_reason") or "remote_turn"),
                pedal_cc64=int(p_raw.get("pedal_cc64") or 0),
                pedal_cc67=int(p_raw.get("pedal_cc67") or 0),
                echo_suppressed_count=int(p_raw.get("echo_suppressed_count") or 0),
                human_notes_count=int(p_raw.get("human_notes_count") or len(human_notes)),
                nlms_erle_db=float(p_raw.get("nlms_erle_db") or 0.0),
                recent_human_notes=human_notes,
                verbal_directive=str(req.directive_hints.get("raw_text") or p_raw.get("verbal_directive") or ""),
            )

            macro_plan: Any = req.macro_plan
            if macro_plan is None:
                macro_plan = self.architect.plan_turn_architecture(
                    human_notes=human_notes,
                    directive_hints=req.directive_hints,
                    mode=snap.mode,
                    explicit_retake=req.explicit_retake,
                )

            if req.use_ollama is False:
                orig_ollama = self.intent_engine.use_ollama
                self.intent_engine.use_ollama = False
            else:
                orig_ollama = self.intent_engine.use_ollama

            try:
                if self.intent_engine.use_ollama:
                    self.vram_manager.acquire_for("ollama", required_gb=3.4)
                corpus_refs = self.corpus_loader.retrieve_voicing_references(tension=0.5, momentum=0.5, top_k=3)
                policy_intent = self.intent_engine.decide_policy(
                    snap,
                    human_notes,
                    req.directive_hints,
                    corpus_refs=corpus_refs,
                    use_llm_design=self.intent_engine.use_ollama,
                    macro_plan=macro_plan,
                )
                turn_res = self.composer.compose_turn(
                    human_notes,
                    policy_intent,
                    human_mode=snap.mode,
                    macro_plan=macro_plan,
                    parallel=True,
                )
                final_intent = self.intent_engine.finalize_deliberation_monologue(
                    snap,
                    human_notes,
                    policy_intent,
                    turn_res,
                    req.directive_hints,
                    corpus_refs=turn_res.corpus_refs_used,
                    macro_plan=macro_plan,
                )
            finally:
                self.intent_engine.use_ollama = orig_ollama

            gpu_infer_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            probe = self.vram_manager.probe_vram(force=False)

            cand_payloads = [
                {
                    "candidate_id": c.candidate_id,
                    "label_ja": c.label_ja,
                    "strategy": c.strategy,
                    "events": c.events,
                    "total_score": c.total_score,
                    "subscores": c.subscores,
                    "subscores_pct": c.subscores_pct,
                    "adopted": c.adopted,
                    "rejection_tag_ja": c.rejection_tag_ja,
                    "symusic_note_count": c.symusic_note_count,
                    "pitch_range_str": c.pitch_range_str,
                    "sample_notes_str": c.sample_notes_str,
                    "problem_note_indices": c.problem_note_indices,
                    "rejection_detail": c.rejection_detail,
                    "audit_passed": c.audit_passed,
                    "pedal_cc64_curve": c.pedal_cc64_curve,
                    "pedal_cc67_curve": c.pedal_cc67_curve,
                    "masterwork_total": c.masterwork_total,
                    "masterwork_pct": c.masterwork_pct,
                    "lh_idiom": c.lh_idiom,
                    "blueprint_source": c.blueprint_source,
                    "chord_timeline": c.chord_timeline,
                    "beauty_summary": c.beauty_summary,
                    "realizer_diff": c.realizer_diff,
                }
                for c in turn_res.candidates
            ]
            adopted_payload = next((c for c in cand_payloads if c["adopted"]), cand_payloads[0])

            logic_trace = build_logic_trace(
                turn_id=int(req.turn_id or turn_res.turn_id),
                mode=snap.mode,
                perception=snap,
                human_notes=human_notes,
                intent=final_intent,
                candidates=turn_res.candidates,
                token_stats=self.intent_engine.last_token_stats,
                compact_prompt=self.intent_engine.last_compact_prompt,
                llm_source=self.intent_engine.last_source,
                validator_rewrites=self.intent_engine.last_validator_rewrites,
                timing_budget={
                    "perception_ms": 0.0,
                    "network_rtt_ms": 0.0,
                    "gpu_infer_ms": gpu_infer_ms,
                    "lookahead_ms": 0.0,
                    "total_response_ms": gpu_infer_ms,
                    "backend_source": "remote_rtx",
                    "speculative_hit": False,
                },
            )

            macro_out = (
                macro_plan
                if isinstance(macro_plan, dict)
                else {
                    "act_number": getattr(macro_plan, "act_number", final_intent.act_number),
                    "act_label_ja": getattr(macro_plan, "act_label_ja", final_intent.act_label_ja),
                    "phrase_function": getattr(macro_plan, "phrase_function", final_intent.phrase_function),
                    "target_tonality": getattr(macro_plan, "target_tonality", final_intent.tonal_center),
                    "seed_motif_str": getattr(macro_plan, "seed_motif_str", final_intent.seed_motif_str),
                    "voice_leading_bridge_desc": getattr(macro_plan, "voice_leading_bridge_desc", final_intent.voice_leading_bridge),
                    "is_retake": bool(getattr(macro_plan, "is_retake", req.explicit_retake)),
                }
            )

            return {
                "ok": True,
                "source": "remote_rtx",
                "gpu_name": str(probe.get("device_name", "NVIDIA GeForce RTX 3070")),
                "vram_used_gb": float(probe.get("used_gb", 0.0)),
                "vram_total_gb": float(probe.get("total_gb", 8.0)),
                "intent": final_intent.model_dump(),
                "candidates": cand_payloads,
                "adopted_candidate": adopted_payload,
                "pedal_cc64_curve": adopted_payload.get("pedal_cc64_curve", []),
                "pedal_cc67_curve": adopted_payload.get("pedal_cc67_curve", []),
                "macro_plan": macro_out,
                "token_stats": dict(self.intent_engine.last_token_stats),
                "compact_prompt": self.intent_engine.last_compact_prompt,
                "llm_source": self.intent_engine.last_source,
                "validator_rewrites": self.intent_engine.last_validator_rewrites,
                "logic_trace": logic_trace,
                "timing": {
                    "gpu_infer_ms": gpu_infer_ms,
                    "parallel_compose_ms": getattr(turn_res, "parallel_compose_ms", 0.0),
                    "sum_worker_ms": getattr(turn_res, "sum_worker_ms", 0.0),
                    "parallel_speedup_ratio": getattr(turn_res, "parallel_speedup_ratio", 1.0),
                    "llm_latency_ms": self.intent_engine.last_latency_ms,
                    "llm_ttft_ms": self.intent_engine.last_ttft_ms,
                },
            }
        finally:
            with self._lock:
                self.inference_queue_length = max(0, self.inference_queue_length - 1)
                self.total_requests_served += 1

    def close(self) -> None:
        try:
            self.notagen_engine.stop_generation()
        except Exception:
            pass
        self.composer.close()


def create_remote_gpu_app(
    api_token: Optional[str] = None,
    use_ollama: Optional[bool] = None,
    corpus_dirs: Optional[List[Path]] = None,
) -> FastAPI:
    token_val, is_ephemeral = _resolve_api_token(api_token)
    engine = RemoteGPUInferenceEngine(use_ollama=use_ollama, corpus_dirs=corpus_dirs)

    def _require_bearer(authorization: Optional[str]) -> None:
        raw = (authorization or "").strip()
        if not raw.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Missing Bearer token")
        supplied = raw[7:].strip()
        if not secrets.compare_digest(supplied, token_val):
            raise HTTPException(status_code=401, detail="Invalid Bearer token")

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        try:
            yield
        finally:
            engine.close()

    app = FastAPI(title="Disklavier-Auto Remote RTX 3070 GPU Inference Server", lifespan=_lifespan)
    app.state.remote_engine = engine
    app.state.api_token = token_val
    app.state.ephemeral_token = is_ephemeral

    @app.get("/ping")
    async def ping() -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "service": "disklavier-remote-gpu",
                "server_time_iso": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "server_epoch_ms": int(time.time() * 1000),
            }
        )

    @app.get("/api/v1/health")
    async def api_v1_health(authorization: Optional[str] = Header(default=None)) -> JSONResponse:
        _require_bearer(authorization)
        return JSONResponse(engine.get_health())

    @app.post("/api/v1/infer/deliberate")
    async def api_v1_infer_deliberate(
        req: RemoteDeliberateRequest,
        authorization: Optional[str] = Header(default=None),
    ) -> JSONResponse:
        _require_bearer(authorization)
        res = await asyncio.to_thread(engine.run_deliberate, req)
        return JSONResponse(res)

    @app.websocket("/ws/v1/stream_compose")
    async def ws_v1_stream_compose(ws: WebSocket) -> None:
        auth_hdr = (ws.headers.get("authorization") or "").strip()
        q_tok = (ws.query_params.get("token") or "").strip()
        supplied = auth_hdr[7:].strip() if auth_hdr.lower().startswith("bearer ") else q_tok
        if not supplied or not secrets.compare_digest(supplied, token_val):
            await ws.close(code=1008, reason="Unauthorized Bearer token")
            return

        await ws.accept()
        try:
            while True:
                raw_msg = await ws.receive_text()
                try:
                    cmd = json.loads(raw_msg)
                except Exception:
                    await ws.send_json({"type": "error", "error": "Invalid JSON command"})
                    continue

                action = str(cmd.get("action") or "start")
                if action == "stop":
                    await asyncio.to_thread(engine.notagen_engine.stop_generation)
                    await ws.send_json({"type": "stopped"})
                    continue

                if action != "start":
                    await ws.send_json({"type": "error", "error": f"Unknown action: {action}"})
                    continue

                t0 = time.perf_counter()
                engine.notagen_engine.start_generation(
                    seed_notes=cmd.get("seed_notes"),
                    prompt_ja=str(cmd.get("prompt_ja") or ""),
                    tonality=str(cmd.get("tonality") or "D_minor"),
                    bpm=float(cmd.get("bpm") or 76.0),
                    bars=int(cmd.get("bars") or 4),
                    engine=str(cmd.get("engine") or "auto"),
                    play_audio=False,
                    blocking=False,
                )

                sent_bars = 0
                while True:
                    st = engine.notagen_engine.get_status()
                    completed = int(st.get("bars_completed", 0))
                    chords = list(st.get("chord_timeline") or [])
                    events = list(st.get("events") or [])
                    abc_lines = str(st.get("abc_stream_text") or "").splitlines()

                    while sent_bars < completed:
                        bar_idx = sent_bars
                        seg = chords[bar_idx] if bar_idx < len(chords) else {}
                        b0 = float(seg.get("start_s", 0.0))
                        b1 = float(seg.get("end_s", b0 + 3.2))
                        bar_evs = [
                            ev
                            for ev in events
                            if b0 - 0.02 <= float(ev.get("onset_s", 0.0)) < b1 + 0.02
                        ]
                        bar_abc = "\n".join(abc_lines[-4:])
                        await ws.send_json(
                            {
                                "type": "bar",
                                "bar": bar_idx + 1,
                                "total_bars": int(st.get("target_bars", 4)),
                                "active_backend": st.get("active_backend"),
                                "chord": seg.get("chord", "Dm"),
                                "roman": seg.get("roman", "i"),
                                "abc_bar": bar_abc,
                                "events": bar_evs,
                                "ttft_ms": st.get("ttft_ms", 0.0),
                                "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                            }
                        )
                        sent_bars += 1

                    if st.get("status") in ("completed", "stopped", "error"):
                        await ws.send_json(
                            {
                                "type": "complete",
                                "status": st.get("status"),
                                "active_backend": st.get("active_backend"),
                                "bars_completed": completed,
                                "ttft_ms": st.get("ttft_ms", 0.0),
                                "total_gen_ms": st.get("total_gen_ms", 0.0),
                                "audit": st.get("audit", {}),
                            }
                        )
                        break
                    await asyncio.sleep(0.015)
        except (WebSocketDisconnect, ConnectionResetError, OSError, asyncio.CancelledError):
            pass
        except Exception as exc:
            try:
                await ws.send_json({"type": "error", "error": str(exc)})
            except Exception:
                pass

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="Disklavier-Auto Remote RTX 3070 GPU Inference Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--no-ollama", action="store_true", help="Run without local Ollama")
    args = parser.parse_args()

    import uvicorn

    app = create_remote_gpu_app(use_ollama=(False if args.no_ollama else None))
    uvicorn.run(app, host=args.host, port=args.port, workers=1, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
