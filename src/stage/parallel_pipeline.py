"""
6-Worker Parallel Real-Time Composition & Perception Pipeline (`src/stage/parallel_pipeline.py`).

Separates real-time performance into 6 non-blocking concurrent workers connected by bounded queues:
  - Worker 1: Ultra-low-latency Audio/MIDI I/O queues + independent L5 Disklavier Output Thread
  - Worker 2: Parallel Acoustic Perception & 2ch NLMS Worker (`PerceptionWorker`)
  - Worker 3: Async LLM Intent, Policy & Obsidian Archival Worker (`AsyncLLMIntentWorker`)
  - Worker 4: 4-Candidate Parallel Composer & Critic Pool (`MultipleCandidateComposer._candidate_pool`)
  - Worker 5: Speculative Pre-Generation Buffer (`SpeculativePreGenWorker`)
  - Worker 6: Async Concert-Hall Audio Synth Worker (`AudioRenderWorker`) + 60fps WebSocket Broadcaster
"""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
import hashlib
import queue
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import numpy as np

from src.stage.layer1_perception import (
    CelloAcousticPerception,
    EchoSuppressionLedger,
    NLMSEchoCanceller,
    PerceptionSnapshot,
    TurnTakingDetector,
    midi_to_note_name,
)


@dataclass
class WorkerTelemetrySnapshot:
    perception_latency_ms: float = 0.0
    parallel_compose_ms: float = 0.0
    sum_worker_ms: float = 0.0
    parallel_speedup_ratio: float = 1.0
    llm_ttft_ms: float = 0.0
    llm_latency_ms: float = 0.0
    l5_jitter_ms: float = 0.0
    l5_max_jitter_ms: float = 0.0
    speculative_hits: int = 0
    speculative_misses: int = 0
    speculative_ready: bool = False
    speculative_saved_ms: float = 0.0
    audio_render_ms: float = 0.0
    audio_queue_depth: int = 0
    active_workers: int = 6
    total_workers: int = 6
    last_turn_wall_ms: float = 0.0
    worker_status: Dict[str, str] = field(
        default_factory=lambda: {
            "w1_io": "running",
            "w2_perception": "running",
            "w3_llm_async": "running",
            "w4_composer_pool": "running",
            "w5_speculative": "running",
            "w6_audio_ws": "running",
        }
    )


@dataclass
class SpeculativeDraft:
    key: str
    mode: str
    created_ts: float
    last_human_pitch: int
    macro_plan: Any
    policy_intent: Any
    turn_result: Any
    final_intent: Any
    compose_ms: float


