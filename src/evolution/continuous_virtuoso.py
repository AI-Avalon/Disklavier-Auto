"""
Continuous Non-Stop Musical Stream Engine & Look-Ahead Double Ring Buffer.

Separates:
1. Real-Time Playback Thread: Dispatches buffered look-ahead bars into DisklavierSafetyGuard
   with exact monotonic `target_sound_time` timestamps so musical flow NEVER stops (0 ms gap).
2. Background Composer & Critic Thread: Monitors `lookahead_horizon_s` and keeps 2-4 future
   bars pre-generated via ParallelCandidateGenerator (4 multiverse branches -> 5-axis Critic
   -> highest-scoring branch adopted), stitching bar seams with +5..+15ms legato overlap and
   -30..-50ms CC#64 anticipatory pedal transitions.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Dict, List, Optional

from src.audio.c519ml_monitor import midi_to_name
from src.evolution.actor_critic import (
    ARC_STAGES,
    MultiObjectiveCritic,
    ParallelCandidateGenerator,
)
from src.evolution.seed_ingestor import SeedMemoryPool
from src.safety.disklavier_safety_guard import DisklavierSafetyGuard
from src.telemetry.telemetry_store import TelemetryStore


class ContinuousVirtuosoEngine:
    """
    Autonomous Self-Evolving Virtuoso Engine with Non-Stop Ring Buffer,
    Storytelling Arc, Live Motif Absorption, and Hyper-Speed Offline Training.
    """

    LOOKAHEAD_MIN_S = 5.5
    LOOKAHEAD_TARGET_S = 8.5
    BAR_DURATION_S = 2.2
    BARS_PER_CANDIDATE = 2

    def __init__(
        self,
        safety_guard: DisklavierSafetyGuard,
        seed_pool: SeedMemoryPool,
        telemetry_store: Optional[TelemetryStore] = None,
        obsidian_brain: Optional[Any] = None,
    ) -> None:
        self.safety_guard = safety_guard
        self.seed_pool = seed_pool
        self.telemetry_store = telemetry_store
        self.obsidian_brain = obsidian_brain

        self.critic = MultiObjectiveCritic()
        self.generator = ParallelCandidateGenerator(critic=self.critic)
        self._lock = threading.RLock()

        # Double Ring Buffer (A/B look-ahead slots)
        self._ring_buffer: deque = deque(maxlen=8)
        self._active_slot: str = "Buffer A"
        self._stream_cursor_t: float = 0.0

        # Stream & Storytelling Arc state
        self.streaming_active: bool = False
        self.bar_index: int = 0
        self.arc_idx: int = 0
        self.motif_list = self.seed_pool.get_all_motifs()
        self.active_motif: Dict[str, Any] = (
            self.motif_list[0] if self.motif_list else self.seed_pool.pick_next_motif()
        )
        self._pending_injected_motif: Optional[Dict[str, Any]] = None

        self.prev_bar_state: Dict[str, Any] = {
            "bar_index": 0,
            "last_melody_note": int(self.active_motif.get("melody_pitches", [65])[0]),
            "last_bass_note": max(36, int(self.active_motif.get("tonic_midi", 61)) - 12),
        }
        self.history_chords: List[str] = []

        # Active Human Directives from Obsidian 04_Human_Directives.md
        self.directives: Dict[str, Any] = {
            "raw_text": "",
            "prefer_minor": False,
            "prefer_chromatic_bass": False,
            "prefer_articulation_contrast": False,
            "bass_chromaticism_target": 0.62,
            "staccato_contrast_target": 0.28,
            "half_pedal_target": 62,
            "counterpoint_density_target": 0.78,
        }

        # Live Telemetry & Evolution History
        self.latest_branches: List[Dict[str, Any]] = []
        self.latest_axes: Dict[str, float] = {
            "voice_leading": 18.0,
            "motivic_continuity": 15.0,
            "harmonic_originality": 14.0,
            "expressive_articulation": 10.0,
            "physical_playability": 18.0,
        }
        self.latest_score: float = 75.0
        self.best_score_ever: float = 75.0
        self.mean_score: float = 75.0
        self.critic_logs: deque = deque(maxlen=24)
        self.evolution_history: List[Dict[str, Any]] = []
        self.hyper_training_status: Dict[str, Any] = {
            "running": False,
            "progress_pct": 0,
            "speed_multiplier": 25,
            "last_run_summary": "Ready",
        }

        self._running = True
        self._composer_thread = threading.Thread(
            target=self._composer_loop,
            name="VirtuosoBackgroundComposer",
            daemon=True,
        )
        self._playback_thread = threading.Thread(
            target=self._playback_loop,
            name="VirtuosoRealtimePlayback",
            daemon=True,
        )
        self._composer_thread.start()
        self._playback_thread.start()

    def attach_obsidian_brain(self, brain: Any) -> None:
        with self._lock:
            self.obsidian_brain = brain

    def update_directives(self, parsed_directives: Dict[str, Any]) -> None:
        with self._lock:
            self.directives.update(parsed_directives)
        if self.telemetry_store:
            self.telemetry_store.log_event(
                f"📝 [OBSIDIAN DIRECTIVES] Updated AI Fitness Weights: {parsed_directives.get('summary', 'Custom')}"
            )

    def start_stream(self) -> Dict[str, Any]:
        with self._lock:
            if not self.streaming_active:
                self._stream_cursor_t = time.perf_counter() + 0.35
                self.streaming_active = True
                self._prepopulate_buffer_locked()
        if self.telemetry_store:
            self.telemetry_store.log_event(
                "♾️ [NON-STOP STREAM] Continuous Autonomous Virtuoso stream started."
            )
        return self.get_telemetry_snapshot()

    def stop_stream(self) -> Dict[str, Any]:
        with self._lock:
            self.streaming_active = False
            self._ring_buffer.clear()
        self.safety_guard.emergency_panic()
        if self.telemetry_store:
            self.telemetry_store.log_event("⏸️ [NON-STOP STREAM] Stream paused.")
        return self.get_telemetry_snapshot()

    def absorb_live_motif(
        self, notes: List[int], source: str = "gui_or_cello"
    ) -> Dict[str, Any]:
        """
        Seamlessly absorbs a newly played motif (from Cello, MIDI IN, or GUI) into the
        next look-ahead bar using pivot modulation without interrupting continuous playback!
        """
        clean_notes = [int(n) for n in notes if 28 <= int(n) <= 96]
        if not clean_notes:
            clean_notes = [62, 65, 69, 68, 65, 62]
        tonic = clean_notes[0]
        while tonic > 65:
            tonic -= 12
        while tonic < 48:
            tonic += 12
        new_motif = {
            "motif_id": f"motif_live_{int(time.time())}",
            "title": f"Live Absorbed Motif ({source})",
            "composer": f"Human × Virtuoso ({source})",
            "key": f"{midi_to_name(tonic)} Pivot",
            "tonic_midi": tonic,
            "scale_intervals": [0, 2, 3, 5, 7, 8, 10] if self.directives.get("prefer_minor") else [0, 2, 4, 5, 7, 9, 11],
            "melody_pitches": clean_notes[:8],
            "interval_vector": [p - clean_notes[0] for p in clean_notes[:8]],
            "rhythm_ratios": [1.0] * max(1, len(clean_notes[:8]) - 1),
            "contrary_motion_ratio": 0.75,
            "half_pedal_profile": {"mean_cc64": 64, "min_cc64": 20, "max_cc64": 70},
            "tags": ["live_absorbed", source],
            "source_file": "live_stream",
        }
        with self._lock:
            self._pending_injected_motif = new_motif
            self.active_motif = new_motif
            self.critic_logs.append(
                f"🎻 [LIVE MOTIF ABSORBED] {source}: {[midi_to_name(n) for n in clean_notes[:6]]} → 次小節でピボット転調＆対位法模倣へ統合"
            )
        if self.telemetry_store:
            self.telemetry_store.log_event(
                f"🎻 [SEAMLESS ABSORPTION] Queued motif {[midi_to_name(n) for n in clean_notes[:6]]} without stopping stream"
            )
        return {"ok": True, "motif": new_motif}

    def _generate_next_chunk_locked(self, record_to_obsidian: bool = True) -> Dict[str, Any]:
        if self._pending_injected_motif is not None:
            self.active_motif = self._pending_injected_motif
            self._pending_injected_motif = None
        elif self.bar_index > 0 and self.bar_index % 10 == 0 and self.motif_list:
            # Smoothly transition to a fresh seed motif every 10 bars during Metamorphosis
            self.active_motif = self.seed_pool.pick_next_motif(
                self.active_motif.get("motif_id")
            )

        stage = ARC_STAGES[self.arc_idx % len(ARC_STAGES)]
        if self.bar_index > 0 and self.bar_index % 2 == 0:
            self.arc_idx = (self.arc_idx + 1) % len(ARC_STAGES)
            stage = ARC_STAGES[self.arc_idx]

        res = self.generator.generate_and_select(
            motif=self.active_motif,
            prev_bar_state=self.prev_bar_state,
            story_arc_stage=stage,
            history_chords=self.history_chords,
            bar_dur_s=self.BAR_DURATION_S,
            num_bars=self.BARS_PER_CANDIDATE,
            directives=self.directives,
        )
        winner = res["winner"]
        gen_num = res["generation"]

        self.bar_index += self.BARS_PER_CANDIDATE
        self.prev_bar_state = {
            "bar_index": self.bar_index,
            "last_melody_note": winner["last_melody_note"],
            "last_bass_note": winner["last_bass_note"],
        }
        self.history_chords.append(winner["chord_label"])
        if len(self.history_chords) > 32:
            self.history_chords = self.history_chords[-32:]

        self.latest_branches = res["branches"]
        self.latest_axes = winner["evaluation"]["axes"]
        self.latest_score = res["best_score"]
        self.mean_score = res["mean_candidate_score"]
        self.best_score_ever = max(self.best_score_ever, res["best_score"])

        self.critic_logs.append(
            f"[Gen {gen_num} | {stage[0]}] {winner['evaluation']['commentary_ja']}"
        )
        self.evolution_history.append(
            {
                "generation": gen_num,
                "best_score": res["best_score"],
                "mean_score": res["mean_candidate_score"],
                "axes": dict(self.latest_axes),
                "arc_stage": stage[0],
                "chord_label": winner["chord_label"],
                "transform": winner["transform_type"],
            }
        )
        if len(self.evolution_history) > 120:
            self.evolution_history = self.evolution_history[-120:]

        if record_to_obsidian and self.obsidian_brain is not None:
            try:
                self.obsidian_brain.record_generation_result(
                    generation_result=res,
                    motif=self.active_motif,
                    arc_stage=stage,
                    evolution_history=self.evolution_history,
                )
            except Exception:
                pass

        return {
            "generation": gen_num,
            "bar_start_idx": self.bar_index - self.BARS_PER_CANDIDATE,
            "duration_s": winner["duration_s"],
            "arc_stage": stage[0],
            "arc_label": stage[1],
            "tension": stage[2],
            "chord_label": winner["chord_label"],
            "score": res["best_score"],
            "events": winner["events"],
        }

    def _prepopulate_buffer_locked(self) -> None:
        while len(self._ring_buffer) < 2:
            chunk = self._generate_next_chunk_locked(record_to_obsidian=True)
            self._ring_buffer.append(chunk)

    def _composer_loop(self) -> None:
        while self._running:
            try:
                with self._lock:
                    if self.streaming_active:
                        now = time.perf_counter()
                        horizon = max(0.0, self._stream_cursor_t - now)
                        if horizon < self.LOOKAHEAD_MIN_S and len(self._ring_buffer) < 4:
                            chunk = self._generate_next_chunk_locked(record_to_obsidian=True)
                            self._ring_buffer.append(chunk)
                            self._active_slot = (
                                "Buffer B" if self._active_slot == "Buffer A" else "Buffer A"
                            )
            except Exception:
                pass
            time.sleep(0.12)

    def _playback_loop(self) -> None:
        while self._running:
            try:
                chunk_to_schedule: Optional[Dict[str, Any]] = None
                start_t = 0.0
                with self._lock:
                    if self.streaming_active:
                        now = time.perf_counter()
                        if self._stream_cursor_t < now + 0.18:
                            self._stream_cursor_t = now + 0.22
                        # Schedule next chunk whenever buffer has ready bars and cursor is within target horizon
                        if (self._stream_cursor_t - now) < self.LOOKAHEAD_TARGET_S and self._ring_buffer:
                            chunk_to_schedule = self._ring_buffer.popleft()
                            start_t = self._stream_cursor_t
                            self._stream_cursor_t += float(chunk_to_schedule["duration_s"])

                if chunk_to_schedule is not None:
                    self._schedule_chunk_to_guard(chunk_to_schedule, start_t)
            except Exception:
                pass
            time.sleep(0.04)

    def _schedule_chunk_to_guard(self, chunk: Dict[str, Any], start_t: float) -> None:
        legato_overlap_s = max(
            0.005,
            min(0.015, self.generator.elite_genome.legato_overlap_ms / 1000.0),
        )
        pedal_preroll_ms = max(
            30.0,
            min(50.0, self.generator.elite_genome.pedal_preroll_ms),
        )
        for ev in chunk["events"]:
            t_off = float(ev.get("time_offset_s", 0.0))
            note = int(ev.get("note", 0))
            if note == 0 and ev.get("pedal_cc64") is not None:
                self.safety_guard.schedule_chord_or_notes(
                    notes=[],
                    target_sound_time=start_t + t_off,
                    use_vdl_compensation=False,
                    pedal_cc64=int(ev["pedal_cc64"]),
                    pedal_preroll_ms=pedal_preroll_ms,
                    source_tag="virtuoso_pedal",
                )
            elif note > 0:
                self.safety_guard.schedule_chord_or_notes(
                    notes=[(note, int(ev["velocity"]), float(ev["duration_s"]))],
                    target_sound_time=start_t + t_off,
                    use_vdl_compensation=True,
                    sympathetic_halo=bool(ev.get("sympathetic_halo", False)),
                    legato_overlap_s=legato_overlap_s,
                    source_tag=str(ev.get("source_tag", "virtuoso")),
                )

    def run_hyper_speed_training(
        self, num_generations: int = 15, speed_multiplier: float = 25.0
    ) -> Dict[str, Any]:
        """
        Hyper-Speed Offline Evolution Mode: runs `num_generations` cycles at 10x-50x speed
        on the Virtual Disklavier Physics Simulator, evolving elite weights and storing
        Hall of Fame performances & discoveries into Obsidian Vault.
        """
        num_generations = max(1, min(60, int(num_generations)))
        start_time = time.perf_counter()
        self.hyper_training_status = {
            "running": True,
            "progress_pct": 0,
            "speed_multiplier": speed_multiplier,
            "last_run_summary": f"Running {num_generations} generations @ {speed_multiplier}x...",
        }

        gen_scores: List[Dict[str, Any]] = []
        for idx in range(num_generations):
            with self._lock:
                chunk = self._generate_next_chunk_locked(record_to_obsidian=True)
                gen_scores.append(
                    {
                        "generation": chunk["generation"],
                        "score": chunk["score"],
                        "mean_score": self.mean_score,
                    }
                )
                self.hyper_training_status["progress_pct"] = int(
                    ((idx + 1) / num_generations) * 100
                )
            # Tiny yield scaled inversely by speed_multiplier so UI 60fps sees live climb
            time.sleep(max(0.005, 0.25 / max(10.0, speed_multiplier)))

        elapsed = round(time.perf_counter() - start_time, 2)
        first_s = gen_scores[0]["score"] if gen_scores else 0.0
        last_s = gen_scores[-1]["score"] if gen_scores else 0.0
        summary = (
            f"Completed {num_generations} gens in {elapsed}s ({speed_multiplier}x): "
            f"{first_s:.1f} -> {last_s:.1f} pts (Peak {self.best_score_ever:.1f} pts)"
        )
        self.hyper_training_status = {
            "running": False,
            "progress_pct": 100,
            "speed_multiplier": speed_multiplier,
            "last_run_summary": summary,
        }
        if self.telemetry_store:
            self.telemetry_store.log_event(f"⚡ [HYPER-SPEED EVOLUTION] {summary}")

        return {
            "ok": True,
            "generations_run": num_generations,
            "elapsed_s": elapsed,
            "first_score": first_s,
            "latest_score": last_s,
            "best_score_ever": self.best_score_ever,
            "history": gen_scores,
            "summary": summary,
        }

    def get_telemetry_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            now = time.perf_counter()
            horizon_s = (
                round(max(0.0, self._stream_cursor_t - now), 2)
                if self.streaming_active
                else 0.0
            )
            stage = ARC_STAGES[self.arc_idx % len(ARC_STAGES)]
            return {
                "streaming_active": self.streaming_active,
                "current_generation": self.generator.elite_genome.generation,
                "bar_index": self.bar_index,
                "lookahead_horizon_s": horizon_s,
                "buffer_slots_queued": len(self._ring_buffer),
                "active_buffer_slot": self._active_slot,
                "story_arc_stage": stage[0],
                "story_arc_label": stage[1],
                "story_tension": stage[2],
                "active_motif": {
                    "motif_id": self.active_motif.get("motif_id"),
                    "title": self.active_motif.get("title"),
                    "composer": self.active_motif.get("composer"),
                    "key": self.active_motif.get("key"),
                    "melody_pitches": self.active_motif.get("melody_pitches", []),
                },
                "latest_score": self.latest_score,
                "mean_score": self.mean_score,
                "best_score_ever": self.best_score_ever,
                "latest_axes": dict(self.latest_axes),
                "branches": list(self.latest_branches),
                "critic_logs": list(self.critic_logs)[-14:],
                "evolution_history": list(self.evolution_history)[-50:],
                "elite_genome": self.generator.elite_genome.to_dict(),
                "directives": dict(self.directives),
                "hyper_training": dict(self.hyper_training_status),
            }

    def close(self) -> None:
        self._running = False
        self.streaming_active = False
