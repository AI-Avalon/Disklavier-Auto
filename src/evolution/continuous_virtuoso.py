"""
Continuous Self-Evolving Virtuoso Engine v3.0:
  - Double Ring Buffer for Zero-Gap Non-Stop Streaming
  - Browser-Style Multi-Song Workspace Tabs + Time-Machine Undo/Redo/Scrubber
  - 30-Second Rolling Rescue Capsule ("⏪ 今の30秒前を救出！")
  - Diverse Reset & Spark Controls (Full Reset, Mood Shift Keep Brain, Inspiration Spark, Human Like)
  - 8..16 Parallel Candidates + Human Reference Teacher + <0.1s LocalPreflightChecker
"""

from __future__ import annotations

import copy
import random
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

from src.audio.c519ml_monitor import midi_to_name
from src.evolution.actor_critic import (
    ARC_STAGES,
    MultiObjectiveCritic,
    MusicalGenome,
    ParallelCandidateGenerator,
)
from src.evolution.human_reference_teacher import HumanReferenceTeacher
from src.evolution.seed_ingestor import SeedMemoryPool
from src.evolution.song_workspace import MultiSongWorkspaceManager, SongTabState
from src.safety.disklavier_safety_guard import DisklavierSafetyGuard
from src.safety.preflight_checker import LocalPreflightChecker
from src.telemetry.telemetry_store import TelemetryStore


CHARACTER_PRESETS: Dict[str, Dict[str, Any]] = {
    "night_nocturne": {
        "title": "🌙 静かな夜の透明なノクターン",
        "expression_x": -0.82,
        "expression_y": 0.36,
        "tonic_midi": 61,
        "prefer_minor": False,
        "prefer_chromatic_bass": False,
        "half_pedal_target": 68,
    },
    "tchaikovsky_passion": {
        "title": "🔥 ロマン派の情熱カンタービレ",
        "expression_x": -0.48,
        "expression_y": 0.82,
        "tonic_midi": 62,
        "prefer_minor": True,
        "prefer_chromatic_bass": True,
        "half_pedal_target": 66,
    },
    "vivace_scherzando": {
        "title": "💃 活発に跳ねるスケルツォ",
        "expression_x": 0.78,
        "expression_y": 0.80,
        "tonic_midi": 67,
        "prefer_minor": False,
        "prefer_chromatic_bass": False,
        "half_pedal_target": 42,
    },
    "debussy_watercolor": {
        "title": "🌊 水の光と透明な倍音",
        "expression_x": -0.68,
        "expression_y": 0.45,
        "tonic_midi": 56,
        "prefer_minor": False,
        "prefer_chromatic_bass": False,
        "half_pedal_target": 72,
    },
    "bach_dialogue": {
        "title": "🏛️ 緻密なかけあい・対位法",
        "expression_x": 0.32,
        "expression_y": 0.58,
        "tonic_midi": 55,
        "prefer_minor": True,
        "prefer_chromatic_bass": True,
        "half_pedal_target": 40,
    },
}


