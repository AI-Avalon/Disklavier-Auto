"""
Multi-Song Workspace Tabs & Time-Machine Undo/Redo/Scrubber Manager.

Allows performers to keep multiple compositions open as browser-style tabs, jump back
to any generation via the Time-Machine slider, Undo/Redo state changes in <10ms,
compare Before (A) vs After (B) evolution, and rescue the last 30-60s of live playing
into a pinned Song Tab.
"""

from __future__ import annotations

import copy
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.evolution.actor_critic import MusicalGenome
from src.evolution.human_reference_teacher import HumanReferenceTeacher
from src.telemetry.telemetry_store import TELEMETRY_DIR, record_caught_exception


WORKSPACE_SNAPSHOT_PATH = TELEMETRY_DIR / "workspace_snapshot.json"


def _default_radar_6axis() -> Dict[str, float]:
    return {
        "melody_beauty": 74.0,
        "harmonic_drama": 71.0,
        "counterpoint": 69.0,
        "human_realism": 73.0,
        "virtuoso_brilliance": 68.0,
        "disklavier_safety": 100.0,
        "total_score_100": 75.0,
    }


@dataclass
class SongTabState:
    tab_id: str
    title: str
    character_tag: str = "romantic"
    tonic_midi: int = 62
    expression_x: float = -0.45
    expression_y: float = 0.68
    pinned: bool = False
    pinned_events: Optional[List[Dict[str, Any]]] = None
    bar_index: int = 0
    arc_idx: int = 0
    current_generation: int = 1
    elite_genome: Dict[str, Any] = field(default_factory=lambda: MusicalGenome(generation=1).to_dict())
    population_genomes: List[Dict[str, Any]] = field(default_factory=list)
    prev_bar_state: Dict[str, Any] = field(default_factory=lambda: {"bar_index": 0, "last_melody_note": 74, "last_bass_note": 50})
    history_chords: List[str] = field(default_factory=list)
    evolution_history: List[Dict[str, Any]] = field(default_factory=list)
    latest_score: float = 72.0
    mean_score: float = 70.0
    best_score_ever: float = 72.0
    latest_axes: Dict[str, float] = field(
        default_factory=lambda: {
            "voice_leading": 15.0,
            "motivic_continuity": 13.0,
            "harmonic_originality": 13.0,
            "expressive_articulation": 10.0,
            "physical_playability": 19.0,
        }
    )
    latest_human_scores: Dict[str, float] = field(
        default_factory=lambda: {
            "legato_smoothness_100": 72.0,
            "rhythmic_vivacity_100": 70.0,
            "melodic_story_100": 71.0,
            "hardware_safety_100": 100.0,
            "human_likeness_100": 74.0,
        }
    )
    latest_radar_6axis: Dict[str, float] = field(default_factory=_default_radar_6axis)
    work_metadata: Dict[str, Any] = field(
        default_factory=lambda: HumanReferenceTeacher.generate_meta_director_work_plan(0, "romantic")
    )
    revision_log: List[Dict[str, Any]] = field(default_factory=list)
    latest_inspiration: Dict[str, Any] = field(default_factory=dict)
    live_story_subtitle_ja: str = "しっとりと歌うような序奏から、新しい物語が始まります…"
    pianist_thought_bubble_ja: str = "💭 AIピアニストの狙い: 最初のモチーフを歌わせながら、和声の色彩を探しています。"
    latest_events: List[Dict[str, Any]] = field(default_factory=list)
    prev_generation_events: List[Dict[str, Any]] = field(default_factory=list)
    generation_snapshots: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    undo_stack: List[Dict[str, Any]] = field(default_factory=list)
    redo_stack: List[Dict[str, Any]] = field(default_factory=list)
    ab_state_a: Optional[Dict[str, Any]] = None
    ab_state_b: Optional[Dict[str, Any]] = None
    bookmarks: List[Dict[str, Any]] = field(default_factory=list)
    locked_genes: List[Dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def make_snapshot_payload(self) -> Dict[str, Any]:
        return {
            "generation": int(self.current_generation),
            "bar_index": int(self.bar_index),
            "arc_idx": int(self.arc_idx),
            "expression_x": float(self.expression_x),
            "expression_y": float(self.expression_y),
            "elite_genome": copy.deepcopy(self.elite_genome),
            "prev_bar_state": copy.deepcopy(self.prev_bar_state),
            "history_chords": list(self.history_chords),
            "latest_score": float(self.latest_score),
            "latest_axes": dict(self.latest_axes),
            "latest_human_scores": dict(self.latest_human_scores),
            "latest_radar_6axis": dict(self.latest_radar_6axis),
            "work_metadata": copy.deepcopy(self.work_metadata),
            "latest_inspiration": copy.deepcopy(self.latest_inspiration),
            "live_story_subtitle_ja": self.live_story_subtitle_ja,
            "pianist_thought_bubble_ja": self.pianist_thought_bubble_ja,
            "events": copy.deepcopy(self.latest_events),
            "prev_events": copy.deepcopy(self.prev_generation_events),
            "timestamp": time.time(),
        }

    def restore_snapshot_payload(self, snap: Dict[str, Any]) -> None:
        self.current_generation = int(snap.get("generation", 1))
        self.bar_index = int(snap.get("bar_index", 0))
        self.arc_idx = int(snap.get("arc_idx", 0))
        self.expression_x = float(snap.get("expression_x", self.expression_x))
        self.expression_y = float(snap.get("expression_y", self.expression_y))
        if snap.get("elite_genome"):
            self.elite_genome = copy.deepcopy(snap["elite_genome"])
        if snap.get("prev_bar_state"):
            self.prev_bar_state = copy.deepcopy(snap["prev_bar_state"])
        if "history_chords" in snap:
            self.history_chords = list(snap["history_chords"])
        self.latest_score = float(snap.get("latest_score", self.latest_score))
        if snap.get("latest_axes"):
            self.latest_axes = dict(snap["latest_axes"])
        if snap.get("latest_human_scores"):
            self.latest_human_scores = dict(snap["latest_human_scores"])
        if snap.get("latest_radar_6axis"):
            self.latest_radar_6axis = dict(snap["latest_radar_6axis"])
        if snap.get("work_metadata"):
            self.work_metadata = copy.deepcopy(snap["work_metadata"])
        if snap.get("latest_inspiration"):
            self.latest_inspiration = copy.deepcopy(snap["latest_inspiration"])
        self.live_story_subtitle_ja = snap.get("live_story_subtitle_ja", self.live_story_subtitle_ja)
        self.pianist_thought_bubble_ja = snap.get("pianist_thought_bubble_ja", self.pianist_thought_bubble_ja)
        if snap.get("events"):
            self.latest_events = copy.deepcopy(snap["events"])
        if "prev_events" in snap:
            self.prev_generation_events = copy.deepcopy(snap.get("prev_events") or [])


class MultiSongWorkspaceManager:
    """
    Browser-style workspace managing multiple parallel Song Tabs, Time-Machine generation
    scrubbing, Undo/Redo stacks, A/B comparison states, and closed-tab recovery.
    """

    MAX_UNDO = 40
    MAX_GEN_SNAPSHOTS = 60

    def __init__(self) -> None:
        self.tabs: Dict[str, SongTabState] = {}
        self.tab_order: List[str] = []
        self.closed_tabs_history: List[SongTabState] = []
        self.active_tab_id: str = ""
        self._init_default_tabs()

    def _init_default_tabs(self) -> None:
        defaults = [
            (
                "tab_1",
                "🎵 曲1: 蒼き星の奔流 Op.14",
                "tchaikovsky_passion",
                58,
                -0.52,
                0.76,
            ),
            (
                "tab_2",
                "🎵 曲2: 華麗なる円舞曲 Op.21",
                "vivace_scherzando",
                62,
                0.76,
                0.80,
            ),
            (
                "tab_3",
                "🎵 曲3: 夜想の銀雫 Op.9",
                "night_nocturne",
                61,
                -0.82,
                0.38,
            ),
        ]
        for idx, (tid, title, char_tag, tonic, ex, ey) in enumerate(defaults):
            g = MusicalGenome(generation=1, expression_x=ex, expression_y=ey)
            work_meta = HumanReferenceTeacher.generate_meta_director_work_plan(idx, char_tag)
            tab = SongTabState(
                tab_id=tid,
                title=title,
                character_tag=char_tag,
                tonic_midi=tonic,
                expression_x=ex,
                expression_y=ey,
                elite_genome=g.to_dict(),
                work_metadata=work_meta,
                prev_bar_state={
                    "bar_index": 0,
                    "last_melody_note": tonic + 12,
                    "last_bass_note": max(34, tonic - 12),
                },
            )
            self.tabs[tid] = tab
            self.tab_order.append(tid)
        self.active_tab_id = "tab_1"

    def get_active_tab(self) -> SongTabState:
        if self.active_tab_id not in self.tabs and self.tab_order:
            self.active_tab_id = self.tab_order[0]
        return self.tabs[self.active_tab_id]

    def switch_tab(self, tab_id: str) -> Optional[SongTabState]:
        if tab_id not in self.tabs:
            return None
        self.active_tab_id = tab_id
        return self.tabs[tab_id]

    def create_new_tab(
        self,
        title: Optional[str] = None,
        character_tag: str = "original_free",
        tonic_midi: int = 60,
        expression_x: float = -0.35,
        expression_y: float = 0.62,
        pinned: bool = False,
        pinned_events: Optional[List[Dict[str, Any]]] = None,
        elite_genome_dict: Optional[Dict[str, Any]] = None,
    ) -> SongTabState:
        num = len(self.tabs) + len(self.closed_tabs_history) + 1
        tid = f"tab_{uuid.uuid4().hex[:6]}"
        work_meta = HumanReferenceTeacher.generate_meta_director_work_plan(num - 1, character_tag)
        clean_title = title or f"🎵 曲{num}: {work_meta['short_ja']} {work_meta['opus']}"
        g_dict = copy.deepcopy(elite_genome_dict) if elite_genome_dict else MusicalGenome(
            generation=1, expression_x=expression_x, expression_y=expression_y
        ).to_dict()
        tab = SongTabState(
            tab_id=tid,
            title=clean_title,
            character_tag=character_tag,
            tonic_midi=tonic_midi,
            expression_x=expression_x,
            expression_y=expression_y,
            pinned=pinned,
            pinned_events=copy.deepcopy(pinned_events) if pinned_events else None,
            elite_genome=g_dict,
            work_metadata=work_meta,
            prev_bar_state={
                "bar_index": 0,
                "last_melody_note": tonic_midi + 12,
                "last_bass_note": max(34, tonic_midi - 12),
            },
            latest_events=copy.deepcopy(pinned_events) if pinned_events else [],
        )
        if pinned_events:
            tab.live_story_subtitle_ja = f"📌 「{clean_title}」をタイムカプセルから保存しました。いつでも再生や続きの進化ができます。"
        self.tabs[tid] = tab
        self.tab_order.append(tid)
        self.active_tab_id = tid
        return tab

    def close_tab(self, tab_id: str) -> Dict[str, Any]:
        if len(self.tab_order) <= 1 or tab_id not in self.tabs:
            return {"ok": False, "reason": "最後の1曲タブは閉じられません"}
        removed = self.tabs.pop(tab_id)
        self.tab_order = [t for t in self.tab_order if t != tab_id]
        self.closed_tabs_history.append(removed)
        if len(self.closed_tabs_history) > 15:
            self.closed_tabs_history = self.closed_tabs_history[-15:]
        if self.active_tab_id == tab_id:
            self.active_tab_id = self.tab_order[0]
        return {"ok": True, "closed_title": removed.title, "active_tab_id": self.active_tab_id}

    def restore_last_closed_tab(self) -> Optional[SongTabState]:
        if not self.closed_tabs_history:
            return None
        restored = self.closed_tabs_history.pop()
        self.tabs[restored.tab_id] = restored
        self.tab_order.append(restored.tab_id)
        self.active_tab_id = restored.tab_id
        return restored

    def record_generation_state(
        self,
        tab: SongTabState,
        chunk_res: Dict[str, Any],
    ) -> None:
        """Records undo point, generation scrubber snapshot, note diffs, and A/B states."""
        prev_snap = tab.make_snapshot_payload()
        if not tab.undo_stack or tab.undo_stack[-1]["generation"] != prev_snap["generation"]:
            tab.undo_stack.append(prev_snap)
            if len(tab.undo_stack) > self.MAX_UNDO:
                tab.undo_stack = tab.undo_stack[-self.MAX_UNDO:]
            tab.redo_stack.clear()

        prev_score = float(tab.latest_score)
        prev_gen = int(tab.current_generation)
        if tab.latest_events:
            tab.prev_generation_events = copy.deepcopy(tab.latest_events)

        winner = chunk_res["winner"]
        gen = int(chunk_res["generation"])
        eval_res = winner["evaluation"]

        tab.current_generation = gen
        tab.elite_genome = copy.deepcopy(chunk_res["elite_genome"])
        tab.latest_score = float(chunk_res["best_score"])
        tab.mean_score = float(chunk_res["mean_candidate_score"])
        tab.best_score_ever = max(tab.best_score_ever, tab.latest_score)
        tab.latest_axes = dict(eval_res["axes"])
        tab.latest_human_scores = dict(eval_res.get("human_scores", tab.latest_human_scores))
        if eval_res.get("radar_6axis"):
            tab.latest_radar_6axis = dict(eval_res["radar_6axis"])
        tab.latest_inspiration = copy.deepcopy(eval_res.get("inspiration", {}))
        tab.live_story_subtitle_ja = eval_res.get("live_story_subtitle_ja", tab.live_story_subtitle_ja)
        tab.pianist_thought_bubble_ja = eval_res.get("pianist_thought_bubble_ja", tab.pianist_thought_bubble_ja)
        tab.latest_events = copy.deepcopy(winner["events"])

        commentary = eval_res.get("commentary_ja", "")
        if prev_gen != gen or not tab.revision_log:
            tab.revision_log.append(
                {
                    "from_gen": prev_gen,
                    "to_gen": gen,
                    "from_score": round(prev_score, 1),
                    "to_score": round(tab.latest_score, 1),
                    "note_ja": commentary or tab.pianist_thought_bubble_ja,
                    "timestamp": time.time(),
                }
            )
            tab.revision_log = tab.revision_log[-24:]

        new_snap = tab.make_snapshot_payload()
        tab.generation_snapshots[gen] = new_snap
        if len(tab.generation_snapshots) > self.MAX_GEN_SNAPSHOTS:
            oldest_k = sorted(tab.generation_snapshots.keys())[0]
            if oldest_k != 1:  # Always preserve Gen 1 raw stone for A/B comparison!
                tab.generation_snapshots.pop(oldest_k, None)

        if tab.ab_state_a is None or gen <= 2:
            tab.ab_state_a = copy.deepcopy(new_snap)
        tab.ab_state_b = copy.deepcopy(new_snap)

    def undo(self) -> Optional[Dict[str, Any]]:
        tab = self.get_active_tab()
        if not tab.undo_stack:
            return None
        current_snap = tab.make_snapshot_payload()
        tab.redo_stack.append(current_snap)
        prev_snap = tab.undo_stack.pop()
        tab.restore_snapshot_payload(prev_snap)
        return prev_snap

    def redo(self) -> Optional[Dict[str, Any]]:
        tab = self.get_active_tab()
        if not tab.redo_stack:
            return None
        current_snap = tab.make_snapshot_payload()
        tab.undo_stack.append(current_snap)
        next_snap = tab.redo_stack.pop()
        tab.restore_snapshot_payload(next_snap)
        return next_snap

    def scrub_to_generation(self, target_gen: int) -> Optional[Dict[str, Any]]:
        tab = self.get_active_tab()
        if not tab.generation_snapshots:
            return None
        available_gens = sorted(tab.generation_snapshots.keys())
        closest_gen = min(available_gens, key=lambda g: abs(g - int(target_gen)))
        snap = tab.generation_snapshots[closest_gen]
        tab.undo_stack.append(tab.make_snapshot_payload())
        if len(tab.undo_stack) > self.MAX_UNDO:
            tab.undo_stack = tab.undo_stack[-self.MAX_UNDO:]
        tab.restore_snapshot_payload(snap)
        return snap

    def save_to_disk(self, path: Optional[Path] = None) -> Path:
        out = path or WORKSPACE_SNAPSHOT_PATH
        data = {
            "active_tab_id": self.active_tab_id,
            "tab_order": list(self.tab_order),
            "tabs": {tid: asdict(t) for tid, t in self.tabs.items()},
        }
        out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return out

    def load_from_disk(self, path: Optional[Path] = None) -> bool:
        src = path or WORKSPACE_SNAPSHOT_PATH
        if not src.exists():
            return False
        try:
            raw = json.loads(src.read_text(encoding="utf-8"))
            loaded_tabs: Dict[str, SongTabState] = {}
            valid_fields = set(SongTabState.__dataclass_fields__.keys())
            for tid, td in raw.get("tabs", {}).items():
                gs = {int(k): v for k, v in td.get("generation_snapshots", {}).items()}
                td["generation_snapshots"] = gs
                clean_td = {k: v for k, v in td.items() if k in valid_fields}
                loaded_tabs[tid] = SongTabState(**clean_td)
            if loaded_tabs:
                self.tabs = loaded_tabs
                self.tab_order = [tid for tid in raw.get("tab_order", []) if tid in loaded_tabs]
                self.active_tab_id = raw.get("active_tab_id", self.tab_order[0])
                return True
        except Exception as exc:
            record_caught_exception("song_workspace.load_from_disk", exc)
            return False
        return False

    def get_summary(self) -> Dict[str, Any]:
        active = self.get_active_tab()
        gens = sorted(active.generation_snapshots.keys()) or [active.current_generation]
        return {
            "active_tab_id": active.tab_id,
            "work_metadata": copy.deepcopy(active.work_metadata),
            "latest_radar_6axis": dict(active.latest_radar_6axis),
            "revision_log": list(active.revision_log[-10:]),
            "tabs": [
                {
                    "tab_id": self.tabs[tid].tab_id,
                    "title": self.tabs[tid].title,
                    "work_title_ja": self.tabs[tid].work_metadata.get("title_ja", self.tabs[tid].title),
                    "opus": self.tabs[tid].work_metadata.get("opus", ""),
                    "character_tag": self.tabs[tid].character_tag,
                    "pinned": self.tabs[tid].pinned,
                    "generation": self.tabs[tid].current_generation,
                    "latest_score": round(self.tabs[tid].latest_score, 1),
                    "best_score": round(self.tabs[tid].best_score_ever, 1),
                    "expression_x": round(self.tabs[tid].expression_x, 2),
                    "expression_y": round(self.tabs[tid].expression_y, 2),
                }
                for tid in self.tab_order
            ],
            "closed_tabs_count": len(self.closed_tabs_history),
            "closed_tabs_titles": [t.title for t in self.closed_tabs_history[-5:]],
            "time_machine": {
                "current_generation": active.current_generation,
                "min_generation": min(gens),
                "max_generation": max(gens),
                "available_generations": gens,
                "can_undo": len(active.undo_stack) > 0,
                "can_redo": len(active.redo_stack) > 0,
                "has_ab_states": (active.ab_state_a is not None and active.ab_state_b is not None),
                "ab_a_gen": active.ab_state_a["generation"] if active.ab_state_a else 1,
                "ab_a_score": active.ab_state_a["latest_score"] if active.ab_state_a else 72.0,
                "ab_b_gen": active.ab_state_b["generation"] if active.ab_state_b else active.current_generation,
                "ab_b_score": active.ab_state_b["latest_score"] if active.ab_state_b else active.latest_score,
                "bookmarks_count": len(active.bookmarks),
                "bookmarks": list(active.bookmarks[-8:]),
            },
        }
