"""
Layer 4: FastAPI Stage Server, 60fps WebSocket Broadcaster, Staggered Deliberation Timeline,
Python Direct Audio Synth, and Virtual Showcase Demo / Production Mode Orchestrator.

Pipeline order per turn:
L1 (Perception) -> L2 (Musical Policy) -> L3 (symusic 4-Candidate Generation & Scoring)
-> L2 (Factual Performer Deliberation Trace via Ollama + MusicalLogicValidator)
-> Staggered Audition & Rejection Timeline (Step 1 Sketch -> Step 2 Highlight/Reject -> Step 3 Adopt/Perform)
-> L5 (Disklavier 30-Voice Safe Output) + Python LocalAudioSynthesizer (when monitor_audio=True)
-> L4 (Stage Editorial Web HUD @ 60fps).
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import random
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
import numpy as np
from pydantic import BaseModel

from src.obsidian import ObsidianMusicalBrain
from src.stage.co_composition_architect import CoCompositionArchitect
from src.stage.config_scanner import DEFAULT_CONFIG_PATH, scan_ollama_environment
from src.stage.corpus_phrase_model import CorpusPhraseModelManager
from src.stage.favicon import PIANO_FAVICON_ICO_BYTES
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
from src.stage.local_audio_synth import LocalAudioSynthesizer
from src.stage.token_economizer import TokenEconomizer

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
STUDIO_HTML_PATH = REPO_ROOT / "src" / "ui" / "studio.html"
WEB_DEMO_DIR = REPO_ROOT / "web-demo"

# Concrete musical inquiry scenarios (zero hardcoded "〇〇風" composer/style presets)
STAGE_INQUIRY_SCENARIOS: Dict[str, Dict[str, Any]] = {
    "cello_low_legato": {
        "label_ja": "チェロ低音レガート問いかけ",
        "directive_text": "低音レガートに対し、高音開離ボイシングとウナ・コルダ踏み替えで応答",
        "tonal_hint": "D_dorian",
        "stance_hint": "imitate",
        "pedal_hint": "resonant",
        "density_delta": -0.05,
        "mode": "cello",
    },
    "cello_leap_spiccato": {
        "label_ja": "チェロ跳躍スピッカート",
        "directive_text": "鋭い跳躍スピッカートに対し、反行対位法と浅い踏み替えで応対",
        "tonal_hint": "G_minor",
        "stance_hint": "counterpoint",
        "pedal_hint": "dry",
        "density_delta": 0.10,
        "mode": "cello",
    },
    "piano_chromatic_cadence": {
        "label_ja": "ピアノ半音階終止",
        "directive_text": "半音階的な終止進行を受け、内声掛留解決とハーフペダルで接続",
        "tonal_hint": "D_minor",
        "stance_hint": "cadence",
        "pedal_hint": "half_clear",
        "density_delta": -0.08,
        "mode": "piano",
    },
    "overtone_mud_clearance": {
        "label_ja": "中音域倍音クリアランス検証",
        "directive_text": "100-600Hzの濁りを完全に避け、バス支えと高域透明和音で構築",
        "tonal_hint": "F_major",
        "stance_hint": "provoke",
        "pedal_hint": "half_clear",
        "density_delta": 0.08,
        "mode": "cello",
    },
}


def _load_stage_config() -> Dict[str, Any]:
    if yaml is not None and DEFAULT_CONFIG_PATH.exists():
        try:
            data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def _probe_gpu_vram_gb_nonblocking() -> Optional[float]:
    """Queries live RTX VRAM usage without importing heavy modules if not already loaded."""
    torch_mod = sys.modules.get("torch")
    if torch_mod is not None:
        try:
            if torch_mod.cuda.is_available():
                free_b, total_b = torch_mod.cuda.mem_get_info(0)
                used_b = max(0, int(total_b) - int(free_b))
                if used_b > 0:
                    return round(used_b / (1024.0 ** 3), 1)
        except Exception:
            pass
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
            creationflags=flags,
        )
        if res.returncode == 0 and res.stdout.strip():
            mib = float(res.stdout.strip().splitlines()[0].strip())
            return round(mib / 1024.0, 1)
    except Exception:
        pass
    return None


_TONALITY_SHORT_MAP: Dict[str, str] = {
    "D_minor": "Dm",
    "D_dorian": "Ddor",
    "F_major": "F",
    "A_minor": "Am",
    "C_major": "C",
    "G_minor": "Gm",
    "Eb_major": "Eb",
    "Bb_major": "Bb",
    "E_minor": "Em",
    "G_major": "G",
    "C_minor": "Cm",
    "Ab_major": "Ab",
    "B_minor": "Bm",
    "D_major": "D",
    "F_minor": "Fm",
    "A_major": "A",
    "F#_minor": "F#m",
    "E_major": "E",
}


class ModeSwitchReq(BaseModel):
    mode: str  # "piano" | "cello"


class DemoToggleReq(BaseModel):
    enabled: bool


class AudioMonitorReq(BaseModel):
    enabled: bool
    audition_preview: Optional[bool] = None


class DirectiveReq(BaseModel):
    text: str = ""


class PresetReq(BaseModel):
    preset: str


class SynthesizeWorkReq(BaseModel):
    play_audio: bool = False


class StageOrchestrator:
    """
    Runs the full L1 -> L2 Policy -> L3 4-Candidate Scoring -> L2 Deliberation Trace
    -> Staggered Audition Timeline -> L5 + Python Audio Synth -> L4 HUD pipeline.
    """

    def __init__(
        self,
        demo_enabled: bool = False,
        monitor_audio: Optional[bool] = None,
        production_mode: bool = False,
        corpus_dirs: Optional[List[Path]] = None,
        use_ollama: Optional[bool] = None,
        open_hardware: bool = True,
        play_audio_device: bool = True,
    ) -> None:
        cfg = _load_stage_config()
        llm_cfg = cfg.get("llm", {})
        midi_cfg = cfg.get("midi", {})

        self.production_mode: bool = bool(production_mode)
        self.mode: str = "cello"  # Default Mode B ("cello") or Mode A ("piano")
        self.demo_enabled: bool = False if self.production_mode else bool(demo_enabled)
        if monitor_audio is None:
            self.monitor_audio: bool = False if self.production_mode else bool(self.demo_enabled)
        else:
            self.monitor_audio = False if self.production_mode else bool(monitor_audio)
        self.play_audio_device: bool = bool(play_audio_device)

        self.phase: str = "listening"
        self.deliberation_stage: str = "idle"  # idle | step1_sketch | step2_audition_reject | step3_adopt_perform
        self.deliberation_active_reject_id: str = ""
        self.deliberation_timeline: List[Dict[str, Any]] = []
        self.active_preset: str = "cello_low_legato"
        self.turn_id: int = 0
        self.obsidian_last_note: str = ""
        self.calibration_status: Dict[str, Any] = {
            "calibrated": False,
            "vdl_base_ms": 24.0,
            "nlms_erle_db": 0.0,
            "timestamp": 0.0,
        }
        self._lock = threading.RLock()

        # L1 Perception
        self.echo_ledger = EchoSuppressionLedger(min_lag_s=0.0, max_lag_s=0.280)
        self.turn_detector = TurnTakingDetector(silence_threshold_s=0.75)
        self.nlms = NLMSEchoCanceller()
        self.cello_perception = CelloAcousticPerception(sample_rate=44100)
        self.directive_recognizer = VerbalDirectiveRecognizer()
        self.pending_directive_hints: Dict[str, Any] = {}
        self.human_phrase_buffer: List[Dict[str, Any]] = []

        # L2 Intent + TokenEconomizer
        selected_model = str(llm_cfg.get("selected_model") or "qwen3.5:4b")
        if use_ollama is None:
            ollama_ready = bool(llm_cfg.get("ollama_running", False))
        else:
            ollama_ready = bool(use_ollama)

        self.economizer = TokenEconomizer()
        self.intent_engine = MusicalIntentEngine(
            model_name=selected_model,
            use_ollama=ollama_ready,
            timeout_s=8.0,
            economizer=self.economizer,
        )
        if use_ollama is None and not ollama_ready:
            def _probe_ollama() -> None:
                info = scan_ollama_environment()
                if info.get("running") and info.get("selected_model"):
                    self.intent_engine.model_name = str(info["selected_model"])
                    self.intent_engine.use_ollama = True

            threading.Thread(target=_probe_ollama, name="OllamaAsyncProbe", daemon=True).start()

        # L3 Multiple Candidate Composer, RTX 3070 Corpus Phrase Model & CoCompositionArchitect
        self.corpus_loader = ZeroConfigCorpusLoader(search_dirs=corpus_dirs)
        self.phrase_model = CorpusPhraseModelManager()
        self.composer = MultipleCandidateComposer(
            corpus_loader=self.corpus_loader,
            phrase_model=self.phrase_model,
        )
        self.architect = CoCompositionArchitect()
        self.latest_macro_state: Dict[str, Any] = self.architect.get_macro_state_snapshot()
        self.latest_synthesized_work: Optional[Dict[str, Any]] = None
        self.turn_revision: int = 0

        # Obsidian Vault Second Brain Integration
        self.obsidian_brain: Optional[ObsidianMusicalBrain] = None
        try:
            self.obsidian_brain = ObsidianMusicalBrain(on_directives_changed=self._on_obsidian_directive)
        except Exception:
            self.obsidian_brain = None

        # L5 Safe Disklavier Output
        out_port_pref = midi_cfg.get("preferred_out_port") if open_hardware else None
        self.output_layer = DisklavierStageOutputLayer(
            port_name=out_port_pref,
            midi_sink=self._on_l5_midi_event,
            echo_register_cb=self.echo_ledger.register_sent_note,
            ai_cc67_cb=self.turn_detector.notify_ai_cc67,
            start_worker=True,
        )

        # Python Direct Audio Synthesizer (ensures immediate sound on speakers in Demo/Monitor mode)
        self.audio_synth = LocalAudioSynthesizer(
            enabled=self.monitor_audio,
            use_midi_synth_fallback=(not self.production_mode),
        )
        self.latest_audio_stats: Dict[str, Any] = self.audio_synth.get_status()

        # Optional MIDI IN listener for Mode A
        self.inport: Any = None
        if open_hardware and mido is not None:
            self._try_open_midi_in(str(midi_cfg.get("preferred_in_port") or "Disklavier"))

        # Latest state snapshots for L4 HUD
        self.latest_perception = PerceptionSnapshot(mode=self.mode, timestamp=time.perf_counter())
        self.latest_intent = self.intent_engine.decide_policy(self.latest_perception, [], {})
        self.latest_candidates: List[CandidatePhrase] = []
        self.recent_strikes: List[Dict[str, Any]] = []
        self.recent_human_demo_notes: List[Dict[str, Any]] = []

        # Motif Memory Graph (starts at 0 nodes at curtain rise)
        self.motif_nodes: List[Dict[str, Any]] = []
        self.motif_edges: List[Dict[str, Any]] = []
        self._last_human_node_id: Optional[str] = None
        self._last_ai_node_id: Optional[str] = None

        # Background Demo / Turn Loop & Lock-free GPU VRAM Monitor
        self._running: bool = True
        self._demo_step_idx: int = 0
        self._gpu_vram_gb: Optional[float] = None
        self._thread = threading.Thread(
            target=self._orchestration_loop,
            name="StageOrchestratorLoop",
            daemon=True,
        )
        self._thread.start()
        self._vram_thread = threading.Thread(
            target=self._vram_poll_loop,
            name="GpuVramPoll",
            daemon=True,
        )
        self._vram_thread.start()

    def _vram_poll_loop(self) -> None:
        while self._running:
            self._gpu_vram_gb = _probe_gpu_vram_gb_nonblocking()
            for _ in range(150):
                if not self._running:
                    break
                time.sleep(0.1)

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
            if self.production_mode:
                self.demo_enabled = False
            else:
                self.demo_enabled = bool(enabled)
                if self.demo_enabled and not self.monitor_audio:
                    self.monitor_audio = True
                    self.audio_synth.set_enabled(True)
        return {"ok": True, "demo_enabled": self.demo_enabled, "monitor_audio": self.monitor_audio}

    def set_audio_monitor(self, enabled: bool, audition_preview: Optional[bool] = None) -> Dict[str, Any]:
        with self._lock:
            if self.production_mode:
                self.monitor_audio = False
                self.audio_synth.set_enabled(False)
            else:
                self.monitor_audio = bool(enabled)
                self.audio_synth.set_enabled(self.monitor_audio)
                if audition_preview is not None:
                    self.audio_synth.set_audition_preview(bool(audition_preview))
            self.latest_audio_stats = self.audio_synth.get_status()
            return {"ok": True, "monitor_audio": self.monitor_audio, **self.latest_audio_stats}

    def _on_obsidian_directive(self, parsed: Dict[str, Any]) -> None:
        raw = str(parsed.get("raw_text", "") or "").strip()
        if raw:
            hints = self.directive_recognizer.parse_directive(raw)
            with self._lock:
                self.pending_directive_hints = hints

    def apply_directive(self, text: str) -> Dict[str, Any]:
        hints = self.directive_recognizer.parse_directive(text)
        with self._lock:
            self.pending_directive_hints = hints
            self.latest_perception.verbal_directive = hints.get("raw_text", "")
        return {"ok": True, "hints": hints}

    def apply_preset(self, preset_key: str) -> Dict[str, Any]:
        preset = STAGE_INQUIRY_SCENARIOS.get(preset_key)
        if not preset:
            return {"ok": False, "error": f"Unknown scenario: {preset_key}"}
        hints = {
            "raw_text": preset["directive_text"],
            "tonal_hint": preset["tonal_hint"],
            "stance_hint": preset["stance_hint"],
            "pedal_hint": preset["pedal_hint"],
            "density_delta": preset["density_delta"],
        }
        with self._lock:
            self.active_preset = preset_key
            if preset.get("mode") in ("cello", "piano"):
                self.mode = str(preset["mode"])
                self.latest_perception.mode = self.mode
            self.pending_directive_hints = hints
            self.latest_perception.verbal_directive = preset["directive_text"]
        return {"ok": True, "preset": preset_key, "label_ja": preset["label_ja"], "hints": hints}

    def run_calibration(self) -> Dict[str, Any]:
        """
        Pre-concert calibration check: verifies 2ch NLMS echo attenuation on synthetic
        reference signal and physical VDL strike lag baseline.
        """
        sr = 44100
        t = np.arange(int(sr * 0.5), dtype=np.float64) / sr
        ref = 0.28 * np.sin(2.0 * np.pi * 293.66 * t) + 0.16 * np.sin(2.0 * np.pi * 440.0 * t)
        mic = 0.72 * ref
        nlms = NLMSEchoCanceller()
        _ = nlms.process_block(mic, ref)
        res = {
            "ok": True,
            "calibrated": True,
            "vdl_base_ms": self.output_layer.offset_ms(60, 72),
            "nlms_erle_db": nlms.last_erle_db,
            "connected_port": self.output_layer.connected_port_name or "Virtual Disklavier",
            "timestamp": round(time.perf_counter(), 3),
        }
        with self._lock:
            self.calibration_status = res
        return res

    def reset_performance(self) -> Dict[str, Any]:
        with self._lock:
            self.motif_nodes.clear()
            self.motif_edges.clear()
            self._last_human_node_id = None
            self._last_ai_node_id = None
            self.turn_id = 0
            self.turn_revision = 0
            self._demo_step_idx = 0
            self.human_phrase_buffer.clear()
            self.latest_candidates = []
            self.deliberation_stage = "idle"
            self.deliberation_active_reject_id = ""
            self.deliberation_timeline = []
            self.architect.reset()
            self.latest_macro_state = self.architect.get_macro_state_snapshot()
            self.latest_synthesized_work = None
        self.audio_synth.stop_audio()
        self.output_layer.panic()
        return {"ok": True, "motif_nodes": 0}

    def _simulate_virtual_human_turn(self, step_idx: int) -> tuple[PerceptionSnapshot, List[Dict[str, Any]]]:
        now = time.perf_counter()
        rng = random.Random(step_idx + 101)
        pos_in_arc = (max(1, step_idx) - 1) % 8
        if self.mode == "cello":
            # Coherent 8-turn 4-act thematic journey derived from Turn 1 Seed Motif (D3-F3-A3-G3-E3)
            cello_motifs = [
                ([50, 53, 57, 55, 52, 50], "legato", "D_dorian"),     # Turn 1 (Act 1): Seed Motif statement
                ([50, 57, 55, 53, 52, 57], "staccato", "D_dorian"),   # Turn 2 (Act 1): Question response on 5th
                ([53, 57, 60, 58, 57, 55], "legato", "F_major"),      # Turn 3 (Act 2): Sequence into relative F major
                ([55, 58, 62, 65, 63, 62], "staccato", "G_minor"),    # Turn 4 (Act 2): Subdominant G minor rise
                ([57, 60, 64, 62, 60, 59], "legato", "A_minor"),      # Turn 5 (Act 2): Dominant A minor modulation
                ([57, 62, 65, 69, 67, 65], "staccato", "D_minor"),    # Turn 6 (Act 3): Climactic leap to high F4/A4
                ([58, 62, 65, 64, 62, 69], "legato", "D_minor"),      # Turn 7 (Act 3): Broad chamber summit
                ([50, 53, 57, 55, 52, 50], "legato", "D_dorian"),     # Turn 8 (Act 4): Calm Act 1 Seed Motif recall
            ]
            pitches, target_art, _ = cello_motifs[pos_in_arc]
            sr = 44100
            f0_main = 440.0 * (2.0 ** ((pitches[-1] - 69) / 12.0))
            n_samp = int(sr * 0.65)
            t = np.arange(n_samp, dtype=np.float64) / sr

            phase = 2.0 * np.pi * f0_main * np.cumsum(1.0 + 0.004 * np.sin(2.0 * np.pi * 5.4 * t)) / sr
            cello = (
                0.28 * np.sin(phase)
                + 0.14 * np.sin(2.0 * phase)
                + 0.07 * np.sin(3.0 * phase)
            )
            if target_art == "staccato":
                env = np.zeros_like(t)
                for k in range(3):
                    s = int((k * 0.21) * sr)
                    e = min(n_samp, s + int(0.08 * sr))
                    if e > s:
                        seg_t = np.linspace(0.0, 1.0, e - s)
                        env[s:e] = np.exp(-5.0 * seg_t) * np.minimum(1.0, seg_t * 14.0)
                cello *= env
            else:
                cello *= np.minimum(1.0, t / 0.08) * (0.9 + 0.1 * np.sin(2.0 * np.pi * 1.5 * t))

            piano_ref = 0.25 * np.sin(2.0 * np.pi * 329.63 * t) + 0.15 * np.sin(2.0 * np.pi * 493.88 * t)
            bleed = 0.72 * piano_ref
            mic = cello + bleed

            snap = self.cello_perception.analyze_utterance(mic, piano_ref, nlms=self.nlms)
            pure_erle = NLMSEchoCanceller.measure_bleed_erle_db(bleed, piano_ref)
            self.nlms.last_erle_db = pure_erle
            snap.nlms_erle_db = pure_erle
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
            piano_motifs = [
                [62, 65, 69, 67, 64, 62],     # Turn 1 (Act 1): Seed Motif in D minor
                [62, 69, 67, 65, 64, 69],     # Turn 2 (Act 1): Question continuation
                [65, 69, 72, 70, 69, 67],     # Turn 3 (Act 2): Sequence into F major
                [67, 70, 74, 72, 69, 67],     # Turn 4 (Act 2): Sequence into G minor
                [69, 72, 76, 74, 72, 71],     # Turn 5 (Act 2): Dominant A minor
                [69, 74, 77, 81, 79, 77],     # Turn 6 (Act 3): High lyrical climax
                [70, 74, 77, 76, 74, 81],     # Turn 7 (Act 3): Peak duet exchange
                [62, 65, 69, 67, 64, 62],     # Turn 8 (Act 4): Calm Act 1 Seed Motif recall
            ]
            pitches = piano_motifs[pos_in_arc]
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
                turn_ready=bool(ready or gauge >= 0.68 or len(human_notes) >= 2),
                turn_reason=reason,
                pedal_cc64=44 if step_idx % 2 == 0 else 12,
                pedal_cc67=90 if step_idx % 2 == 1 else 0,
                echo_suppressed_count=self.echo_ledger.suppressed_count,
                human_notes_count=self.echo_ledger.human_accepted_count,
                recent_human_notes=human_notes,
                pitch_history=[round(440.0 * (2.0 ** ((p - 69) / 12.0)), 2) for p in pitches],
            )
            return snap, human_notes

    @staticmethod
    def _build_deliberation_timeline(candidates: List[CandidatePhrase], fast_mode: bool = False) -> List[Dict[str, Any]]:
        """
        Creates the staggered audition & rejection sequence (0.5s - 1.2s total, or accelerated in live fast mode).
        Step 1: Sketch all 4 staves
        Step 2: Highlight problem notes in vermilion, strike out and fade each rejected candidate one by one
        Step 3: Lock adopted candidate in parchment off-white and trigger L5 performance
        """
        scale = 0.45 if fast_mode else 1.0
        rejected = [c for c in candidates if not c.adopted]
        adopted = next((c for c in candidates if c.adopted), None)

        steps: List[Dict[str, Any]] = [
            {
                "stage": "step1_sketch",
                "at_ms": 0,
                "label_ja": "Step 1 着想: 4候補の五線譜を並列スケッチ",
                "active_reject_id": "",
                "rejected_so_far": [],
                "adopted_locked": False,
            }
        ]
        t_ms = int(220 * scale)
        rejected_so_far: List[str] = []
        for rej in rejected:
            rejected_so_far = [*rejected_so_far, rej.candidate_id]
            steps.append(
                {
                    "stage": "step2_audition_reject",
                    "at_ms": t_ms,
                    "label_ja": f"Step 2 推敲: 案{rej.candidate_id}を検証 → {(rej.rejection_detail or {}).get('hud_badge_ja', rej.rejection_tag_ja)}",
                    "active_reject_id": rej.candidate_id,
                    "rejected_so_far": list(rejected_so_far),
                    "adopted_locked": False,
                }
            )
            t_ms += int(240 * scale)

        steps.append(
            {
                "stage": "step3_adopt_perform",
                "at_ms": t_ms,
                "label_ja": f"Step 3 確定実演: 案{adopted.candidate_id if adopted else 'A'}をロックし打鍵開始",
                "active_reject_id": "",
                "rejected_so_far": [r.candidate_id for r in rejected],
                "adopted_locked": True,
            }
        )
        return steps

    def execute_composition_turn(
        self,
        use_virtual_human: bool = True,
        staggered_playback: bool = False,
        explicit_retake: bool = False,
    ) -> Dict[str, Any]:
        with self._lock:
            hints = dict(self.pending_directive_hints)
            is_retake_req = bool(
                self.architect.turn_history
                and (explicit_retake or self.architect.detect_retake_request(str(hints.get("raw_text") or "")))
            )

            self.phase = "deliberating"
            self.deliberation_stage = "step1_sketch"

            if is_retake_req and self.recent_human_demo_notes:
                human_notes = [dict(n) for n in self.recent_human_demo_notes]
                snap = self.latest_perception
                snap.breath_gauge = 1.0
                snap.turn_ready = True
            else:
                self._demo_step_idx += 1
                step_idx = self._demo_step_idx
                if use_virtual_human or not self.human_phrase_buffer:
                    snap, human_notes = self._simulate_virtual_human_turn(step_idx)
                    self.recent_human_demo_notes = [dict(n) for n in human_notes]
                else:
                    human_notes = list(self.human_phrase_buffer[-8:])
                    self.human_phrase_buffer.clear()
                    self.recent_human_demo_notes = [dict(n) for n in human_notes]
                    snap = self.latest_perception
                    snap.breath_gauge = 1.0
                    snap.turn_ready = True

            if hints.get("raw_text"):
                snap.verbal_directive = str(hints["raw_text"])

        # Step 0: Macro-Form 4-Act Architecture Planning & Voice-Leading Bridge
        macro_plan = self.architect.plan_turn_architecture(
            human_notes=human_notes,
            directive_hints=hints,
            mode=snap.mode,
            explicit_retake=is_retake_req,
        )

        # Step 1: L2 decides autonomous musical policy via TokenEconomizer + Ollama/Rules
        corpus_refs = self.corpus_loader.retrieve_voicing_references(
            tension=0.5, momentum=0.5, top_k=3
        )
        policy_intent = self.intent_engine.decide_policy(
            snap,
            human_notes,
            hints,
            corpus_refs=corpus_refs,
            macro_plan=macro_plan,
        )

        # Step 2: L3 generates 4 two-hand polyphonic candidates + RTX 3070 Transformer Blueprint + Masterwork Audit
        turn_res = self.composer.compose_turn(
            human_notes,
            policy_intent,
            human_mode=snap.mode,
            macro_plan=macro_plan,
        )
        adopted = turn_res.adopted_candidate
        rejected = [c for c in turn_res.candidates if not c.adopted]

        # Step 3: L2 grounds the performer deliberation memo strictly in L1 & L3 real data
        final_intent = self.intent_engine.finalize_deliberation_monologue(
            snap,
            human_notes,
            policy_intent,
            turn_res,
            hints,
            corpus_refs=turn_res.corpus_refs_used,
            macro_plan=macro_plan,
        )
        timeline = self._build_deliberation_timeline(
            turn_res.candidates, fast_mode=self.production_mode
        )

        # Commit turn into CoCompositionArchitect's macro-form history (supports in-place Retake)
        cand_dicts = [
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
                "pitch_range_str": c.pitch_range_str,
                "masterwork_total": c.masterwork_total,
                "masterwork_pct": c.masterwork_pct,
            }
            for c in turn_res.candidates
        ]
        adopted_dict = next((c for c in cand_dicts if c["adopted"]), cand_dicts[0])
        self.architect.commit_turn(
            plan=macro_plan,
            mode=snap.mode,
            human_notes=human_notes,
            intent_dict=final_intent.model_dump(),
            candidates=cand_dicts,
            adopted=adopted_dict,
            directive_text=str(hints.get("raw_text") or ""),
        )

        # Publish Step 1 -> Step 2 -> Step 3 intermediate deliberation states for live WebSocket observers
        with self._lock:
            self.turn_id = macro_plan.turn_number
            self.turn_revision += 1
            self.latest_perception = snap
            self.latest_intent = final_intent
            self.latest_candidates = turn_res.candidates
            self.latest_macro_state = self.architect.get_macro_state_snapshot()
            self.deliberation_timeline = timeline
            self.deliberation_stage = "step1_sketch"
            self.deliberation_active_reject_id = ""
            self.pending_directive_hints = {}

        if staggered_playback:
            for item in timeline:
                with self._lock:
                    self.deliberation_stage = str(item["stage"])
                    self.deliberation_active_reject_id = str(item["active_reject_id"])
                if item["stage"] == "step2_audition_reject":
                    time.sleep(0.08 if self.production_mode else 0.18)
                elif item["stage"] == "step1_sketch":
                    time.sleep(0.05 if self.production_mode else 0.14)

        # Step 4: Synthesize Python direct audio (dual-pedal physics) & schedule L5 output
        audio_stats = self.audio_synth.play_turn_audio(
            human_notes=human_notes,
            human_mode=snap.mode,
            human_articulation=snap.articulation,
            rejected_candidates=rejected,
            adopted_events=adopted.events,
            play_device=(self.monitor_audio and self.play_audio_device),
        )
        self.output_layer.schedule_phrase(adopted.events, base_sound_ts=time.perf_counter() + 0.10)

        # Update latest perception pedal state to reflect active dual-pedal drive for HUD gauges
        if adopted.pedal_cc64_curve:
            c64_vals = [int(pt["val"]) for pt in adopted.pedal_cc64_curve]
            snap.pedal_cc64 = max(c64_vals) if max(c64_vals) > 0 else 0
        if adopted.pedal_cc67_curve:
            c67_vals = [int(pt["val"]) for pt in adopted.pedal_cc67_curve]
            snap.pedal_cc67 = max(c67_vals) if max(c67_vals) > 0 else 0

        # Step 5: Archive turn deliberation & voicing trace to Obsidian Vault
        if self.obsidian_brain is not None:
            try:
                c64_list = [int(pt["val"]) for pt in adopted.pedal_cc64_curve] or [0]
                c67_list = [int(pt["val"]) for pt in adopted.pedal_cc67_curve] or [0]
                note_path = self.obsidian_brain.record_stage_dialogue_turn(
                    turn_id=macro_plan.turn_number,
                    mode=snap.mode,
                    human_summary=self.economizer.extract_human_phrase_metrics(snap, human_notes),
                    token_stats=self.intent_engine.last_token_stats,
                    intent_data=final_intent.model_dump(),
                    candidates=[
                        {
                            "candidate_id": c.candidate_id,
                            "adopted": c.adopted,
                            "total_score": c.total_score,
                            "pitch_range_str": c.pitch_range_str,
                            "rejection_tag_ja": c.rejection_tag_ja,
                            "rejection_detail": c.rejection_detail,
                        }
                        for c in turn_res.candidates
                    ],
                    pedal_summary={
                        "cc64_min": min(c64_list),
                        "cc64_max": max(c64_list),
                        "cc67_min": min(c67_list),
                        "cc67_max": max(c67_list),
                        "repedal_count": audio_stats.get("repedal_count", 0),
                    },
                    session_tag="demo" if self.demo_enabled else ("prod" if self.production_mode else "stage"),
                )
                self.obsidian_last_note = note_path.name
            except Exception:
                pass

        with self._lock:
            self.deliberation_stage = "step3_adopt_perform"
            self.deliberation_active_reject_id = ""
            self.phase = "performing"
            self.latest_audio_stats = audio_stats

            # Grow Motif Memory Graph (starts at 0 nodes at curtain rise)
            h_id = f"H{self.turn_id}"
            a_id = f"A{self.turn_id}"
            h_pitches = [int(n["pitch"]) for n in human_notes[:4]]
            a_pitches = [int(e["pitch"]) for e in adopted.events if int(e.get("pitch", 0)) > 0][:4]

            # Update existing node if this was an in-place Retake
            if macro_plan.is_retake:
                self.motif_nodes = [n for n in self.motif_nodes if n["id"] not in (h_id, a_id)]
                self.motif_edges = [
                    e for e in self.motif_edges if e["target"] not in (h_id, a_id) and e["source"] != a_id
                ]

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
            ton_short = _TONALITY_SHORT_MAP.get(final_intent.tonal_center, str(final_intent.tonal_center).replace("_", " "))
            a_motif_str = "-".join(midi_to_note_name(p) for p in a_pitches[:3])
            self.motif_nodes.append(
                {
                    "id": a_id,
                    "kind": "ai",
                    "turn": self.turn_id,
                    "label": f"AI [{adopted.candidate_id}] Act{macro_plan.act_number} {ton_short} {a_motif_str}",
                    "pitches": a_pitches,
                    "stance": final_intent.interaction_stance,
                }
            )
            self.motif_edges.append(
                {
                    "source": h_id,
                    "target": a_id,
                    "relation": final_intent.interaction_stance,
                }
            )
            if self._last_ai_node_id is not None and self._last_ai_node_id != a_id:
                self.motif_edges.append(
                    {
                        "source": self._last_ai_node_id,
                        "target": h_id,
                        "relation": "dialogue_flow",
                    }
                )
            self._last_human_node_id = h_id
            self._last_ai_node_id = a_id
            if len(self.motif_nodes) > 24:
                keep_ids = {n["id"] for n in self.motif_nodes[-24:]}
                self.motif_nodes = self.motif_nodes[-24:]
                self.motif_edges = [
                    e for e in self.motif_edges if e["source"] in keep_ids and e["target"] in keep_ids
                ]

        return self.get_hud_state()

    def retake_last_turn(self, directive_text: str = "") -> Dict[str, Any]:
        """Public Rehearsal: rewrites the most recent AI phrase in-place following human critique."""
        if not self.architect.turn_history:
            return self.execute_composition_turn(use_virtual_human=True, staggered_playback=False)
        text = directive_text.strip() or "今のフレーズはまだ解決しない問いかけのまま、左手の内声を増強して書き直して"
        self.apply_directive(text)
        return self.execute_composition_turn(
            use_virtual_human=True,
            staggered_playback=False,
            explicit_retake=True,
        )

    def synthesize_complete_work(
        self,
        out_dir: Optional[Path] = None,
        basename: Optional[str] = None,
        play_audio: bool = False,
    ) -> Dict[str, Any]:
        """
        Synthesizes all recorded turns into ONE completed masterwork with LLM title naming,
        concert-hall stereo WAV & MP3 rendering, SMF Type-1 MIDI, MusicXML Grand Staff,
        and Obsidian Vault archival.
        """
        if not self.architect.turn_history:
            self.execute_composition_turn(use_virtual_human=True, staggered_playback=False)

        work = self.architect.synthesize_complete_masterwork(
            mode=self.mode,
            title_generator_fn=self.intent_engine.generate_work_title,
        )
        stereo_audio, audio_metrics = self.audio_synth.render_masterwork_stereo_audio(work)

        export_dir = out_dir or (REPO_ROOT / "data" / "telemetry" / "exports")
        stem = basename or f"masterwork_{self.mode}_{int(time.time())}"
        mid_path = self.architect.export_masterwork_midi(work, export_dir / f"{stem}.mid")
        xml_path = self.architect.export_masterwork_musicxml(work, export_dir / f"{stem}.musicxml")
        audio_files = self.audio_synth.export_stereo_wav_and_mp3(
            stereo_audio,
            out_wav_path=export_dir / f"{stem}.wav",
            out_mp3_path=export_dir / f"{stem}.mp3",
            mp3_bitrate_kbps=128,
        )

        asset_rel = {
            "mid": mid_path.name,
            "musicxml": xml_path.name,
            "wav": Path(audio_files["wav_path"]).name,
            "mp3": Path(audio_files["mp3_path"]).name if audio_files.get("mp3_path") else "",
            "mp3_url": f"/demos/{Path(audio_files['mp3_path']).name}" if audio_files.get("mp3_path") else "",
            "wav_url": f"/demos/{Path(audio_files['wav_path']).name}",
            "mid_url": f"/demos/{mid_path.name}",
            "musicxml_url": f"/demos/{xml_path.name}",
        }

        if self.obsidian_brain is not None:
            try:
                obs_path = self.obsidian_brain.record_completed_masterwork(
                    work=work,
                    turn_history=self.architect.turn_history,
                    asset_paths=asset_rel,
                    session_tag="demo" if self.demo_enabled else ("prod" if self.production_mode else "stage"),
                )
                self.obsidian_last_note = obs_path.name
            except Exception:
                pass

        if play_audio and self.monitor_audio and self.play_audio_device:
            try:
                import sounddevice as sd

                sd.stop()
                sd.play(stereo_audio, samplerate=self.audio_synth.sample_rate, blocking=False)
            except Exception:
                pass

        summary = {
            "ok": True,
            "title_ja": work["title_ja"],
            "title_en": work["title_en"],
            "mode": work["mode"],
            "home_tonality": work["home_tonality"],
            "seed_motif": work["seed_motif"],
            "total_duration_s": audio_metrics["duration_s"],
            "turns_count": work["turns_count"],
            "retakes_count": work["retakes_count"],
            "preflight_passed": bool(work["preflight_report"].get("passed", True)),
            "audio_metrics": audio_metrics,
            "assets": asset_rel,
            "obsidian_note": self.obsidian_last_note,
        }
        with self._lock:
            self.latest_synthesized_work = summary
            self.latest_macro_state = self.architect.get_macro_state_snapshot()
        return summary

    def _orchestration_loop(self) -> None:
        last_turn_t = 0.0
        while self._running:
            now = time.perf_counter()
            if self.demo_enabled and not self.production_mode and (now - last_turn_t) >= 4.2:
                last_turn_t = now
                try:
                    self.execute_composition_turn(use_virtual_human=True, staggered_playback=True)
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
                        self.execute_composition_turn(use_virtual_human=False, staggered_playback=False)
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
                }
                for c in self.latest_candidates
            ]
            hw_port = out_tel.connected_port
            audio_status_label = (
                f"実機MIDI出力: {hw_port}"
                if (self.production_mode or hw_port)
                else (
                    "音声出力中: Concert Hall Synth + WebAudio"
                    if self.monitor_audio
                    else "モニター消音中 (WebAudio待機)"
                )
            )
            return {
                "timestamp": round(time.perf_counter(), 4),
                "mode": self.mode,
                "production_mode": self.production_mode,
                "demo_enabled": self.demo_enabled,
                "monitor_audio": self.monitor_audio,
                "active_preset": self.active_preset,
                "phase": self.phase,
                "deliberation_stage": self.deliberation_stage,
                "deliberation_active_reject_id": self.deliberation_active_reject_id,
                "deliberation_timeline": list(self.deliberation_timeline),
                "turn_id": self.turn_id,
                "turn_revision": self.turn_revision,
                "macro_form": dict(self.latest_macro_state),
                "synthesized_work": dict(self.latest_synthesized_work) if self.latest_synthesized_work else None,
                "corpus_source": self.corpus_loader.source_mode,
                "phrase_model_device": self.phrase_model.device if self.phrase_model.loaded else "fallback",
                "gpu_vram_gb": self._gpu_vram_gb,
                "calibration": dict(self.calibration_status),
                "audio_status": {
                    **self.latest_audio_stats,
                    "monitor_audio": self.monitor_audio,
                    "label_ja": audio_status_label,
                },
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
                    "validator_rewrites": self.intent_engine.last_validator_rewrites,
                    "token_economizer": self.intent_engine.last_token_stats,
                },
                "obsidian_last_note": self.obsidian_last_note,
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
                    "pedal_cc67": out_tel.pedal_cc67,
                    "pedal_cc64_curve": out_tel.pedal_cc64_curve,
                    "pedal_cc67_curve": out_tel.pedal_cc67_curve,
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
        self.audio_synth.close()
        self.output_layer.close()
        if self.obsidian_brain is not None:
            try:
                self.obsidian_brain.close()
            except Exception:
                pass


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
                payload = await asyncio.to_thread(self.orchestrator.get_hud_state)
                dead: List[WebSocket] = []
                for ws in list(self.clients):
                    try:
                        await asyncio.wait_for(ws.send_json(payload), timeout=1.5)
                    except (asyncio.TimeoutError, TimeoutError):
                        # Slow consumer frame skip; keep client subscribed
                        continue
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    self.clients.discard(ws)
            await asyncio.sleep(1.0 / 33.0)


def build_stage_router(orch: StageOrchestrator, broadcaster: StageWSBroadcaster) -> APIRouter:
    router = APIRouter()

    @router.get("/favicon.ico", include_in_schema=False)
    async def serve_favicon() -> Response:
        return Response(content=PIANO_FAVICON_ICO_BYTES, media_type="image/x-icon")

    @router.get("/", response_class=HTMLResponse)
    @router.get("/studio", response_class=HTMLResponse)
    async def serve_studio() -> HTMLResponse:
        if STUDIO_HTML_PATH.exists():
            return HTMLResponse(STUDIO_HTML_PATH.read_text(encoding="utf-8"))
        if STAGE_HUD_HTML_PATH.exists():
            return HTMLResponse(STAGE_HUD_HTML_PATH.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>studio.html missing</h1>", status_code=404)

    @router.get("/stage", response_class=HTMLResponse)
    async def serve_stage_hud() -> HTMLResponse:
        if STAGE_HUD_HTML_PATH.exists():
            return HTMLResponse(STAGE_HUD_HTML_PATH.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>stage_hud.html missing</h1>", status_code=404)

    @router.get("/studio-static/{filename:path}", include_in_schema=False)
    async def serve_studio_static(filename: str) -> Response:
        safe_name = Path(filename).name
        if safe_name not in ("style.css", "app.js") or safe_name != filename or ".." in filename:
            return JSONResponse({"ok": False, "error": "Invalid asset"}, status_code=400)
        target = (WEB_DEMO_DIR / safe_name).resolve()
        if not target.exists() or target.parent != WEB_DEMO_DIR.resolve():
            return JSONResponse({"ok": False, "error": "Asset not found"}, status_code=404)
        mt = "text/css; charset=utf-8" if safe_name.endswith(".css") else "application/javascript; charset=utf-8"
        return FileResponse(str(target), media_type=mt)

    @router.get("/api/catalog")
    async def api_catalog() -> Response:
        cat_path = (WEB_DEMO_DIR / "data" / "catalog_100.json").resolve()
        if not cat_path.exists():
            return JSONResponse({"ok": False, "error": "Catalog not generated"}, status_code=404)
        return FileResponse(str(cat_path), media_type="application/json")

    @router.get("/api/pieces/{piece_id}")
    async def api_piece_detail(piece_id: str) -> Response:
        clean_id = Path(piece_id).stem
        if not clean_id or clean_id != piece_id or ".." in piece_id or "/" in piece_id or "\\" in piece_id:
            return JSONResponse({"ok": False, "error": "Invalid piece id"}, status_code=400)
        pieces_dir = (WEB_DEMO_DIR / "data" / "pieces").resolve()
        target = (pieces_dir / f"{clean_id}.json").resolve()
        if not target.exists() or target.parent != pieces_dir:
            return JSONResponse({"ok": False, "error": "Piece not found"}, status_code=404)
        return FileResponse(str(target), media_type="application/json")

    @router.get("/audio/{filename:path}", include_in_schema=False)
    async def serve_showcase_audio(filename: str) -> Response:
        safe_name = Path(filename).name
        if not safe_name or safe_name != filename or ".." in filename:
            return JSONResponse({"ok": False, "error": "Invalid filename"}, status_code=400)
        audio_dir = (WEB_DEMO_DIR / "audio").resolve()
        target = (audio_dir / safe_name).resolve()
        if not target.exists() or not target.is_file() or target.parent != audio_dir:
            return JSONResponse({"ok": False, "error": "Audio not found"}, status_code=404)
        media_map = {".mp3": "audio/mpeg", ".mid": "audio/midi", ".wav": "audio/wav"}
        return FileResponse(str(target), media_type=media_map.get(target.suffix.lower()))

    @router.get("/api/stage/state")
    async def api_stage_state() -> JSONResponse:
        return JSONResponse(orch.get_hud_state())

    @router.post("/api/stage/mode")
    async def api_stage_mode(req: ModeSwitchReq) -> JSONResponse:
        return JSONResponse(orch.set_mode(req.mode))

    @router.post("/api/stage/demo")
    async def api_stage_demo(req: DemoToggleReq) -> JSONResponse:
        res = orch.set_demo_enabled(req.enabled)
        if req.enabled and not orch.production_mode and orch.turn_id == 0:
            await asyncio.to_thread(
                orch.execute_composition_turn, use_virtual_human=True, staggered_playback=False
            )
        return JSONResponse(res)

    @router.post("/api/stage/audio_monitor")
    async def api_stage_audio_monitor(req: AudioMonitorReq) -> JSONResponse:
        return JSONResponse(orch.set_audio_monitor(req.enabled, req.audition_preview))

    @router.post("/api/stage/directive")
    async def api_stage_directive(req: DirectiveReq) -> JSONResponse:
        return JSONResponse(orch.apply_directive(req.text))

    @router.post("/api/stage/preset")
    async def api_stage_preset(req: PresetReq) -> JSONResponse:
        res = orch.apply_preset(req.preset)
        if res.get("ok"):
            state = await asyncio.to_thread(
                orch.execute_composition_turn, use_virtual_human=True, staggered_playback=False
            )
            return JSONResponse({**res, "state": state})
        return JSONResponse(res, status_code=400)

    @router.post("/api/stage/calibrate")
    async def api_stage_calibrate() -> JSONResponse:
        res = await asyncio.to_thread(orch.run_calibration)
        return JSONResponse(res)

    @router.post("/api/stage/turn")
    async def api_stage_turn(request: Request) -> JSONResponse:
        state = await asyncio.to_thread(
            orch.execute_composition_turn, use_virtual_human=True, staggered_playback=False
        )
        return JSONResponse({"ok": True, "state": state})

    @router.post("/api/stage/retake")
    async def api_stage_retake(req: Optional[DirectiveReq] = None) -> JSONResponse:
        text = req.text if req is not None else ""
        state = await asyncio.to_thread(orch.retake_last_turn, text)
        return JSONResponse({"ok": True, "state": state})

    @router.post("/api/stage/synthesize_work")
    async def api_stage_synthesize_work(req: Optional[SynthesizeWorkReq] = None) -> JSONResponse:
        play_audio = bool(req.play_audio) if req is not None else False
        summary = await asyncio.to_thread(orch.synthesize_complete_work, play_audio=play_audio)
        return JSONResponse({**summary, "state": orch.get_hud_state()})

    @router.get("/demos/{filename:path}", include_in_schema=False)
    async def serve_demo_or_export_file(filename: str) -> Response:
        safe_name = Path(filename).name
        if not safe_name or safe_name != filename or ".." in filename:
            return JSONResponse({"ok": False, "error": "Invalid filename"}, status_code=400)
        for folder in (REPO_ROOT / "docs" / "demos", REPO_ROOT / "data" / "telemetry" / "exports"):
            candidate = (folder / safe_name).resolve()
            if candidate.exists() and candidate.is_file() and candidate.parent == folder.resolve():
                media_map = {
                    ".mp3": "audio/mpeg",
                    ".wav": "audio/wav",
                    ".mid": "audio/midi",
                    ".musicxml": "application/vnd.recordare.musicxml+xml",
                }
                return FileResponse(str(candidate), media_type=media_map.get(candidate.suffix.lower()))
        return JSONResponse({"ok": False, "error": "File not found"}, status_code=404)

    @router.post("/api/stage/reset")
    async def api_stage_reset() -> JSONResponse:
        return JSONResponse(orch.reset_performance())

    @router.post("/api/stage/panic")
    async def api_stage_panic() -> JSONResponse:
        orch.audio_synth.stop_audio()
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
                    await asyncio.to_thread(
                        orch.execute_composition_turn, use_virtual_human=True, staggered_playback=False
                    )
                elif msg == "panic":
                    orch.audio_synth.stop_audio()
                    orch.output_layer.panic()
        except (WebSocketDisconnect, ConnectionResetError, OSError, asyncio.CancelledError):
            pass
        except Exception:
            pass
        finally:
            broadcaster.clients.discard(ws)

    return router


def create_stage_app(
    demo_enabled: Optional[bool] = None,
    monitor_audio: Optional[bool] = None,
    production_mode: Optional[bool] = None,
    corpus_dirs: Optional[List[Path]] = None,
    use_ollama: Optional[bool] = None,
    open_hardware: bool = True,
    play_audio_device: bool = True,
) -> FastAPI:
    env_prod = os.environ.get("DISKLAVIER_STAGE_PRODUCTION", "0") == "1"
    env_demo = os.environ.get("DISKLAVIER_STAGE_DEMO", "0") == "1"
    env_mon = os.environ.get("DISKLAVIER_MONITOR_AUDIO", "0") == "1"

    prod = env_prod if production_mode is None else bool(production_mode)
    demo = False if prod else (env_demo if demo_enabled is None else bool(demo_enabled))
    mon = False if prod else ((demo or env_mon) if monitor_audio is None else bool(monitor_audio))

    orch = StageOrchestrator(
        demo_enabled=demo,
        monitor_audio=mon,
        production_mode=prod,
        corpus_dirs=corpus_dirs,
        use_ollama=use_ollama,
        open_hardware=open_hardware,
        play_audio_device=play_audio_device,
    )
    broadcaster = StageWSBroadcaster(orch)

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        broadcaster.ensure_started()
        if orch.demo_enabled and not orch.production_mode and orch.turn_id == 0:
            await asyncio.to_thread(
                orch.execute_composition_turn, use_virtual_human=True, staggered_playback=False
            )
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
    parser.add_argument("--demo", action="store_true", help="Virtual Showcase Demo + Direct Audio ON")
    parser.add_argument("--monitor-audio", action="store_true", help="Enable Python Direct Audio Synthesizer")
    parser.add_argument("--production", action="store_true", help="Production Stage Mode (Hardware Only, Virtual Synth OFF)")
    args = parser.parse_args()

    if args.production:
        os.environ["DISKLAVIER_STAGE_PRODUCTION"] = "1"
        os.environ["DISKLAVIER_STAGE_DEMO"] = "0"
        os.environ["DISKLAVIER_MONITOR_AUDIO"] = "0"
    elif args.demo:
        os.environ["DISKLAVIER_STAGE_DEMO"] = "1"
        os.environ["DISKLAVIER_MONITOR_AUDIO"] = "1"

    import uvicorn

    app = create_stage_app(
        demo_enabled=args.demo and not args.production,
        monitor_audio=(args.monitor_audio or args.demo) and not args.production,
        production_mode=args.production,
    )
    uvicorn.run(app, host=args.host, port=args.port, workers=1, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