class PerceptionWorker:
    """
    Worker 2: Dedicated CPU thread draining raw MIDI IN and 2ch audio blocks from lock-free queues,
    executing EchoSuppressionLedger / TurnTakingDetector / NLMSEchoCanceller / CelloAcousticPerception
    without blocking L5 output or UI streaming.
    """

    def __init__(
        self,
        echo_ledger: EchoSuppressionLedger,
        turn_detector: TurnTakingDetector,
        nlms: NLMSEchoCanceller,
        cello_perception: CelloAcousticPerception,
        on_state_update: Callable[[PerceptionSnapshot, List[Dict[str, Any]], List[int]], None],
        on_speculate_hint: Callable[[PerceptionSnapshot, List[Dict[str, Any]]], None],
        on_turn_trigger: Callable[[], None],
    ) -> None:
        self.echo_ledger = echo_ledger
        self.turn_detector = turn_detector
        self.nlms = nlms
        self.cello_perception = cello_perception
        self._on_state_update = on_state_update
        self._on_speculate_hint = on_speculate_hint
        self._on_turn_trigger = on_turn_trigger

        self.midi_in_queue: queue.Queue[Dict[str, Any]] = queue.Queue(maxsize=512)
        self.audio_in_queue: queue.Queue[Tuple[np.ndarray, Optional[np.ndarray], float]] = queue.Queue(
            maxsize=32
        )

        self._lock = threading.Lock()
        self.mode: str = "cello"
        self.live_listening: bool = False
        self.human_phrase_buffer: List[Dict[str, Any]] = []
        self.human_active_pitches: Dict[int, float] = {}
        self.latest_snapshot = PerceptionSnapshot(mode="cello", timestamp=time.perf_counter())
        self.last_latency_ms: float = 0.0
        self._last_speculate_count: int = 0
        self._last_trigger_ts: float = 0.0
        self._cello_onset_buf: Deque[Dict[str, Any]] = deque(maxlen=16)

        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, name="W2_PerceptionNLMS", daemon=True
        )
        self._thread.start()

    def set_mode(self, mode: str) -> None:
        with self._lock:
            self.mode = mode
            self.latest_snapshot.mode = mode

    def set_live_listening(self, active: bool) -> None:
        with self._lock:
            self.live_listening = bool(active)

    def push_midi_event(self, event: Dict[str, Any]) -> None:
        try:
            self.midi_in_queue.put_nowait(event)
        except queue.Full:
            try:
                _ = self.midi_in_queue.get_nowait()
                self.midi_in_queue.put_nowait(event)
            except Exception:
                pass

    def push_audio_block(
        self,
        mic_ch: np.ndarray,
        ref_ch: Optional[np.ndarray] = None,
        ts: Optional[float] = None,
    ) -> None:
        stamp = ts if ts is not None else time.perf_counter()
        try:
            self.audio_in_queue.put_nowait((mic_ch, ref_ch, stamp))
        except queue.Full:
            try:
                _ = self.audio_in_queue.get_nowait()
                self.audio_in_queue.put_nowait((mic_ch, ref_ch, stamp))
            except Exception:
                pass

    def consume_human_phrase(self, limit: int = 8) -> List[Dict[str, Any]]:
        with self._lock:
            notes = list(self.human_phrase_buffer[-limit:])
            self.human_phrase_buffer.clear()
            self._last_speculate_count = 0
            return notes

    def _run_loop(self) -> None:
        while self._running:
            t0 = time.perf_counter()
            processed_any = False

            # Drain MIDI IN queue (non-blocking)
            for _ in range(32):
                try:
                    ev = self.midi_in_queue.get_nowait()
                except queue.Empty:
                    break
                processed_any = True
                self._handle_midi_event(ev)

            # Drain Audio IN queue (Mode B Cello + 2ch NLMS)
            for _ in range(4):
                try:
                    mic, ref, ts = self.audio_in_queue.get_nowait()
                except queue.Empty:
                    break
                processed_any = True
                self._handle_audio_block(mic, ref, ts)

            now = time.perf_counter()
            if processed_any:
                self.last_latency_ms = round((now - t0) * 1000.0, 3)

            self._evaluate_turn_and_speculate(now)
            time.sleep(0.006 if processed_any else 0.012)

    def _handle_midi_event(self, ev: Dict[str, Any]) -> None:
        ts = float(ev.get("ts") or time.perf_counter())
        etype = str(ev.get("type") or "note_on")
        if etype == "note_on" and int(ev.get("velocity", 0)) > 0:
            pitch = int(ev.get("pitch", ev.get("note", 60)))
            vel = int(ev.get("velocity", 72))
            bypass_echo = bool(ev.get("from_browser", False))
            if bypass_echo:
                accepted = {
                    "pitch": pitch,
                    "velocity": vel,
                    "onset_s": ts,
                    "duration_s": float(ev.get("duration_s", 0.35)),
                    "source": "browser_keyboard",
                }
                self.echo_ledger.human_accepted_count += 1
            else:
                accepted = self.echo_ledger.filter_incoming_note(pitch, vel, recv_ts=ts)
                if accepted is None:
                    return
                accepted["duration_s"] = float(ev.get("duration_s", 0.35))

            with self._lock:
                self.human_phrase_buffer.append(accepted)
                if len(self.human_phrase_buffer) > 32:
                    self.human_phrase_buffer = self.human_phrase_buffer[-32:]
                self.human_active_pitches[pitch] = ts + float(accepted.get("duration_s", 0.35))
                self.turn_detector.push_note(pitch, ts)
                self.latest_snapshot = PerceptionSnapshot(
                    mode=self.mode,
                    timestamp=ts,
                    f0_hz=round(440.0 * (2.0 ** ((pitch - 69) / 12.0)), 2),
                    midi_pitch=pitch,
                    note_name=midi_to_note_name(pitch),
                    articulation="legato" if self.turn_detector.pedal_cc64 >= 35 else "staccato",
                    echo_suppressed_count=self.echo_ledger.suppressed_count,
                    human_notes_count=self.echo_ledger.human_accepted_count,
                    pedal_cc64=self.turn_detector.pedal_cc64,
                    pedal_cc67=self.turn_detector.pedal_cc67,
                    recent_human_notes=list(self.human_phrase_buffer[-12:]),
                    pitch_history=[
                        round(440.0 * (2.0 ** ((int(n["pitch"]) - 69) / 12.0)), 2)
                        for n in self.human_phrase_buffer[-16:]
                    ],
                )
        elif etype == "note_off":
            pitch = int(ev.get("pitch", ev.get("note", 0)))
            with self._lock:
                self.human_active_pitches.pop(pitch, None)
        elif etype == "control_change":
            ctrl = int(ev.get("control", 0))
            val = int(ev.get("value", 0))
            with self._lock:
                self.turn_detector.push_cc(ctrl, val, ts)

    def _handle_audio_block(
        self, mic: np.ndarray, ref: Optional[np.ndarray], ts: float
    ) -> None:
        clean = self.nlms.process_block(mic, ref)
        snap = self.cello_perception.analyze_frame(clean, now=ts, nlms_erle_db=self.nlms.last_erle_db)
        with self._lock:
            if snap.midi_pitch > 0 and snap.rms_level > 0.009:
                note_entry = {
                    "pitch": int(snap.midi_pitch),
                    "velocity": min(105, max(42, int(round(52 + snap.rms_level * 260)))),
                    "onset_s": round(ts, 3),
                    "duration_s": 0.18 if snap.articulation == "staccato" else 0.36,
                    "voice": "cello_mic",
                }
                if not self.human_phrase_buffer or self.human_phrase_buffer[-1]["pitch"] != snap.midi_pitch:
                    self.human_phrase_buffer.append(note_entry)
                    self.turn_detector.push_note(int(snap.midi_pitch), ts)
            snap.mode = self.mode
            snap.recent_human_notes = list(self.human_phrase_buffer[-12:])
            snap.human_notes_count = len(self.human_phrase_buffer)
            snap.echo_suppressed_count = self.echo_ledger.suppressed_count
            self.latest_snapshot = snap

    def _evaluate_turn_and_speculate(self, now: float) -> None:
        trigger = False
        speculate_args: Optional[Tuple[PerceptionSnapshot, List[Dict[str, Any]]]] = None
        state_args: Optional[Tuple[PerceptionSnapshot, List[Dict[str, Any]], List[int]]] = None

        with self._lock:
            # Prune expired browser/human visual key holds
            expired = [p for p, exp_t in self.human_active_pitches.items() if exp_t <= now]
            for p in expired:
                self.human_active_pitches.pop(p, None)

            gauge, ready, reason = self.turn_detector.evaluate(now)
            self.latest_snapshot.breath_gauge = gauge
            self.latest_snapshot.turn_ready = ready
            self.latest_snapshot.turn_reason = reason

            n_buf = len(self.human_phrase_buffer)
            if n_buf > 0:
                state_args = (
                    self.latest_snapshot,
                    list(self.human_phrase_buffer[-12:]),
                    sorted(self.human_active_pitches.keys()),
                )

            if n_buf >= 2 and (n_buf != self._last_speculate_count or gauge >= 0.42):
                self._last_speculate_count = n_buf
                speculate_args = (self.latest_snapshot, list(self.human_phrase_buffer[-8:]))

            if self.live_listening and ready and n_buf >= 2 and (now - self._last_trigger_ts) >= 1.1:
                self._last_trigger_ts = now
                trigger = True

        if state_args is not None:
            self._on_state_update(*state_args)
        if speculate_args is not None:
            self._on_speculate_hint(*speculate_args)
        if trigger:
            self._on_turn_trigger()

    def close(self) -> None:
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=0.5)


