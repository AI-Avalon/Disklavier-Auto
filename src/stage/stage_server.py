"""
Layer 4: FastAPI Stage Server, 60fps WebSocket Broadcaster, and Virtual Showcase Demo Runner.

Coordinates L1 (Perception) -> L2 (Ollama/Fallback Intent) -> L3 (symusic 4-Candidate Pruning)
-> L5 (Disklavier 30-Voice Safe Output) -> L4 (Stage Editorial Web HUD @ 60fps).
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import random
import threading
import time
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
import numpy as np
from pydantic import BaseModel

from src.stage.config_scanner import DEFAULT_CONFIG_PATH, scan_ollama_environment
from src.stage.layer1_perception import (
    CelloAcousticPerception,
    EchoSuppressionLedger,
    NLMSEchoCanceller,
    PerceptionSnapshot,
    TurnTakingDetector,
    VerbalDirectiveRecognizer,
    midi_to_note_name,
)
from src.stage.layer2_intent_llm import MusicalIntentEngine
from src.stage.layer3_candidate_composer import (
    CandidatePhrase,
    MultipleCandidateComposer,
    ZeroConfigCorpusLoader,
)
from src.stage.layer5_disklavier_output import DisklavierStageOutputLayer

try:
    import mido
except Exception:  # pragma: no cover
    mido = None  # type: ignore[assignment]

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


REPO_ROOT = Path(__file__).resolve().parents[2]
STAGE_HUD_HTML_PATH = REPO_ROOT / "src" / "ui" / "stage_hud.html"


def _load_stage_config() -> Dict[str, Any]:
    if yaml is not None and DEFAULT_CONFIG_PATH.exists():
        try:
            data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


class ModeSwitchReq(BaseModel):
    mode: str  # "piano" | "cello"


class DemoToggleReq(BaseModel):
    enabled: bool


class DirectiveReq(BaseModel):
    text: str


class StageOrchestrator:
    """
    Runs the full L1 -> L2 -> L3 -> L5 -> L4 pipeline in real time.
    Supports both live MIDI/Audio hardware and zero-hardware Virtual Showcase Demo mode.
    """

    def __init__(
        self,
        demo_enabled: bool = False,
        corpus_dirs: Optional[List[Path]] = None,
        use_ollama: Optional[bool] = None,
        open_hardware: bool = True,
    ) -> None:
        cfg = _load_stage_config()
        llm_cfg = cfg.get("llm", {})
        midi_cfg = cfg.get("midi", {})

        self.mode: str = "cello"  # Default Mode B ("cello") or Mode A ("piano")
        self.demo_enabled: bool = demo_enabled
        self.phase: str = "listening"
        self.turn_id: int = 0
        self._lock = threading.RLock()

        # L1 Perception
        self.echo_ledger = EchoSuppressionLedger(min_lag_s=0.0, max_lag_s=0.280)
        self.turn_detector = TurnTakingDetector(silence_threshold_s=0.75)
        self.nlms = NLMSEchoCanceller()
        self.cello_perception = CelloAcousticPerception(sample_rate=44100)
        self.directive_recognizer = VerbalDirectiveRecognizer()
        self.pending_directive_hints: Dict[str, Any] = {}
        self.human_phrase_buffer: List[Dict[str, Any]] = []

        # L2 Intent (Ollama auto-detection unless explicitly specified)
        selected_model = str(llm_cfg.get("selected_model") or "qwen3.5:4b")
        if use_ollama is None:
            ollama_ready = bool(llm_cfg.get("ollama_running", False))
        else:
            ollama_ready = bool(use_ollama)

        self.intent_engine = MusicalIntentEngine(
            model_name=selected_model,
            use_ollama=ollama_ready,
            timeout_s=5.0,
        )
        if use_ollama is None and not ollama_ready:
            # Probe Ollama asynchronously so server startup / module import is instant
            def _probe_ollama() -> None:
                info = scan_ollama_environment()
                if info.get("running") and info.get("selected_model"):
                    self.intent_engine.model_name = str(info["selected_model"])
                    self.intent_engine.use_ollama = True

            threading.Thread(target=_probe_ollama, name="OllamaAsyncProbe", daemon=True).start()

        # L3 Multiple Candidate Composer (Zero-Config Corpus)
        self.corpus_loader = ZeroConfigCorpusLoader(search_dirs=corpus_dirs)
        self.composer = MultipleCandidateComposer(corpus_loader=self.corpus_loader)

        # L5 Safe Disklavier Output
        out_port_pref = midi_cfg.get("preferred_out_port") if open_hardware else None
        self.output_layer = DisklavierStageOutputLayer(
            port_name=out_port_pref,
            midi_sink=self._on_l5_midi_event,
            echo_register_cb=self.echo_ledger.register_sent_note,
            start_worker=True,
        )

        # Optional MIDI IN listener for Mode A
        self.inport: Any = None
        if open_hardware and mido is not None:
            self._try_open_midi_in(str(midi_cfg.get("preferred_in_port") or "Disklavier"))

        # Latest state snapshots for L4 HUD
        self.latest_perception = PerceptionSnapshot(mode=self.mode, timestamp=time.perf_counter())
        self.latest_intent = self.intent_engine._rule_based_intent(
            self.latest_perception, [], {}
        )
        self.latest_candidates: List[CandidatePhrase] = []
        self.recent_strikes: List[Dict[str, Any]] = []
        self.recent_human_demo_notes: List[Dict[str, Any]] = []

        # Motif Memory Graph (starts at 0 nodes at curtain rise)
        self.motif_nodes: List[Dict[str, Any]] = []
        self.motif_edges: List[Dict[str, Any]] = []
        self._last_human_node_id: Optional[str] = None
        self._last_ai_node_id: Optional[str] = None

        # Background Demo / Turn Loop
        self._running: bool = True
        self._demo_step_idx: int = 0
        self._thread = threading.Thread(
            target=self._orchestration_loop,
            name="StageOrchestratorLoop",
            daemon=True,
        )
        self._thread.start()

    def _try_open_midi_in(self, preferred: str) -> None:
        try:
            ins = mido.get_input_names()
            for kw in (preferred.lower(), "disklavier", "yamaha", "usb-midi"):
                match = next((i for i in ins if kw in i.lower()), None)
                if match:
                    self.inport = mido.open_input(match, callback=self._on_hardware_midi_in)
                    return
        except Exception:
            self.inport = None

    def _on_hardware_midi_in(self, msg: Any) -> None:
        now = time.perf_counter()
        if msg.type == "note_on" and msg.velocity > 0:
            accepted = self.echo_ledger.filter_incoming_note(msg.note, msg.velocity, recv_ts=now)
            if accepted is None:
                return
            with self._lock:
                self.human_phrase_buffer.append(accepted)
                self.turn_detector.push_note(msg.note, now)
                self.latest_perception = PerceptionSnapshot(
                    mode="piano",
                    timestamp=now,
                    midi_pitch=msg.note,
                    note_name=midi_to_note_name(msg.note),
                    articulation="legato" if self.turn_detector.pedal_cc64 >= 35 else "staccato",
                    echo_suppressed_count=self.echo_ledger.suppressed_count,
                    human_notes_count=self.echo_ledger.human_accepted_count,
                    pedal_cc64=self.turn_detector.pedal_cc64,
                    pedal_cc67=self.turn_detector.pedal_cc67,
                    recent_human_notes=list(self.human_phrase_buffer[-12:]),
                )
        elif msg.type == "control_change":
            with self._lock:
                self.turn_detector.push_cc(msg.control, msg.value, now)

    def _on_l5_midi_event(self, payload: Dict[str, Any]) -> None:
        if payload.get("type") == "note_on":
            with self._lock:
                self.recent_strikes.append(
                    {
                        "pitch": int(payload["pitch"]),
                        "velocity": int(payload["velocity"]),
                        "ts": float(payload["ts"]),
                        "note_name": midi_to_note_name(int(payload["pitch"])),
                    }
                )
                if len(self.recent_strikes) > 48:
                    self.recent_strikes = self.recent_strikes[-48:]

    def set_mode(self, mode: str) -> Dict[str, Any]:
        m = "piano" if str(mode).lower() in ("piano", "mode_a", "a") else "cello"
        with self._lock:
            self.mode = m
            self.latest_perception.mode = m
        return {"ok": True, "mode": self.mode}

    def set_demo_enabled(self, enabled: bool) -> Dict[str, Any]:
        with self._lock:
            self.demo_enabled = bool(enabled)
        return {"ok": True, "demo_enabled": self.demo_enabled}

    def apply_directive(self, text: str) -> Dict[str, Any]:
        hints = self.directive_recognizer.parse_directive(text)
        with self._lock:
            self.pending_directive_hints = hints
            self.latest_perception.verbal_directive = hints.get("raw_text", "")
        return {"ok": True, "hints": hints}

    def reset_performance(self) -> Dict[str, Any]:
        with self._lock:
            self.motif_nodes.clear()
            self.motif_edges.clear()
            self._last_human_node_id = None
            self._last_ai_node_id = None
            self.turn_id = 0
            self.human_phrase_buffer.clear()
            self.latest_candidates = []
        self.output_layer.panic()
        return {"ok": True, "motif_nodes": 0}

    def _simulate_virtual_human_turn(self, step_idx: int) -> tuple[PerceptionSnapshot, List[Dict[str, Any]]]:
        """
        Generates authentic Virtual Cellist (Mode B) or Virtual Pianist (Mode A) performance
        through the real L1 perception pipeline (including NLMS echo cancellation and Echo Ledger).
        """
        now = time.perf_counter()
        rng = random.Random(step_idx + 101)
        if self.mode == "cello":
            cello_motifs = [
                ([50, 53, 57, 55, 52, 50], "legato", "D_dorian"),
                ([55, 58, 62, 65, 63, 62], "staccato", "G_minor"),
                ([45, 52, 57, 60, 59, 57], "legato", "D_minor"),
                ([57, 60, 64, 67, 65, 62], "staccato", "D_dorian"),
            ]
            pitches, target_art, _ = cello_motifs[step_idx % len(cello_motifs)]
            sr = 44100
            f0_main = 440.0 * (2.0 ** ((pitches[-1] - 69) / 12.0))
            n_samp = int(sr * 0.65)
            t = np.arange(n_samp, dtype=np.float64) / sr

            # Synthesize realistic cello harmonic waveform + phase-integrated vibrato
            phase = 2.0 * np.pi * f0_main * np.cumsum(1.0 + 0.004 * np.sin(2.0 * np.pi * 5.4 * t)) / sr
            cello = (
                0.28 * np.sin(phase)
                + 0.14 * np.sin(2.0 * phase)
                + 0.07 * np.sin(3.0 * phase)
            )
            if target_art == "staccato":
                # 3 detached staccato strokes with crisp decay
                env = np.zeros_like(t)
                for k in range(3):
                    s = int((k * 0.21) * sr)
                    e = min(n_samp, s + int(0.08 * sr))
                    if e > s:
                        seg_t = np.linspace(0.0, 1.0, e - s)
                        env[s:e] = np.exp(-5.0 * seg_t) * np.minimum(1.0, seg_t * 14.0)
                cello *= env
            else:
                # Smooth legato bow envelope
                cello *= np.minimum(1.0, t / 0.08) * (0.9 + 0.1 * np.sin(2.0 * np.pi * 1.5 * t))

            # Inject synthetic Disklavier bleed (Ch2 ref) and cancel it with NLMS in L1
            piano_ref = 0.25 * np.sin(2.0 * np.pi * 329.63 * t) + 0.15 * np.sin(2.0 * np.pi * 493.88 * t)
            mic = cello + 0.72 * piano_ref

            snap = self.cello_perception.analyze_utterance(mic, piano_ref, nlms=self.nlms)
            snap.mode = "cello"
            snap.breath_gauge = 0.92
            snap.turn_ready = True
            snap.turn_reason = "virtual_cello_phrase_end"
            snap.double_stop_pitches = [pitches[-2], pitches[-1]] if step_idx % 3 == 2 else [pitches[-1]]

            human_notes = [
                {
                    "pitch": p,
                    "onset_s": round(i * (0.18 if target_art == "staccato" else 0.34), 3),
                    "duration_s": 0.11 if target_art == "staccato" else 0.32,
                    "velocity": 68 + rng.randint(-4, 8),
                    "voice": "virtual_cello",
                }
                for i, p in enumerate(pitches)
            ]
            snap.recent_human_notes = human_notes
            snap.human_notes_count = len(human_notes)
            return snap, human_notes

        else:
            # Mode A: Virtual Pianist + test self-echo injection to prove EchoSuppressionLedger
            piano_motifs = [
                [62, 65, 69, 67, 64, 62],
                [57, 60, 64, 65, 64, 60],
                [67, 70, 74, 72, 69, 67],
            ]
            pitches = piano_motifs[step_idx % len(piano_motifs)]
            # Simulate an echoed self-strike caught and suppressed by L1 EchoSuppressionLedger
            echo_pitch = 72
            self.echo_ledger.register_sent_note(echo_pitch, now - 0.045)
            assert self.echo_ledger.filter_incoming_note(echo_pitch, 68, recv_ts=now) is None

            human_notes = []
            for i, p in enumerate(pitches):
                note_ts = now - (len(pitches) - i) * 0.24
                filtered = self.echo_ledger.filter_incoming_note(p, 70 + (i % 3) * 4, recv_ts=note_ts)
                if filtered:
                    self.turn_detector.push_note(p, note_ts)
                    human_notes.append(
                        {
                            "pitch": p,
                            "onset_s": round(i * 0.28, 3),
                            "duration_s": 0.26,
                            "velocity": filtered["velocity"],
                            "voice": "virtual_piano",
                        }
                    )
            self.turn_detector.push_cc(67, 90 if step_idx % 2 == 1 else 0, now)
            gauge, ready, reason = self.turn_detector.evaluate(now + 0.45)

            snap = PerceptionSnapshot(
                mode="piano",
                timestamp=now,
                f0_hz=round(440.0 * (2.0 ** ((pitches[-1] - 69) / 12.0)), 2),
                midi_pitch=pitches[-1],
                note_name=midi_to_note_name(pitches[-1]),
                articulation="legato" if step_idx % 2 == 0 else "staccato",
                breath_gauge=max(0.84, gauge),
                turn_ready=True or ready,
                turn_reason=reason,
                pedal_cc64=44 if step_idx % 2 == 0 else 12,
                pedal_cc67=90 if step_idx % 2 == 1 else 0,
                echo_suppressed_count=self.echo_ledger.suppressed_count,
                human_notes_count=self.echo_ledger.human_accepted_count,
                recent_human_notes=human_notes,
                pitch_history=[round(440.0 * (2.0 ** ((p - 69) / 12.0)), 2) for p in pitches],
            )
            return snap, human_notes

    def execute_composition_turn(self, use_virtual_human: bool = True) -> Dict[str, Any]:
        with self._lock:
            self._demo_step_idx += 1
            step_idx = self._demo_step_idx
            self.phase = "deliberating"

            if use_virtual_human or not self.human_phrase_buffer:
                snap, human_notes = self._simulate_virtual_human_turn(step_idx)
                self.recent_human_demo_notes = human_notes
            else:
                human_notes = list(self.human_phrase_buffer[-8:])
                self.human_phrase_buffer.clear()
                snap = self.latest_perception
                snap.breath_gauge = 1.0
                snap.turn_ready = True

            hints = dict(self.pending_directive_hints)
            if hints.get("raw_text"):
                snap.verbal_directive = str(hints["raw_text"])

        # L2: Musical Intent (outside lock so fast WS reads never block)
        intent = self.intent_engine.decide_intent(snap, human_notes, hints)

        # L3: Generate 4 candidates + score + prune
        turn_res = self.composer.compose_turn(human_notes, intent, human_mode=snap.mode)

        # L5: Schedule adopted candidate safely with VDL lookahead
        adopted = turn_res.adopted_candidate
        self.output_layer.schedule_phrase(adopted.events, base_sound_ts=time.perf_counter() + 0.12)

        with self._lock:
            self.turn_id = turn_res.turn_id
            self.latest_perception = snap
            self.latest_intent = intent
            self.latest_candidates = turn_res.candidates
            self.phase = "performing"
            self.pending_directive_hints = {}

            # Grow Motif Memory Graph (starts at 0 nodes at curtain rise)
            h_id = f"H{self.turn_id}"
            a_id = f"A{self.turn_id}"
            h_pitches = [int(n["pitch"]) for n in human_notes[:4]]
            a_pitches = [int(e["pitch"]) for e in adopted.events if int(e.get("pitch", 0)) > 0][:4]
            self.motif_nodes.append(
                {
                    "id": h_id,
                    "kind": "human",
                    "turn": self.turn_id,
                    "label": f"{'Vc' if snap.mode == 'cello' else 'Pf'} M{self.turn_id}: "
                    + "-".join(midi_to_note_name(p) for p in h_pitches[:3]),
                    "pitches": h_pitches,
                }
            )
            self.motif_nodes.append(
                {
                    "id": a_id,
                    "kind": "ai",
                    "turn": self.turn_id,
                    "label": f"AI [{adopted.candidate_id}] {intent.tonal_center}: "
                    + "-".join(midi_to_note_name(p) for p in a_pitches[:3]),
                    "pitches": a_pitches,
                    "stance": intent.interaction_stance,
                }
            )
            self.motif_edges.append(
                {
                    "source": h_id,
                    "target": a_id,
                    "relation": intent.interaction_stance,
                }
            )
            if self._last_ai_node_id is not None:
                self.motif_edges.append(
                    {
                        "source": self._last_ai_node_id,
                        "target": h_id,
                        "relation": "dialogue_flow",
                    }
                )
            self._last_human_node_id = h_id
            self._last_ai_node_id = a_id
            # Keep last 24 nodes cleanly readable
            if len(self.motif_nodes) > 24:
                keep_ids = {n["id"] for n in self.motif_nodes[-24:]}
                self.motif_nodes = self.motif_nodes[-24:]
                self.motif_edges = [
                    e for e in self.motif_edges if e["source"] in keep_ids and e["target"] in keep_ids
                ]

        return self.get_hud_state()

    def _orchestration_loop(self) -> None:
        last_turn_t = 0.0
        while self._running:
            now = time.perf_counter()
            if self.demo_enabled and (now - last_turn_t) >= 3.6:
                last_turn_t = now
                try:
                    self.execute_composition_turn(use_virtual_human=True)
                except Exception:
                    pass
            elif not self.demo_enabled and self.mode == "piano":
                with self._lock:
                    gauge, ready, reason = self.turn_detector.evaluate(now)
                    self.latest_perception.breath_gauge = gauge
                    self.latest_perception.turn_ready = ready
                    self.latest_perception.turn_reason = reason
                    has_notes = len(self.human_phrase_buffer) >= 2
                if ready and has_notes:
                    last_turn_t = now
                    try:
                        self.execute_composition_turn(use_virtual_human=False)
                    except Exception:
                        pass
            time.sleep(0.05)

    def get_hud_state(self) -> Dict[str, Any]:
        with self._lock:
            out_tel = self.output_layer.get_telemetry()
            active_pitches = sorted(int(k) for k in self.output_layer.active_notes.keys())
            p = self.latest_perception
            intent_dict = self.latest_intent.model_dump()
            candidates_list = [
                {
                    "candidate_id": c.candidate_id,
                    "label_ja": c.label_ja,
                    "strategy": c.strategy,
                    "events": c.events,
                    "total_score": c.total_score,
                    "subscores": c.subscores,
                    "adopted": c.adopted,
                    "rejection_tag_ja": c.rejection_tag_ja,
                    "symusic_note_count": c.symusic_note_count,
                }
                for c in self.latest_candidates
            ]
            return {
                "timestamp": round(time.perf_counter(), 4),
                "mode": self.mode,
                "demo_enabled": self.demo_enabled,
                "phase": self.phase,
                "turn_id": self.turn_id,
                "corpus_source": self.corpus_loader.source_mode,
                "perception": {
                    "mode": p.mode,
                    "f0_hz": p.f0_hz,
                    "midi_pitch": p.midi_pitch,
                    "note_name": p.note_name,
                    "vibrato_cents": p.vibrato_cents,
                    "vibrato_rate_hz": p.vibrato_rate_hz,
                    "articulation": p.articulation,
                    "attack_slope": p.attack_slope,
                    "rms_level": p.rms_level,
                    "rms_dbfs": p.rms_dbfs,
                    "double_stop_pitches": p.double_stop_pitches,
                    "breath_gauge": p.breath_gauge,
                    "turn_ready": p.turn_ready,
                    "turn_reason": p.turn_reason,
                    "pedal_cc64": p.pedal_cc64,
                    "pedal_cc67": p.pedal_cc67,
                    "echo_suppressed_count": self.echo_ledger.suppressed_count,
                    "human_notes_count": self.echo_ledger.human_accepted_count,
                    "nlms_erle_db": self.nlms.last_erle_db,
                    "pitch_history": p.pitch_history,
                    "recent_human_notes": p.recent_human_notes,
                    "verbal_directive": p.verbal_directive,
                },
                "intent": {
                    **intent_dict,
                    "llm_source": self.intent_engine.last_source,
                    "llm_latency_ms": self.intent_engine.last_latency_ms,
                },
                "candidates": candidates_list,
                "output": {
                    "active_pitches": active_pitches,
                    "active_voices": out_tel.active_voices,
                    "max_active_observed": out_tel.max_active_observed,
                    "hard_limit": out_tel.hard_limit,
                    "voice_steals": out_tel.voice_steals,
                    "velocity_clamps": out_tel.velocity_clamps,
                    "thermal_throttles": out_tel.thermal_throttles,
                    "pedal_thinned": out_tel.pedal_thinned,
                    "ttl_expired": out_tel.ttl_expired,
                    "panic_count": out_tel.panic_count,
                    "last_panic_elapsed_ms": out_tel.last_panic_elapsed_ms,
                    "pedal_cc64": out_tel.pedal_cc64,
                    "rolling_10s_energy": out_tel.rolling_10s_energy,
                    "energy_limit_10s": out_tel.energy_limit_10s,
                    "total_notes_sent": out_tel.total_notes_sent,
                    "connected_port": out_tel.connected_port or "Virtual Disklavier",
                    "recent_strikes": list(self.recent_strikes[-16:]),
                },
                "motif_graph": {
                    "nodes": list(self.motif_nodes),
                    "edges": list(self.motif_edges),
                },
            }

    def close(self) -> None:
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=0.5)
        if self.inport is not None:
            try:
                self.inport.close()
            except Exception:
                pass
            self.inport = None
        self.output_layer.close()


class StageWSBroadcaster:
    def __init__(self, orchestrator: StageOrchestrator) -> None:
        self.orchestrator = orchestrator
        self.clients: Set[WebSocket] = set()
        self._task: Optional[asyncio.Task[None]] = None

    def ensure_started(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._broadcast_loop())

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _broadcast_loop(self) -> None:
        while True:
            if self.clients:
                payload = self.orchestrator.get_hud_state()
                dead: List[WebSocket] = []
                for ws in list(self.clients):
                    try:
                        await ws.send_json(payload)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    self.clients.discard(ws)
            await asyncio.sleep(1.0 / 60.0)  # 60fps stream


_GLOBAL_ORCHESTRATOR: Optional[StageOrchestrator] = None


def get_orchestrator(
    demo_enabled: Optional[bool] = None,
    corpus_dirs: Optional[List[Path]] = None,
) -> StageOrchestrator:
    global _GLOBAL_ORCHESTRATOR
    if _GLOBAL_ORCHESTRATOR is None:
        env_demo = os.environ.get("DISKLAVIER_STAGE_DEMO", "0") == "1"
        _GLOBAL_ORCHESTRATOR = StageOrchestrator(
            demo_enabled=env_demo if demo_enabled is None else demo_enabled,
            corpus_dirs=corpus_dirs,
        )
    elif demo_enabled is not None:
        _GLOBAL_ORCHESTRATOR.set_demo_enabled(demo_enabled)
    return _GLOBAL_ORCHESTRATOR


def build_stage_router(orch: StageOrchestrator, broadcaster: StageWSBroadcaster) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    @router.get("/stage", response_class=HTMLResponse)
    async def serve_stage_hud() -> HTMLResponse:
        if STAGE_HUD_HTML_PATH.exists():
            return HTMLResponse(STAGE_HUD_HTML_PATH.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>stage_hud.html missing</h1>", status_code=404)

    @router.get("/api/stage/state")
    async def api_stage_state() -> JSONResponse:
        return JSONResponse(orch.get_hud_state())

    @router.post("/api/stage/mode")
    async def api_stage_mode(req: ModeSwitchReq) -> JSONResponse:
        return JSONResponse(orch.set_mode(req.mode))

    @router.post("/api/stage/demo")
    async def api_stage_demo(req: DemoToggleReq) -> JSONResponse:
        res = orch.set_demo_enabled(req.enabled)
        if req.enabled and orch.turn_id == 0:
            orch.execute_composition_turn(use_virtual_human=True)
        return JSONResponse(res)

    @router.post("/api/stage/directive")
    async def api_stage_directive(req: DirectiveReq) -> JSONResponse:
        res = orch.apply_directive(req.text)
        return JSONResponse(res)

    @router.post("/api/stage/turn")
    async def api_stage_turn(request: Request) -> JSONResponse:
        state = orch.execute_composition_turn(use_virtual_human=True)
        return JSONResponse({"ok": True, "state": state})

    @router.post("/api/stage/reset")
    async def api_stage_reset() -> JSONResponse:
        return JSONResponse(orch.reset_performance())

    @router.post("/api/stage/panic")
    async def api_stage_panic() -> JSONResponse:
        res = orch.output_layer.panic()
        return JSONResponse(res)

    @router.websocket("/ws")
    @router.websocket("/ws/stage")
    async def stage_websocket(ws: WebSocket) -> None:
        await ws.accept()
        broadcaster.ensure_started()
        broadcaster.clients.add(ws)
        try:
            await ws.send_json(orch.get_hud_state())
            while True:
                msg = await ws.receive_text()
                if msg == "turn":
                    orch.execute_composition_turn(use_virtual_human=True)
                elif msg == "panic":
                    orch.output_layer.panic()
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            broadcaster.clients.discard(ws)

    return router


def create_stage_app(
    demo_enabled: Optional[bool] = None,
    corpus_dirs: Optional[List[Path]] = None,
    use_ollama: Optional[bool] = None,
    open_hardware: bool = True,
) -> FastAPI:
    orch = StageOrchestrator(
        demo_enabled=(os.environ.get("DISKLAVIER_STAGE_DEMO", "0") == "1") if demo_enabled is None else demo_enabled,
        corpus_dirs=corpus_dirs,
        use_ollama=use_ollama,
        open_hardware=open_hardware,
    )
    broadcaster = StageWSBroadcaster(orch)

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        broadcaster.ensure_started()
        if orch.demo_enabled and orch.turn_id == 0:
            orch.execute_composition_turn(use_virtual_human=True)
        try:
            yield
        finally:
            await broadcaster.stop()
            orch.close()

    app = FastAPI(title="Disklavier-Auto Stage Process Visualization", lifespan=_lifespan)
    app.state.orchestrator = orch
    app.state.broadcaster = broadcaster
    app.include_router(build_stage_router(orch, broadcaster))
    return app


def __getattr__(name: str) -> Any:
    if name == "stage_app":
        return create_stage_app()
    raise AttributeError(name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Disklavier-Auto Stage Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--demo", action="store_true", help="Start immediately in Virtual Showcase Demo mode")
    args = parser.parse_args()

    if args.demo:
        os.environ["DISKLAVIER_STAGE_DEMO"] = "1"

    import uvicorn

    app = create_stage_app(demo_enabled=args.demo)
    uvicorn.run(app, host=args.host, port=args.port, workers=1, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