class ContinuousVirtuosoEngine:
    """
    Autonomous Self-Evolving Virtuoso Engine with Non-Stop Ring Buffer,
    Multi-Song Workspace Tabs, Time-Machine Undo/Redo/Scrubber, and Local Preflight Guard.
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

        self.teacher = HumanReferenceTeacher(auto_build=True)
        self.critic = MultiObjectiveCritic()
        self.generator = ParallelCandidateGenerator(
            critic=self.critic,
            candidate_count=8,
            use_process_pool=True,
        )
        self.workspace = MultiSongWorkspaceManager()
        self._lock = threading.RLock()

        # Double Ring Buffer & 60s Rolling Rescue History
        self._ring_buffer: deque = deque(maxlen=8)
        self._active_slot: str = "Buffer A"
        self._stream_cursor_t: float = 0.0
        self._played_chunks_ring: deque = deque(maxlen=40)  # Stores last ~90s of played chunks

        # Stream & Storytelling state
        self.streaming_active: bool = False
        self.bar_index: int = 0
        self.arc_idx: int = 0
        self.motif_list = self.seed_pool.get_all_motifs()
        self.active_motif: Dict[str, Any] = (
            self.motif_list[0] if self.motif_list else self.seed_pool.pick_next_motif()
        )
        self._pending_injected_motif: Optional[Dict[str, Any]] = None
        self._pending_spark: bool = False
        self._rescued_count: int = 0
        self._rng = random.Random(2026)

        self.prev_bar_state: Dict[str, Any] = {
            "bar_index": 0,
            "last_melody_note": int(self.active_motif.get("melody_pitches", [65])[0]),
            "last_bass_note": max(36, int(self.active_motif.get("tonic_midi", 61)) - 12),
        }
        self.history_chords: List[str] = []

        self.directives: Dict[str, Any] = {
            "raw_text": "",
            "prefer_minor": False,
            "prefer_chromatic_bass": False,
            "prefer_articulation_contrast": False,
            "bass_chromaticism_target": 0.64,
            "staccato_contrast_target": 0.24,
            "half_pedal_target": 62,
            "counterpoint_density_target": 0.78,
        }

        # Live Telemetry & Evolution History
        self.latest_branches: List[Dict[str, Any]] = []
        self.latest_axes: Dict[str, float] = {
            "voice_leading": 15.0,
            "motivic_continuity": 13.0,
            "harmonic_originality": 13.0,
            "expressive_articulation": 10.0,
            "physical_playability": 19.0,
        }
        self.latest_human_scores: Dict[str, float] = {
            "legato_smoothness_100": 72.0,
            "rhythmic_vivacity_100": 70.0,
            "melodic_story_100": 71.0,
            "hardware_safety_100": 100.0,
            "human_likeness_100": 74.0,
        }
        self.latest_inspiration: Dict[str, Any] = HumanReferenceTeacher.compute_emergent_inspiration(
            self.generator.elite_genome.to_dict(),
            expression_x=self.generator.elite_genome.expression_x,
            expression_y=self.generator.elite_genome.expression_y,
        )
        self.live_story_subtitle_ja: str = "しっとりと歌うような序奏から、新しい物語が始まります…"
        self.pianist_thought_bubble_ja: str = "💭 AIピアニストの狙い: 最初のメロディを歌わせながら、和声の色彩を探しています。"
        self.latest_events: List[Dict[str, Any]] = []
        self.latest_score: float = 72.0
        self.best_score_ever: float = 72.0
        self.mean_score: float = 70.0
        self.critic_logs: deque = deque(maxlen=24)
        self.evolution_history: List[Dict[str, Any]] = []
        self.last_preflight_report: Dict[str, Any] = {"passed": True, "elapsed_ms": 0.0, "violations": []}
        self.hyper_training_status: Dict[str, Any] = {
            "running": False,
            "progress_pct": 0,
            "speed_multiplier": 30,
            "last_run_summary": "待機中（50世代高速トレーニング可能）",
        }

        self._sync_engine_from_active_tab()

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
                f"📝 [進化メモ反映] {parsed_directives.get('summary', 'カスタム設定')}"
            )

    def _save_active_tab_from_engine(self) -> None:
        tab = self.workspace.get_active_tab()
        tab.bar_index = self.bar_index
        tab.arc_idx = self.arc_idx
        tab.current_generation = self.generator.elite_genome.generation
        tab.expression_x = self.generator.elite_genome.expression_x
        tab.expression_y = self.generator.elite_genome.expression_y
        tab.elite_genome = self.generator.elite_genome.to_dict()
        tab.population_genomes = [g.to_dict() for g in self.generator.population]
        tab.prev_bar_state = copy.deepcopy(self.prev_bar_state)
        tab.history_chords = list(self.history_chords)
        tab.evolution_history = list(self.evolution_history)
        tab.latest_score = self.latest_score
        tab.mean_score = self.mean_score
        tab.best_score_ever = self.best_score_ever
        tab.latest_axes = dict(self.latest_axes)
        tab.latest_human_scores = dict(self.latest_human_scores)
        tab.latest_inspiration = copy.deepcopy(self.latest_inspiration)
        tab.live_story_subtitle_ja = self.live_story_subtitle_ja
        tab.pianist_thought_bubble_ja = self.pianist_thought_bubble_ja
        tab.latest_events = copy.deepcopy(self.latest_events)

    def _sync_engine_from_active_tab(self, tab: Optional[SongTabState] = None) -> None:
        t = tab or self.workspace.get_active_tab()
        self.bar_index = t.bar_index
        self.arc_idx = t.arc_idx
        self.generator.elite_genome = MusicalGenome.from_dict(t.elite_genome)
        self.generator.elite_genome.generation = t.current_generation
        self.generator.elite_genome.expression_x = t.expression_x
        self.generator.elite_genome.expression_y = t.expression_y
        if t.population_genomes:
            self.generator.population = [MusicalGenome.from_dict(gd) for gd in t.population_genomes]
        else:
            self.generator.population = [
                self.generator.elite_genome,
                self.generator.elite_genome.mutate(generation=t.current_generation, rng=self._rng),
            ]
        self.prev_bar_state = copy.deepcopy(t.prev_bar_state)
        self.history_chords = list(t.history_chords)
        self.evolution_history = list(t.evolution_history)
        self.latest_score = t.latest_score
        self.mean_score = t.mean_score
        self.best_score_ever = t.best_score_ever
        self.latest_axes = dict(t.latest_axes)
        self.latest_human_scores = dict(t.latest_human_scores)
        self.latest_inspiration = (
            copy.deepcopy(t.latest_inspiration)
            if t.latest_inspiration
            else HumanReferenceTeacher.compute_emergent_inspiration(
                self.generator.elite_genome.to_dict(),
                expression_x=t.expression_x,
                expression_y=t.expression_y,
            )
        )
        self.live_story_subtitle_ja = t.live_story_subtitle_ja
        self.pianist_thought_bubble_ja = t.pianist_thought_bubble_ja
        self.latest_events = copy.deepcopy(t.latest_events)
        # Keep tonic aligned in active motif
        self.active_motif = dict(self.active_motif)
        self.active_motif["tonic_midi"] = t.tonic_midi

    # -------------------------------------------------------------------------
    # Multi-Song Tabs & Time-Machine Controls
    # -------------------------------------------------------------------------
    def switch_song_tab(self, tab_id: str) -> Dict[str, Any]:
        with self._lock:
            self._save_active_tab_from_engine()
            target = self.workspace.switch_tab(tab_id)
            if target is None:
                return {"ok": False, "error": "指定された曲タブが見つかりません"}
            self._ring_buffer.clear()
            self._sync_engine_from_active_tab(target)
            self._stream_cursor_t = time.perf_counter() + 0.25
            if self.streaming_active:
                self._prepopulate_buffer_locked()
            self.critic_logs.append(f"🗂️ 曲タブ切替: {target.title} (第{target.current_generation}世代)")
        return {"ok": True, "tab": target.title, "virtuoso": self.get_telemetry_snapshot()}

    def create_song_tab(self, title: Optional[str] = None, character_preset: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            self._save_active_tab_from_engine()
            preset = CHARACTER_PRESETS.get(character_preset or "", {})
            ex = float(preset.get("expression_x", self._rng.uniform(-0.7, 0.7)))
            ey = float(preset.get("expression_y", self._rng.uniform(0.45, 0.85)))
            tonic = int(preset.get("tonic_midi", self._rng.choice([55, 57, 60, 61, 62, 65, 67])))
            new_tab = self.workspace.create_new_tab(
                title=title or preset.get("title"),
                character_tag=character_preset or "original_free",
                tonic_midi=tonic,
                expression_x=ex,
                expression_y=ey,
            )
            self._ring_buffer.clear()
            self._sync_engine_from_active_tab(new_tab)
            self._stream_cursor_t = time.perf_counter() + 0.25
            if self.streaming_active:
                self._prepopulate_buffer_locked()
            self.critic_logs.append(f"➕ 新しい曲タブ作成: {new_tab.title}")
        return {"ok": True, "tab_id": new_tab.tab_id, "virtuoso": self.get_telemetry_snapshot()}

    def close_song_tab(self, tab_id: str) -> Dict[str, Any]:
        with self._lock:
            self._save_active_tab_from_engine()
            res = self.workspace.close_tab(tab_id)
            if res.get("ok"):
                self._ring_buffer.clear()
                self._sync_engine_from_active_tab()
            return {**res, "virtuoso": self.get_telemetry_snapshot()}

    def restore_closed_song_tab(self) -> Dict[str, Any]:
        with self._lock:
            self._save_active_tab_from_engine()
            restored = self.workspace.restore_last_closed_tab()
            if not restored:
                return {"ok": False, "message": "復元できる閉じた曲はありません"}
            self._ring_buffer.clear()
            self._sync_engine_from_active_tab(restored)
            self.critic_logs.append(f"♻️ 閉じた曲を復元: {restored.title}")
            return {"ok": True, "restored_title": restored.title, "virtuoso": self.get_telemetry_snapshot()}

    def timemachine_undo(self) -> Dict[str, Any]:
        with self._lock:
            self._save_active_tab_from_engine()
            snap = self.workspace.undo()
            if not snap:
                return {"ok": False, "message": "これ以上前の状態はありません"}
            self._ring_buffer.clear()
            self._sync_engine_from_active_tab()
            self.critic_logs.append(f"◀ タイムマシン: 第{snap['generation']}世代の状態へ戻りました ({snap['latest_score']:.1f}点)")
            if snap.get("events"):
                self.play_events_now(snap["events"], source_tag="timemachine_undo")
            return {"ok": True, "generation": snap["generation"], "virtuoso": self.get_telemetry_snapshot()}

    def timemachine_redo(self) -> Dict[str, Any]:
        with self._lock:
            self._save_active_tab_from_engine()
            snap = self.workspace.redo()
            if not snap:
                return {"ok": False, "message": "次の状態はありません"}
            self._ring_buffer.clear()
            self._sync_engine_from_active_tab()
            self.critic_logs.append(f"▶ タイムマシン: 第{snap['generation']}世代の状態へ進みました ({snap['latest_score']:.1f}点)")
            if snap.get("events"):
                self.play_events_now(snap["events"], source_tag="timemachine_redo")
            return {"ok": True, "generation": snap["generation"], "virtuoso": self.get_telemetry_snapshot()}

    def timemachine_scrub(self, target_gen: int) -> Dict[str, Any]:
        with self._lock:
            self._save_active_tab_from_engine()
            snap = self.workspace.scrub_to_generation(target_gen)
            if not snap:
                return {"ok": False, "message": "履歴スナップショットがまだありません"}
            self._ring_buffer.clear()
            self._sync_engine_from_active_tab()
            self.critic_logs.append(f"⏪ タイムマシンスライダー: 第{snap['generation']}世代を表示中 ({snap['latest_score']:.1f}点)")
            if snap.get("events"):
                self.play_events_now(snap["events"], source_tag="timemachine_scrub")
            return {"ok": True, "generation": snap["generation"], "virtuoso": self.get_telemetry_snapshot()}

    def play_ab_comparison(self, which: str = "both") -> Dict[str, Any]:
        """Plays State A (Before Evolution) and/or State B (After Evolution) for instant ear comparison."""
        with self._lock:
            tab = self.workspace.get_active_tab()
            state_a = tab.ab_state_a
            state_b = tab.ab_state_b
            if not state_a or not state_b:
                return {"ok": False, "message": "比較用の履歴がまだ十分ではありません"}

            events_to_play: List[Dict[str, Any]] = []
            if which in ("a", "both") and state_a.get("events"):
                for ev in state_a["events"]:
                    events_to_play.append(dict(ev))
            if which == "both" and state_a.get("events") and state_b.get("events"):
                dur_a = max((float(e.get("time_offset_s", 0.0)) + float(e.get("duration_s", 0.4)) for e in state_a["events"]), default=3.5)
                gap = dur_a + 0.55
                for ev in state_b["events"]:
                    ev2 = dict(ev)
                    ev2["time_offset_s"] = round(float(ev2.get("time_offset_s", 0.0)) + gap, 4)
                    events_to_play.append(ev2)
            elif which == "b" and state_b.get("events"):
                events_to_play = [dict(ev) for ev in state_b["events"]]

            if events_to_play:
                self.play_events_now(events_to_play, source_tag=f"ab_compare_{which}")
            return {
                "ok": True,
                "which": which,
                "a_gen": state_a["generation"],
                "a_score": state_a["latest_score"],
                "b_gen": state_b["generation"],
                "b_score": state_b["latest_score"],
            }

    def rescue_last_30s_to_tab(self, seconds: float = 35.0) -> Dict[str, Any]:
        """Extracts the last 30-60 seconds of live performed events into a pinned Song Tab."""
        with self._lock:
            self._save_active_tab_from_engine()
            now = time.perf_counter()
            cutoff = now - max(10.0, min(60.0, float(seconds)))
            recent_chunks = [c for c in self._played_chunks_ring if c["recorded_at"] >= cutoff]
            if not recent_chunks and self.latest_events:
                recent_chunks = [{"recorded_at": now, "events": self.latest_events, "duration_s": 4.4}]
            if not recent_chunks:
                return {"ok": False, "message": "救出できる演奏データがまだありません"}

            merged_events: List[Dict[str, Any]] = []
            offset = 0.0
            for ch in recent_chunks:
                for ev in ch["events"]:
                    ev_copy = dict(ev)
                    ev_copy["time_offset_s"] = round(offset + float(ev.get("time_offset_s", 0.0)), 4)
                    merged_events.append(ev_copy)
                offset += float(ch.get("duration_s", 4.0))

            repaired, _ = LocalPreflightChecker.audit_and_repair(merged_events)
            self._rescued_count += 1
            active_tab = self.workspace.get_active_tab()
            pinned_tab = self.workspace.create_new_tab(
                title=f"📌 救出フレーズ #{self._rescued_count} (Gen {active_tab.current_generation})",
                character_tag=active_tab.character_tag,
                tonic_midi=active_tab.tonic_midi,
                expression_x=active_tab.expression_x,
                expression_y=active_tab.expression_y,
                pinned=True,
                pinned_events=repaired,
                elite_genome_dict=active_tab.elite_genome,
            )
            pinned_tab.latest_score = active_tab.latest_score
            pinned_tab.best_score_ever = active_tab.best_score_ever
            pinned_tab.latest_human_scores = dict(active_tab.latest_human_scores)
            pinned_tab.latest_inspiration = copy.deepcopy(active_tab.latest_inspiration)
            pinned_tab.generation_snapshots[1] = pinned_tab.make_snapshot_payload()
            self._sync_engine_from_active_tab(pinned_tab)
            self.critic_logs.append(f"⏪ タイムカプセル救出: 直近{len(recent_chunks)}フレーズを「{pinned_tab.title}」へ保存しました！")
            return {
                "ok": True,
                "tab_id": pinned_tab.tab_id,
                "title": pinned_tab.title,
                "rescued_events": len(repaired),
                "virtuoso": self.get_telemetry_snapshot(),
            }

    # -------------------------------------------------------------------------
    # Diverse Resets, Mood Shift, Spark, Expression Pad & Bookmarks
    # -------------------------------------------------------------------------
    def full_reset_new_song(self, title: Optional[str] = None) -> Dict[str, Any]:
        """🔄 完全リセット（記憶を白紙にして一から新曲を作る）"""
        with self._lock:
            self._ring_buffer.clear()
            self.safety_guard.emergency_panic()
            tab = self.workspace.get_active_tab()
            fresh_tonic = self._rng.choice([55, 57, 60, 61, 62, 64, 65, 67])
            fresh_genome = MusicalGenome(
                generation=1,
                expression_x=tab.expression_x,
                expression_y=tab.expression_y,
            )
            tab.title = title or f"🎵 新曲 ({midi_to_name(fresh_tonic)}からスタート)"
            tab.tonic_midi = fresh_tonic
            tab.bar_index = 0
            tab.arc_idx = 0
            tab.current_generation = 1
            tab.elite_genome = fresh_genome.to_dict()
            tab.population_genomes = [fresh_genome.to_dict()]
            tab.prev_bar_state = {"bar_index": 0, "last_melody_note": fresh_tonic + 12, "last_bass_note": max(34, fresh_tonic - 12)}
            tab.history_chords = []
            tab.evolution_history = []
            tab.generation_snapshots = {}
            tab.undo_stack = []
            tab.redo_stack = []
            tab.ab_state_a = None
            tab.ab_state_b = None
            tab.latest_score = 71.0
            tab.best_score_ever = 71.0
            tab.live_story_subtitle_ja = "記憶を白紙に戻しました。第1世代の素朴な原石から新しい楽曲を紡ぎ始めます…"
            self._sync_engine_from_active_tab(tab)
            self._stream_cursor_t = time.perf_counter() + 0.30
            if self.streaming_active:
                self._prepopulate_buffer_locked()
            self.critic_logs.append("🔄 完全リセット実行: 第1世代（白紙）から新曲の作曲を開始しました。")
        return {"ok": True, "virtuoso": self.get_telemetry_snapshot()}

    def mood_change_keep_brain(self, preset_id: Optional[str] = None) -> Dict[str, Any]:
        """✨ 曲の雰囲気だけ変える（進化した頭脳のまま別の曲調へ）"""
        with self._lock:
            self._ring_buffer.clear()
            preset_keys = list(CHARACTER_PRESETS.keys())
            chosen_key = preset_id if preset_id in CHARACTER_PRESETS else self._rng.choice(preset_keys)
            preset = CHARACTER_PRESETS[chosen_key]
            tab = self.workspace.get_active_tab()
            tab.character_tag = chosen_key
            tab.expression_x = float(preset["expression_x"])
            tab.expression_y = float(preset["expression_y"])
            tab.tonic_midi = int(preset["tonic_midi"])
            self.generator.set_expression(tab.expression_x, tab.expression_y)
            self.directives["prefer_minor"] = bool(preset.get("prefer_minor", False))
            self.directives["prefer_chromatic_bass"] = bool(preset.get("prefer_chromatic_bass", False))
            self.directives["half_pedal_target"] = int(preset.get("half_pedal_target", 62))
            self.active_motif = dict(self.active_motif)
            self.active_motif["tonic_midi"] = tab.tonic_midi
            self.live_story_subtitle_ja = f"✨ 進化した演奏技術のまま、雰囲気を「{preset['title']}」へ転換しました！"
            self.critic_logs.append(f"✨ 雰囲気チェンジ（頭脳維持）: {preset['title']}")
            if self.streaming_active:
                self._prepopulate_buffer_locked()
        return {"ok": True, "preset": chosen_key, "virtuoso": self.get_telemetry_snapshot()}

    def trigger_inspiration_spark(self) -> Dict[str, Any]:
        """⚡ ひらめきスパーク（急にドラマチックな展開・転調を起こす）"""
        with self._lock:
            self._ring_buffer.clear()
            self._pending_spark = True
            self.arc_idx = 2  # Jump right into Climax!
            tab = self.workspace.get_active_tab()
            tab.expression_y = min(1.0, max(0.75, tab.expression_y + 0.25))
            self.generator.set_expression(tab.expression_x, tab.expression_y)
            self.live_story_subtitle_ja = "⚡ ひらめきスパーク発生！ ドラマチックな転調と情熱的なクライマックスへ飛び込みます！"
            self.critic_logs.append("⚡ ひらめきスパーク: 劇的転調・クライマックス展開を即時スケジュール")
            if self.streaming_active:
                self._prepopulate_buffer_locked()
        return {"ok": True, "virtuoso": self.get_telemetry_snapshot()}

    def register_human_like_vote(self) -> Dict[str, Any]:
        """👍 今のフレーズいいね！（人間の好評を遺伝情報に即時ロック＆強化）"""
        with self._lock:
            tab = self.workspace.get_active_tab()
            tab.locked_genes.append(copy.deepcopy(self.generator.elite_genome.to_dict()))
            tab.locked_genes = tab.locked_genes[-10:]
            tab.bookmarks.append(
                {
                    "generation": self.generator.elite_genome.generation,
                    "score": self.latest_score,
                    "label": self.latest_inspiration.get("primary_lineage", "virtuoso"),
                    "subtitle": self.live_story_subtitle_ja[:48],
                    "timestamp": time.strftime("%H:%M:%S"),
                }
            )
            self.critic_logs.append(f"👍 いいね！登録 (Gen {self.generator.elite_genome.generation}): 現在の歌い回しと和声遺伝子を強化・保存しました。")
        return {"ok": True, "bookmarks": tab.bookmarks, "virtuoso": self.get_telemetry_snapshot()}

    def set_expression_pad(self, x: float, y: float) -> Dict[str, Any]:
        """2D Pad: x in [-1.0 しっとり歌う .. +1.0 活発に跳ねる], y in [0.0 穏やか .. 1.0 情熱的クライマックス]"""
        with self._lock:
            cx = max(-1.0, min(1.0, float(x)))
            cy = max(0.0, min(1.0, float(y)))
            tab = self.workspace.get_active_tab()
            tab.expression_x = cx
            tab.expression_y = cy
            self.generator.set_expression(cx, cy)
            self.latest_inspiration = HumanReferenceTeacher.compute_emergent_inspiration(
                self.generator.elite_genome.to_dict(),
                expression_x=cx,
                expression_y=cy,
                bar_idx=self.bar_index,
            )
        return {"ok": True, "expression_x": cx, "expression_y": cy, "virtuoso": self.get_telemetry_snapshot()}

    def apply_character_preset(self, preset_id: str) -> Dict[str, Any]:
        return self.mood_change_keep_brain(preset_id=preset_id)

    def play_events_now(self, events: List[Dict[str, Any]], source_tag: str = "replay") -> None:
        repaired, report = LocalPreflightChecker.audit_and_repair(events)
        self.last_preflight_report = report
        base_t = time.perf_counter() + 0.16
        self._dispatch_events_to_guard(repaired, base_t, source_tag=source_tag)

    # -------------------------------------------------------------------------
    # Streaming & Generation Core
    # -------------------------------------------------------------------------
    def start_stream(self) -> Dict[str, Any]:
        with self._lock:
            if not self.streaming_active:
                self._stream_cursor_t = time.perf_counter() + 0.35
                self.streaming_active = True
                self._prepopulate_buffer_locked()
        if self.telemetry_store:
            self.telemetry_store.log_event("🟢 自動作曲・連続演奏ストリームを開始しました。")
        return self.get_telemetry_snapshot()

    def stop_stream(self) -> Dict[str, Any]:
        with self._lock:
            self.streaming_active = False
            self._ring_buffer.clear()
        self.safety_guard.emergency_panic()
        if self.telemetry_store:
            self.telemetry_store.log_event("⏸️ 自動演奏を一時停止しました。")
        return self.get_telemetry_snapshot()

    def absorb_live_motif(self, notes: List[int], source: str = "gui_or_cello") -> Dict[str, Any]:
        """Internal handler for live MIDI IN / Cello acoustic pitch motifs."""
        clean_notes = [int(n) for n in notes if 28 <= int(n) <= 96] or [62, 65, 69, 68, 65, 62]
        tonic = clean_notes[0]
        while tonic > 65:
            tonic -= 12
        while tonic < 48:
            tonic += 12
        new_motif = {
            "motif_id": f"motif_live_{int(time.time())}",
            "title": f"共鳴フレーズ ({source})",
            "composer": f"共演セッション ({source})",
            "key": f"{midi_to_name(tonic)}",
            "tonic_midi": tonic,
            "scale_intervals": [0, 2, 3, 5, 7, 8, 10] if self.directives.get("prefer_minor") else [0, 2, 4, 5, 7, 9, 11],
            "melody_pitches": clean_notes[:8],
            "interval_vector": [p - clean_notes[0] for p in clean_notes[:8]],
            "rhythm_ratios": [1.0] * max(1, len(clean_notes[:8]) - 1),
            "contrary_motion_ratio": 0.75,
            "half_pedal_profile": {"mean_cc64": 64, "min_cc64": 20, "max_cc64": 70},
            "tags": ["live_dialogue", source],
            "source_file": "live_stream",
        }
        with self._lock:
            self._pending_injected_motif = new_motif
            self.active_motif = new_motif
            self.critic_logs.append(f"🎻 共演音の反応 ({source}): {[midi_to_name(n) for n in clean_notes[:5]]} から自然な応答フレーズを紡ぎます")
        return {"ok": True, "motif": new_motif}

    def _generate_next_chunk_locked(
        self,
        record_to_obsidian: bool = True,
        candidate_count: int = 8,
        prefer_multiprocess: bool = False,
    ) -> Dict[str, Any]:
        if self._pending_injected_motif is not None:
            self.active_motif = self._pending_injected_motif
            self._pending_injected_motif = None
        elif self.bar_index > 0 and self.bar_index % 12 == 0 and self.motif_list:
            self.active_motif = self.seed_pool.pick_next_motif(self.active_motif.get("motif_id"))

        stage = ARC_STAGES[self.arc_idx % len(ARC_STAGES)]
        if self._pending_spark:
            stage = ARC_STAGES[2]  # Climax
            self._pending_spark = False
        elif self.bar_index > 0 and self.bar_index % 2 == 0:
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
            candidate_count=candidate_count,
            prefer_multiprocess=prefer_multiprocess,
        )
        winner = res["winner"]
        gen_num = res["generation"]

        # Verify winning events through <0.1s LocalPreflightChecker
        clean_events, preflight_report = LocalPreflightChecker.audit_and_repair(winner["events"])
        winner["events"] = clean_events
        self.last_preflight_report = preflight_report

        self.bar_index += self.BARS_PER_CANDIDATE
        self.prev_bar_state = {
            "bar_index": self.bar_index,
            "last_melody_note": winner["last_melody_note"],
            "last_bass_note": winner["last_bass_note"],
        }
        self.history_chords.append(winner["chord_label"])
        if len(self.history_chords) > 32:
            self.history_chords = self.history_chords[-32:]

        eval_res = winner["evaluation"]
        self.latest_branches = res["branches"]
        self.latest_axes = eval_res["axes"]
        self.latest_human_scores = eval_res.get("human_scores", self.latest_human_scores)
        self.latest_inspiration = eval_res.get("inspiration", self.latest_inspiration)
        self.live_story_subtitle_ja = eval_res.get("live_story_subtitle_ja", self.live_story_subtitle_ja)
        self.pianist_thought_bubble_ja = eval_res.get("pianist_thought_bubble_ja", self.pianist_thought_bubble_ja)
        self.latest_events = clean_events
        self.latest_score = res["best_score"]
        self.mean_score = res["mean_candidate_score"]
        self.best_score_ever = max(self.best_score_ever, res["best_score"])

        self.critic_logs.append(eval_res["commentary_ja"])
        self.evolution_history.append(
            {
                "generation": gen_num,
                "best_score": res["best_score"],
                "mean_score": res["mean_candidate_score"],
                "axes": dict(self.latest_axes),
                "human_scores": dict(self.latest_human_scores),
                "arc_stage": stage[0],
                "chord_label": winner["chord_label"],
                "transform": winner["transform_type"],
            }
        )
        if len(self.evolution_history) > 120:
            self.evolution_history = self.evolution_history[-120:]

        # Persist state & Time-Machine snapshot into active Song Tab
        self._save_active_tab_from_engine()
        self.workspace.record_generation_state(self.workspace.get_active_tab(), res)

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
            "events": clean_events,
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
                        if (self._stream_cursor_t - now) < self.LOOKAHEAD_TARGET_S and self._ring_buffer:
                            chunk_to_schedule = self._ring_buffer.popleft()
                            start_t = self._stream_cursor_t
                            self._stream_cursor_t += float(chunk_to_schedule["duration_s"])
                            self._played_chunks_ring.append(
                                {
                                    "recorded_at": now,
                                    "duration_s": chunk_to_schedule["duration_s"],
                                    "events": copy.deepcopy(chunk_to_schedule["events"]),
                                }
                            )

                if chunk_to_schedule is not None:
                    self._schedule_chunk_to_guard(chunk_to_schedule, start_t)
            except Exception:
                pass
            time.sleep(0.04)

    def _dispatch_events_to_guard(
        self, events: List[Dict[str, Any]], start_t: float, source_tag: str = "virtuoso"
    ) -> None:
        legato_overlap_s = max(
            0.005,
            min(0.015, self.generator.elite_genome.legato_overlap_ms / 1000.0),
        )
        pedal_preroll_ms = max(
            30.0,
            min(50.0, self.generator.elite_genome.pedal_preroll_ms),
        )
        for ev in events:
            t_off = float(ev.get("time_offset_s", 0.0))
            note = int(ev.get("note", 0))
            if note == 0 and ev.get("pedal_cc64") is not None:
                self.safety_guard.schedule_chord_or_notes(
                    notes=[],
                    target_sound_time=start_t + t_off,
                    use_vdl_compensation=False,
                    pedal_cc64=int(ev["pedal_cc64"]),
                    pedal_preroll_ms=pedal_preroll_ms,
                    source_tag=f"{source_tag}_pedal",
                )
            elif note > 0:
                rel_v = ev.get("release_velocity")
                self.safety_guard.schedule_chord_or_notes(
                    notes=[(note, int(ev["velocity"]), float(ev["duration_s"]))],
                    target_sound_time=start_t + t_off,
                    use_vdl_compensation=True,
                    sympathetic_halo=bool(ev.get("sympathetic_halo", False)),
                    legato_overlap_s=0.0 if ev.get("articulation") == "staccato" else legato_overlap_s,
                    source_tag=str(ev.get("source_tag", source_tag)),
                    release_velocity=int(rel_v) if rel_v is not None else None,
                )

    def _schedule_chunk_to_guard(self, chunk: Dict[str, Any], start_t: float) -> None:
        self._dispatch_events_to_guard(chunk["events"], start_t, source_tag="virtuoso")

    def run_hyper_speed_training(
        self, num_generations: int = 50, speed_multiplier: float = 50.0
    ) -> Dict[str, Any]:
        """
        100x Fast Background Evolution Bootcamp: evaluates 12 parallel candidates per generation
        on Apple Silicon cores so scores visibly climb from raw Gen 1 to master-level Gen 50!
        """
        num_generations = max(1, min(80, int(num_generations)))
        start_time = time.perf_counter()
        self.hyper_training_status = {
            "running": True,
            "progress_pct": 0,
            "speed_multiplier": speed_multiplier,
            "last_run_summary": f"裏で一気に{num_generations}世代進化トレーニング中...",
        }

        gen_scores: List[Dict[str, Any]] = []
        for idx in range(num_generations):
            with self._lock:
                chunk = self._generate_next_chunk_locked(
                    record_to_obsidian=True,
                    candidate_count=12,
                    prefer_multiprocess=(idx == 0 and num_generations >= 10),
                )
                gen_scores.append(
                    {
                        "generation": chunk["generation"],
                        "score": chunk["score"],
                        "mean_score": self.mean_score,
                    }
                )
                self.hyper_training_status["progress_pct"] = int(((idx + 1) / num_generations) * 100)
            time.sleep(max(0.002, 0.12 / max(10.0, speed_multiplier)))

        elapsed = round(time.perf_counter() - start_time, 2)
        first_s = gen_scores[0]["score"] if gen_scores else 0.0
        last_s = gen_scores[-1]["score"] if gen_scores else 0.0
        summary = (
            f"{num_generations}世代の進化完了 ({elapsed}秒): "
            f"{first_s:.1f}点 → {last_s:.1f}点 (最高 {self.best_score_ever:.1f}点)"
        )
        self.hyper_training_status = {
            "running": False,
            "progress_pct": 100,
            "speed_multiplier": speed_multiplier,
            "last_run_summary": summary,
        }
        if self.telemetry_store:
            self.telemetry_store.log_event(f"⚡ [高速トレーニング完了] {summary}")

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
            ws_summary = self.workspace.get_summary()
            active_tab = self.workspace.get_active_tab()
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
                    "title": active_tab.title,
                    "composer": "AIオリジナル自発作曲",
                    "key": midi_to_name(active_tab.tonic_midi),
                    "melody_pitches": self.active_motif.get("melody_pitches", []),
                },
                "latest_score": self.latest_score,
                "mean_score": self.mean_score,
                "best_score_ever": self.best_score_ever,
                "latest_axes": dict(self.latest_axes),
                "human_scores": dict(self.latest_human_scores),
                "inspiration": copy.deepcopy(self.latest_inspiration),
                "live_story_subtitle_ja": self.live_story_subtitle_ja,
                "pianist_thought_bubble_ja": self.pianist_thought_bubble_ja,
                "expression_pad": {"x": active_tab.expression_x, "y": active_tab.expression_y},
                "branches": list(self.latest_branches),
                "critic_logs": list(self.critic_logs)[-14:],
                "evolution_history": list(self.evolution_history)[-50:],
                "elite_genome": self.generator.elite_genome.to_dict(),
                "directives": dict(self.directives),
                "hyper_training": dict(self.hyper_training_status),
                "workspace": ws_summary,
                "hardware_accel": self.generator.accel_info,
                "preflight": dict(self.last_preflight_report),
                "latest_events": list(self.latest_events)[:80],
                "rescued_count": self._rescued_count,
            }

    def close(self) -> None:
        self._running = False
        self.streaming_active = False
        self.generator.shutdown_pools()