class AsyncLLMIntentWorker:
    """
    Worker 3: Non-blocking ThreadPoolExecutor for Ollama policy/deliberation calls
    and asynchronous Obsidian vault writes so audio/MIDI/UI threads never stall on LLM I/O.
    """

    def __init__(self) -> None:
        self._llm_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="W3_AsyncLLM")
        self._obsidian_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="W3_ObsidianIO")

    def submit_llm(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future[Any]:
        return self._llm_pool.submit(fn, *args, **kwargs)

    def submit_obsidian(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future[Any]:
        return self._obsidian_pool.submit(fn, *args, **kwargs)

    def close(self) -> None:
        self._llm_pool.shutdown(wait=False, cancel_futures=True)
        self._obsidian_pool.shutdown(wait=False, cancel_futures=True)


class SpeculativePreGenWorker:
    """
    Worker 5: Speculative Pre-Generation Buffer.
    While the human performer is still phrasing/breathing, Worker 5 pre-computes
    a candidate draft from the trailing motif so final turn dispatch executes
    with only a fast delta voice-leading adjustment.
    """

    def __init__(
        self,
        architect: Any,
        composer: Any,
        intent_engine: Any,
        corpus_loader: Any,
        ttl_s: float = 6.0,
    ) -> None:
        self.architect = architect
        self.composer = composer
        self.intent_engine = intent_engine
        self.corpus_loader = corpus_loader
        self.ttl_s = float(ttl_s)

        self._queue: queue.Queue[Tuple[PerceptionSnapshot, List[Dict[str, Any]], Dict[str, Any]]] = queue.Queue(
            maxsize=3
        )
        self._cache_lock = threading.Lock()
        self._cached_draft: Optional[SpeculativeDraft] = None
        self.hits: int = 0
        self.misses: int = 0
        self.last_saved_ms: float = 0.0

        self._running = True
        self._thread = threading.Thread(
            target=self._worker_loop, name="W5_SpeculativePreGen", daemon=True
        )
        self._thread.start()

    @staticmethod
    def _make_key(mode: str, human_notes: List[Dict[str, Any]], hints: Dict[str, Any]) -> str:
        pitches = [int(n.get("pitch", n.get("note", 0))) for n in human_notes[-4:]]
        raw_dir = str(hints.get("raw_text") or "")
        digest = hashlib.blake2b(raw_dir.encode("utf-8"), digest_size=4).hexdigest()
        return f"{mode}:{pitches}:{digest}"

    def schedule_pregen(
        self,
        perception: PerceptionSnapshot,
        human_notes: List[Dict[str, Any]],
        directive_hints: Dict[str, Any],
    ) -> None:
        if len(human_notes) < 2:
            return
        try:
            self._queue.put_nowait((perception, list(human_notes[-8:]), dict(directive_hints)))
        except queue.Full:
            try:
                _ = self._queue.get_nowait()
                self._queue.put_nowait((perception, list(human_notes[-8:]), dict(directive_hints)))
            except Exception:
                pass

    def has_ready_draft(self) -> bool:
        now = time.perf_counter()
        with self._cache_lock:
            return (
                self._cached_draft is not None
                and (now - self._cached_draft.created_ts) <= self.ttl_s
            )

    def try_consume(
        self,
        mode: str,
        human_notes: List[Dict[str, Any]],
        directive_hints: Dict[str, Any],
    ) -> Optional[SpeculativeDraft]:
        now = time.perf_counter()
        pitches = [int(n.get("pitch", n.get("note", 0))) for n in human_notes if int(n.get("pitch", 0)) > 0]
        last_p = pitches[-1] if pitches else 60
        key = self._make_key(mode, human_notes, directive_hints)

        with self._cache_lock:
            draft = self._cached_draft
            if draft is None or (now - draft.created_ts) > self.ttl_s:
                self.misses += 1
                return None
            if draft.mode != mode:
                self.misses += 1
                return None
            # Accept exact match OR close trailing-pitch match (<= 2 semitones delta adjustment)
            if draft.key == key or abs(draft.last_human_pitch - last_p) <= 2:
                self._cached_draft = None
                self.hits += 1
                self.last_saved_ms = draft.compose_ms
                return draft
            self.misses += 1
            return None

    def _worker_loop(self) -> None:
        while self._running:
            try:
                snap, human_notes, hints = self._queue.get(timeout=0.12)
            except queue.Empty:
                continue

            # Drain to newest request so we never pre-generate stale frames
            while not self._queue.empty():
                try:
                    snap, human_notes, hints = self._queue.get_nowait()
                except queue.Empty:
                    break

            try:
                t0 = time.perf_counter()
                macro_plan = self.architect.preview_turn_architecture(
                    human_notes=human_notes,
                    directive_hints=hints,
                    mode=snap.mode,
                )
                policy_intent = self.intent_engine.decide_policy(
                    snap,
                    human_notes,
                    hints,
                    corpus_refs=self.corpus_loader.retrieve_voicing_references(0.5, 0.5, top_k=3),
                    use_llm_design=False,
                    macro_plan=macro_plan,
                )
                turn_res = self.composer.compose_turn(
                    human_notes,
                    policy_intent,
                    human_mode=snap.mode,
                    macro_plan=macro_plan,
                    parallel=True,
                )
                from src.stage.layer2_intent_llm import MusicalLogicValidator

                rejected = [c for c in turn_res.candidates if not getattr(c, "adopted", False)]
                memo = MusicalLogicValidator.build_factual_memo(
                    perception=snap,
                    human_notes=human_notes,
                    pedal_strategy=policy_intent.pedal_strategy,
                    adopted_candidate=turn_res.adopted_candidate,
                    rejected_candidates=rejected,
                )
                final_intent = policy_intent.model_copy(update={"monologue_ja": memo})

                compose_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                pitches = [int(n.get("pitch", n.get("note", 60))) for n in human_notes]
                draft = SpeculativeDraft(
                    key=self._make_key(snap.mode, human_notes, hints),
                    mode=snap.mode,
                    created_ts=time.perf_counter(),
                    last_human_pitch=pitches[-1] if pitches else 60,
                    macro_plan=macro_plan,
                    policy_intent=policy_intent,
                    turn_result=turn_res,
                    final_intent=final_intent,
                    compose_ms=compose_ms,
                )
                with self._cache_lock:
                    self._cached_draft = draft
            except Exception:
                continue

    def clear(self) -> None:
        with self._cache_lock:
            self._cached_draft = None

    def close(self) -> None:
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=0.5)


class AudioRenderWorker:
    """
    Worker 6a: Asynchronous Concert-Hall Audio Synthesis Worker.
    Offloads 44.1kHz stereo FFT reverb synthesis off the real-time orchestration thread.
    """

    def __init__(
        self,
        audio_synth: Any,
        on_rendered: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.audio_synth = audio_synth
        self._on_rendered = on_rendered
        self._queue: queue.Queue[Dict[str, Any]] = queue.Queue(maxsize=3)
        self.last_render_ms: float = 0.0
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, name="W6_HallAudioSynth", daemon=True
        )
        self._thread.start()

    def submit(self, job: Dict[str, Any]) -> None:
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            try:
                _ = self._queue.get_nowait()
                self._queue.put_nowait(job)
            except Exception:
                pass

    def queue_depth(self) -> int:
        return self._queue.qsize()

    def _run_loop(self) -> None:
        while self._running:
            try:
                job = self._queue.get(timeout=0.12)
            except queue.Empty:
                continue
            # Always render the newest job if multiple queued
            while not self._queue.empty():
                try:
                    job = self._queue.get_nowait()
                except queue.Empty:
                    break
            t0 = time.perf_counter()
            try:
                stats = self.audio_synth.play_turn_audio(
                    human_notes=job["human_notes"],
                    human_mode=job["human_mode"],
                    human_articulation=job["human_articulation"],
                    rejected_candidates=job["rejected_candidates"],
                    adopted_events=job["adopted_events"],
                    play_device=job["play_device"],
                )
                self.last_render_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                if self._on_rendered is not None:
                    self._on_rendered(stats)
            except Exception:
                continue

    def close(self) -> None:
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=0.5)
