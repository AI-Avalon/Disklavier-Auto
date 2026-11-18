r"""
Layer 3: Idiomatic Two-Hand Co-Composition Engine, RTX 3070 Corpus Phrase Transformer,
Masterwork & MusicalityCritic Auditing, and Continuous Dual-Pedal Curves.

Core Architecture:
- Indexes classical MIDI corpora (`data/imslp_midi`, `data/human_references`,
  `data/maestro-v3.0.0`, `data/midi`) via `symusic` + CUDA/NumPy cosine retrieval.
- Queries `CorpusPhraseModelManager` (`data/models/classical_phrase_model.pt` trained on RTX 3070)
  for cantabile melody degree curves, idiomatic LH accompaniment patterns
  (`arpeggio_root_5th_10th`, `pedal_point_inner_voice`, `syncopated_offbeat_harmony`,
  `una_corda_choral_recall`), 4-3 / 9-8 / 7-6 suspension resolutions, and agogic/velocity arches.
- Integrates `CoCompositionArchitect` (`MacroTurnPlan` + `ThematicDNA`) to generate 2-bar
  (8 melody notes + breathing LH/inner voices = 18-28 events) candidates A, B, C, and contrastive D,
  with post-leap gap-fill, single melodic apex, voice-leading bridges, Melody Lead, and Act cadences.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from symusic import Note, Score, Track

from src.stage.co_composition_architect import (
    SCALE_INTERVALS,
    ThematicDNA,
    quantize_pitch_to_tonality,
)
from src.stage.corpus_phrase_model import (
    CorpusPhraseModelManager,
    PhraseBlueprint,
)
from src.stage.layer1_perception import midi_to_note_name
from src.stage.layer2_intent_llm import MusicalIntentSchema
from src.stage.musicality_critic import MusicalityAuditReport, MusicalityCritic

try:
    import torch

    _HAS_CUDA = bool(torch.cuda.is_available())
except Exception:  # pragma: no cover
    torch = None  # type: ignore[assignment]
    _HAS_CUDA = False

REPO_ROOT = Path(__file__).resolve().parents[2]

BUILTIN_CHAMBER_SEED_DICTIONARY: List[Dict[str, Any]] = [
    {
        "id": "grammar_contrary_open_fifth",
        "title": "Open Fifth Bass & Contrary Soprano Leap Grammar",
        "lh_intervals": [0, 7, 12, 16],
        "rh_intervals": [0, 5, 12, 10, 7, 14],
        "rhythm_beats": [0.75, 0.35, 0.65, 0.45, 0.85, 1.15],
        "velocities": [58, 68, 76, 72, 64, 56],
        "voicing_spread": 24,
        "contrary_ratio": 0.78,
        "tension_level": 0.42,
        "momentum": 0.55,
        "pedal_curve_summary": "delayed_half_repedal",
    },
    {
        "id": "grammar_suspension_inner_voice",
        "title": "4-3 / 9-8 Suspension & Tenor Counterline Grammar",
        "lh_intervals": [0, 7, 14, 15],
        "rh_intervals": [5, 4, 9, 7, 11, 12],
        "rhythm_beats": [1.0, 0.5, 0.35, 0.65, 0.5, 1.2],
        "velocities": [54, 64, 72, 66, 70, 58],
        "voicing_spread": 21,
        "contrary_ratio": 0.68,
        "tension_level": 0.62,
        "momentum": 0.48,
        "pedal_curve_summary": "syncopated_repedal_shift",
    },
    {
        "id": "grammar_quartal_spectral_spread",
        "title": "Mid-Clearance Quartal & Ninth Resonance Grammar",
        "lh_intervals": [0, 7, 19],
        "rh_intervals": [0, 5, 10, 14, 17, 12],
        "rhythm_beats": [0.85, 0.45, 0.75, 0.55, 0.90, 1.25],
        "velocities": [46, 56, 64, 60, 52, 44],
        "voicing_spread": 29,
        "contrary_ratio": 0.62,
        "tension_level": 0.52,
        "momentum": 0.42,
        "pedal_curve_summary": "una_corda_half_pedal",
    },
    {
        "id": "grammar_staccato_polyrhythm_exchange",
        "title": "Crisp Antiphonal Tenth Bass & Dotted Upper Exchange",
        "lh_intervals": [0, 10, 7, 12],
        "rh_intervals": [0, -3, 4, -5, 7, 2],
        "rhythm_beats": [0.30, 0.22, 0.48, 0.24, 0.52, 0.70],
        "velocities": [66, 78, 70, 84, 74, 64],
        "voicing_spread": 22,
        "contrary_ratio": 0.74,
        "tension_level": 0.68,
        "momentum": 0.78,
        "pedal_curve_summary": "short_release_una_corda",
    },
]


@dataclass
class CandidatePhrase:
    candidate_id: str  # "A", "B", "C", "D"
    label_ja: str
    strategy: str
    events: List[Dict[str, Any]]
    total_score: float
    subscores: Dict[str, float]
    subscores_pct: Dict[str, int]
    adopted: bool
    rejection_tag_ja: str
    symusic_note_count: int
    pitch_range_str: str = "--"
    sample_notes_str: str = "--"
    problem_note_indices: List[int] = field(default_factory=list)
    rejection_detail: Dict[str, Any] = field(default_factory=dict)
    audit_passed: bool = False
    pedal_cc64_curve: List[Dict[str, Any]] = field(default_factory=list)
    pedal_cc67_curve: List[Dict[str, Any]] = field(default_factory=list)
    masterwork_total: float = 88.0
    masterwork_pct: Dict[str, int] = field(default_factory=dict)
    lh_idiom: str = "arpeggio_root_5th_10th"
    blueprint_source: str = "rtx3070_transformer"


@dataclass
class CompositionTurnResult:
    turn_id: int
    intent: MusicalIntentSchema
    candidates: List[CandidatePhrase]
    adopted_candidate: CandidatePhrase
    corpus_source: str
    corpus_refs_used: List[Dict[str, Any]] = field(default_factory=list)


class ZeroConfigCorpusLoader:
    """
    Indexes classical MIDI corpora via `symusic` into universal acoustic & voicing vectors
    (LH/RH spacing, contrary motion, tension, momentum, pedal curve) without style-imitation labels.
    Falls back cleanly to `BUILTIN_CHAMBER_SEED_DICTIONARY` when directories are empty.
    """

    def __init__(self, search_dirs: Optional[List[Path]] = None, max_files: int = 36) -> None:
        self.search_dirs = (
            search_dirs
            if search_dirs is not None
            else [
                Path(r"D:\Disklavier-Data"),
                REPO_ROOT / "data" / "imslp_midi",
                REPO_ROOT / "data" / "human_references",
                REPO_ROOT / "data" / "maestro-v3.0.0",
                REPO_ROOT / "data" / "midi",
            ]
        )
        self.max_files = max_files
        self.patterns: List[Dict[str, Any]] = []
        self.feature_matrix: np.ndarray = np.zeros((0, 4), dtype=np.float32)
        self.source_mode: str = "builtin_seed_dictionary"
        self.loaded_file_count: int = 0
        self.load()

    def load(self) -> List[Dict[str, Any]]:
        midi_files: List[Path] = []
        per_dir_cap = max(6, self.max_files // max(1, len(self.search_dirs)))
        for d in self.search_dirs:
            if not (d.exists() and d.is_dir()):
                continue
            found_dir: List[Path] = []
            for ext in ("*.mid", "*.midi"):
                for p in d.rglob(ext):
                    found_dir.append(p)
                    if len(found_dir) >= per_dir_cap * 3:
                        break
                if len(found_dir) >= per_dir_cap * 3:
                    break
            found_dir.sort()
            if len(found_dir) > per_dir_cap:
                step = max(1, len(found_dir) // per_dir_cap)
                found_dir = found_dir[::step][:per_dir_cap]
            midi_files.extend(found_dir)
            if len(midi_files) >= self.max_files:
                break

        extracted: List[Dict[str, Any]] = []
        for mf in midi_files[: self.max_files]:
            try:
                sc = Score(str(mf))
                tpq = max(1, int(sc.ticks_per_quarter))
                all_notes: List[Tuple[int, int, int, int]] = []
                for tr in sc.tracks:
                    for n in tr.notes:
                        all_notes.append((int(n.time), int(n.pitch), int(n.duration), int(n.velocity)))
                all_notes.sort(key=lambda x: x[0])
                if len(all_notes) < 8:
                    continue

                slice_notes = all_notes[:24]
                lh_notes = [n for n in slice_notes if n[1] < 60]
                rh_notes = [n for n in slice_notes if n[1] >= 60]
                if not lh_notes:
                    lh_notes = slice_notes[:4]
                if not rh_notes:
                    rh_notes = slice_notes[-6:]

                lh_base = lh_notes[0][1]
                rh_base = rh_notes[0][1]
                lh_ivs = [int(n[1] - lh_base) for n in lh_notes[:6]]
                rh_ivs = [int(n[1] - rh_base) for n in rh_notes[:8]]
                spread = int(
                    round(
                        float(np.median([n[1] for n in rh_notes]))
                        - float(np.median([n[1] for n in lh_notes]))
                    )
                )
                spread = max(12, min(42, spread))

                beats = [max(0.20, min(1.8, round(n[2] / tpq, 2))) for n in rh_notes[:6]]
                vels = [max(24, min(102, n[3])) for n in rh_notes[:6]]
                avg_dur = float(np.mean(beats)) if beats else 0.5
                momentum = round(min(1.0, 0.35 / max(0.15, avg_dur)), 2)

                k_cmp = min(len(lh_notes), len(rh_notes))
                contrary = 0
                for i in range(1, k_cmp):
                    dl = lh_notes[i][1] - lh_notes[i - 1][1]
                    dr = rh_notes[i][1] - rh_notes[i - 1][1]
                    if dl * dr < 0:
                        contrary += 1
                contrary_ratio = round(contrary / max(1, k_cmp - 1), 2)

                rh_leaps = [abs(rh_ivs[i] - rh_ivs[i - 1]) for i in range(1, len(rh_ivs))]
                diss = sum(1 for iv in rh_leaps if iv % 12 in (1, 2, 6, 11))
                tension = round(min(1.0, 0.25 + 0.14 * diss), 2)

                extracted.append(
                    {
                        "id": f"corpus_{mf.stem[:24]}",
                        "title": f"Structural Voicing Grammar ({mf.parent.name}/{mf.stem[:20]})",
                        "lh_intervals": lh_ivs[:4],
                        "rh_intervals": rh_ivs[:6],
                        "intervals": rh_ivs[:6],
                        "rhythm_beats": beats,
                        "velocities": vels,
                        "voicing_spread": spread,
                        "contrary_ratio": contrary_ratio,
                        "tension_level": tension,
                        "momentum": momentum,
                        "pedal_curve_summary": "delayed_half_repedal",
                    }
                )
            except Exception:
                continue

        if extracted:
            self.patterns = extracted + BUILTIN_CHAMBER_SEED_DICTIONARY
            self.source_mode = f"symusic_midi_corpus({len(extracted)}_files)"
            self.loaded_file_count = len(extracted)
        else:
            self.patterns = list(BUILTIN_CHAMBER_SEED_DICTIONARY)
            self.source_mode = "builtin_seed_dictionary"
            self.loaded_file_count = 0

        feats = [
            [
                float(p.get("tension_level", 0.5)),
                float(p.get("momentum", 0.5)),
                float(p.get("voicing_spread", 22)) / 42.0,
                float(p.get("contrary_ratio", 0.6)),
            ]
            for p in self.patterns
        ]
        self.feature_matrix = np.asarray(feats, dtype=np.float32)
        return self.patterns

    def retrieve_voicing_references(
        self,
        tension: float,
        momentum: float,
        target_spread_norm: float = 0.62,
        contrary_pref: float = 0.72,
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        if not self.patterns or len(self.feature_matrix) == 0:
            return list(BUILTIN_CHAMBER_SEED_DICTIONARY[:top_k])

        query = np.array([tension, momentum, target_spread_norm, contrary_pref], dtype=np.float32)
        if _HAS_CUDA and torch is not None:
            try:
                mat_t = torch.tensor(self.feature_matrix, device="cuda")
                q_t = torch.tensor(query, device="cuda").unsqueeze(0)
                sims = torch.nn.functional.cosine_similarity(mat_t, q_t, dim=1)
                idx_top = torch.argsort(sims, descending=True)[:top_k].detach().cpu().tolist()
                return [self.patterns[i] for i in idx_top]
            except Exception:
                pass

        q_norm = np.linalg.norm(query) + 1e-8
        m_norms = np.linalg.norm(self.feature_matrix, axis=1) + 1e-8
        sims = (self.feature_matrix @ query) / (m_norms * q_norm)
        idx_top = np.argsort(sims)[::-1][:top_k]
        return [self.patterns[int(i)] for i in idx_top]


def _events_to_symusic_score(events: List[Dict[str, Any]], tpq: int = 480) -> Score:
    sc = Score(tpq)
    tr_rh = Track(name="Piano_RH")
    tr_lh = Track(name="Piano_LH")
    for ev in events:
        p = int(ev.get("pitch", ev.get("note", 0)) or 0)
        if p <= 0:
            continue
        t_tick = max(0, int(round(float(ev["onset_s"]) * tpq * 2)))
        d_tick = max(48, int(round(float(ev["duration_s"]) * tpq * 2)))
        vel = max(18, min(112, int(ev.get("velocity", 64))))
        hand = str(ev.get("hand", "")).lower()
        n_obj = Note(time=t_tick, duration=d_tick, pitch=p, velocity=vel)
        if hand == "lh" or p < 60:
            tr_lh.notes.append(n_obj)
        else:
            tr_rh.notes.append(n_obj)
    sc.tracks.append(tr_rh)
    sc.tracks.append(tr_lh)
    return sc


def _build_continuous_pedal_events(
    total_dur_s: float,
    damper_plan: List[int],
    una_corda_plan: List[int],
    strategy: str,
    act_number: int = 1,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    dur = max(1.4, float(total_dur_s))
    if strategy == "contrastive_flaw":
        return (
            [{"pitch": 0, "onset_s": 0.0, "control": 64, "value": 100, "voice": "pedal"}],
            [{"t": 0.0, "val": 100}],
            [{"t": 0.0, "val": 0}],
        )

    plan64 = [max(0, min(127, int(v))) for v in (damper_plan or [0, 74, 112, 8, 86, 0])]
    plan67 = [max(0, min(127, int(v))) for v in (una_corda_plan or [24, 76, 94, 48, 16])]

    if act_number == 4 or strategy == "overtone_clear_high_spread":
        # Deep Una Corda & luminous half-pedal across harmonic changes
        u_base = 76 if act_number == 4 else 32
        u_peak = 114 if act_number == 4 else 98
        cc64_pts = [
            (0.00, 0),
            (0.07, min(78, max(46, plan64[1] if len(plan64) > 1 else 64))),
            (round(dur * 0.44, 3), min(86, max(54, plan64[2] if len(plan64) > 2 else 76))),
            (round(dur * 0.50, 3), 0),     # crisp repedaling at barline
            (round(dur * 0.56, 3), 72),
            (round(dur * 0.94, 3), 0),
        ]
        cc67_pts = [
            (0.00, u_base),
            (round(dur * 0.25, 3), min(120, int(u_peak * 0.92))),
            (round(dur * 0.55, 3), u_peak),
            (round(dur * 0.82, 3), max(54, int(u_peak * 0.78))),
            (round(dur * 0.96, 3), 24 if act_number != 4 else 48),
        ]
    elif strategy == "suspension_inner_voice":
        cc64_pts = [
            (0.00, 0),
            (0.06, 86),
            (round(dur * 0.34, 3), 108),
            (round(dur * 0.42, 3), 4),     # repedal #1
            (round(dur * 0.49, 3), 94),
            (round(dur * 0.73, 3), 8),     # repedal #2
            (round(dur * 0.80, 3), 78),
            (round(dur * 0.95, 3), 0),
        ]
        cc67_pts = [
            (0.00, 20),
            (round(dur * 0.25, 3), 68),
            (round(dur * 0.52, 3), 42),
            (round(dur * 0.80, 3), 84),
            (round(dur * 0.96, 3), 22),
        ]
    else:
        p_mid = max(62, min(118, plan64[2] if len(plan64) > 2 else 104))
        cc64_pts = [
            (0.00, 0),
            (0.06, max(48, min(88, plan64[1] if len(plan64) > 1 else 68))),
            (round(dur * 0.38, 3), p_mid),
            (round(dur * 0.48, 3), 0),     # barline repedaling
            (round(dur * 0.55, 3), 88),
            (round(dur * 0.86, 3), 58),
            (round(dur * 0.96, 3), 0),
        ]
        u_peak = max(48, min(116, max(plan67)))
        cc67_pts = [
            (0.00, max(16, plan67[0] if plan67 else 24)),
            (round(dur * 0.25, 3), int(u_peak * 0.72)),
            (round(dur * 0.52, 3), int(u_peak * 0.38)),
            (round(dur * 0.78, 3), u_peak),
            (round(dur * 0.96, 3), 18),
        ]

    evs: List[Dict[str, Any]] = []
    curve64: List[Dict[str, Any]] = []
    curve67: List[Dict[str, Any]] = []
    for t, val in cc64_pts:
        evs.append({"pitch": 0, "onset_s": t, "control": 64, "value": int(val), "voice": "pedal"})
        curve64.append({"t": t, "val": int(val)})
    for t, val in cc67_pts:
        evs.append({"pitch": 0, "onset_s": t, "control": 67, "value": int(val), "voice": "pedal"})
        curve67.append({"t": t, "val": int(val)})
    return evs, curve64, curve67


class MultipleCandidateComposer:
    """
    Layer 3 Co-Composition Two-Hand Composer powered by RTX 3070 `CorpusPhraseModelManager`,
    `CoCompositionArchitect` (`MacroTurnPlan` / `ThematicDNA`), and `MusicalityCritic` + `MasterworkEvaluator`.
    """

    def __init__(
        self,
        corpus_loader: Optional[ZeroConfigCorpusLoader] = None,
        phrase_model: Optional[CorpusPhraseModelManager] = None,
    ) -> None:
        self.corpus_loader = corpus_loader or ZeroConfigCorpusLoader()
        self.phrase_model = phrase_model or CorpusPhraseModelManager()
        self.turn_counter: int = 0

    def _safe_pitch_outside_mud(
        self,
        raw_pitch: int,
        tonal_center: str,
        reg_lo: int,
        reg_hi: int,
        human_pitches: List[int],
    ) -> int:
        p = quantize_pitch_to_tonality(raw_pitch, tonal_center, reg_lo, reg_hi)

        def _clashes(cand: int) -> bool:
            if any(abs(cand - hp) <= 2 for hp in human_pitches):
                return True
            if 40 <= cand <= 66 and any(abs(cand - hp) <= 4 for hp in human_pitches):
                return True
            return False

        if _clashes(p):
            for shift in (-12, 12, -5, 5, 7, -7, 4, -3):
                cand = quantize_pitch_to_tonality(p + shift, tonal_center, max(24, reg_lo - 7), min(98, reg_hi + 7))
                if not _clashes(cand):
                    return cand
        return p

    @staticmethod
    def _degree_offset_to_pitch(anchor_pitch: int, deg_off: int, tonality: str) -> int:
        tonic_pc, intervals = SCALE_INTERVALS.get(tonality, (2, [0, 2, 3, 5, 7, 9, 10]))
        # Build ordered scale ladder in [48..96]
        scale_notes = [
            m for m in range(48, 97) if (m - tonic_pc) % 12 in intervals
        ]
        if not scale_notes:
            return max(60, min(90, anchor_pitch + deg_off))
        nearest_idx = min(range(len(scale_notes)), key=lambda i: abs(scale_notes[i] - anchor_pitch))
        tgt_idx = max(0, min(len(scale_notes) - 1, nearest_idx + int(deg_off)))
        return scale_notes[tgt_idx]

    @staticmethod
    def _apply_post_leap_gap_fill(melody: List[int], tonality: str) -> List[int]:
        """Enforces the classical Post-Leap Gap-Fill Law & prevents >=4 unidirectional steps or pitch repeats."""
        if len(melody) < 4:
            return melody
        out = list(melody)
        for i in range(1, len(out) - 1):
            leap = out[i] - out[i - 1]
            if abs(leap) >= 5:
                nxt_iv = out[i + 1] - out[i]
                # Must move in opposite direction by a step (1..3 semitones)
                if leap * nxt_iv >= 0 or abs(nxt_iv) > 4 or nxt_iv == 0:
                    step_dir = -2 if leap > 0 else 2
                    out[i + 1] = quantize_pitch_to_tonality(out[i] + step_dir, tonality, 61, 89)

        # Eliminate consecutive repeated melody notes (except purposeful syncopated suspension release)
        for i in range(1, len(out)):
            if out[i] == out[i - 1]:
                nudge = 2 if i % 2 == 0 else -2
                out[i] = quantize_pitch_to_tonality(out[i] + nudge, tonality, 61, 89)

        # Break any >=3 consecutive unidirectional stepwise intervals
        run_dir = 0
        run_len = 0
        for i in range(1, len(out)):
            d = out[i] - out[i - 1]
            cur_dir = 1 if d > 0 else (-1 if d < 0 else 0)
            if cur_dir != 0 and cur_dir == run_dir and abs(d) <= 2:
                run_len += 1
                if run_len >= 2:
                    out[i] = quantize_pitch_to_tonality(out[i - 1] - cur_dir * 4, tonality, 61, 89)
                    run_dir = -cur_dir
                    run_len = 0
            else:
                run_dir = cur_dir
                run_len = 1 if cur_dir != 0 else 0
        return out

    def _build_idiomatic_lh_events(
        self,
        lh_idiom: str,
        bar_onsets: List[float],
        bar_durs: List[float],
        bass_roots: List[int],
        tonality: str,
        human_pitches: List[int],
        base_vel: int,
        boost_inner: bool = False,
    ) -> List[Dict[str, Any]]:
        """Generates breathing, idiomatic classical LH patterns (10th arpeggio, pedal point, syncopation, choral recall)."""
        evs: List[Dict[str, Any]] = []
        inner_vel = min(74, base_vel - (2 if boost_inner else 8))

        for b_idx, t0 in enumerate(bar_onsets):
            b_dur = max(0.9, bar_durs[b_idx] if b_idx < len(bar_durs) else 1.4)
            root = self._safe_pitch_outside_mud(bass_roots[b_idx % len(bass_roots)], tonality, 28, 45, human_pitches)
            fifth = self._safe_pitch_outside_mud(root + 7, tonality, 34, 52, human_pitches)
            tenth = self._safe_pitch_outside_mud(root + 15, tonality, 40, 58, human_pitches)
            if tenth == fifth or abs(tenth - fifth) < 3:
                tenth = self._safe_pitch_outside_mud(root + 12, tonality, 38, 56, human_pitches)

            if lh_idiom == "arpeggio_root_5th_10th":
                # Breathing root -> fifth -> tenth -> step inner suspension
                pts = [
                    (t0 + 0.015, root, b_dur * 0.92, base_vel - 6, "lh_bass"),
                    (t0 + b_dur * 0.25, fifth, b_dur * 0.68, inner_vel, "lh_fifth"),
                    (t0 + b_dur * 0.50, tenth, b_dur * 0.45, inner_vel, "lh_tenth"),
                    (t0 + b_dur * 0.76, quantize_pitch_to_tonality(tenth - 2, tonality, 38, 58), b_dur * 0.24, inner_vel - 4, "lh_inner"),
                ]
            elif lh_idiom == "pedal_point_inner_voice":
                # Sustained deep pedal point + active tenor counterline
                anchor = self._safe_pitch_outside_mud(bass_roots[0], tonality, 28, 42, human_pitches)
                step1 = self._safe_pitch_outside_mud(anchor + 14 + (b_idx % 2), tonality, 40, 57, human_pitches)
                step2 = self._safe_pitch_outside_mud(step1 - 1, tonality, 39, 56, human_pitches)
                pts = [
                    (t0 + 0.015, anchor, b_dur * 0.95, base_vel - 4, "lh_bass"),
                    (t0 + 0.030, anchor + 7, b_dur * 0.85, inner_vel - 2, "lh_fifth"),
                    (t0 + b_dur * 0.34, step1, b_dur * 0.35, inner_vel + 2, "lh_inner"),
                    (t0 + b_dur * 0.68, step2, b_dur * 0.30, inner_vel, "lh_inner"),
                ]
            elif lh_idiom == "syncopated_offbeat_harmony":
                # Beat-1 bass + offbeat inner chord & suspension
                pts = [
                    (t0 + 0.015, root, b_dur * 0.90, base_vel - 5, "lh_bass"),
                    (t0 + b_dur * 0.28, fifth, b_dur * 0.40, inner_vel, "lh_fifth"),
                    (t0 + b_dur * 0.54, tenth, b_dur * 0.42, inner_vel + 2, "lh_inner"),
                    (t0 + b_dur * 0.78, quantize_pitch_to_tonality(fifth + 5, tonality, 38, 58), b_dur * 0.22, inner_vel - 2, "lh_inner"),
                ]
            else:  # una_corda_choral_recall
                pts = [
                    (t0 + 0.015, root, b_dur * 0.94, max(36, base_vel - 10), "lh_bass"),
                    (t0 + 0.065, fifth, b_dur * 0.88, max(34, inner_vel - 4), "lh_fifth"),
                    (t0 + b_dur * 0.42, tenth, b_dur * 0.52, max(34, inner_vel - 2), "lh_inner"),
                ]

            for on_s, p, d_s, vel, v_role in pts:
                if abs(on_s) < 0.001:
                    on_s = 0.016  # Melody Lead (+16ms) keeps RH top melody prominent
                evs.append(
                    {
                        "pitch": max(24, min(59, int(p))),
                        "onset_s": round(on_s, 3),
                        "duration_s": round(max(0.14, d_s), 3),
                        "velocity": max(28, min(92, int(vel))),
                        "hand": "lh",
                        "voice": v_role,
                    }
                )
        return evs

    def _build_polyphonic_candidate(
        self,
        strategy: str,
        human_notes: List[Dict[str, Any]],
        intent: MusicalIntentSchema,
        turn_idx: int,
        corpus_refs: List[Dict[str, Any]],
        macro_plan: Dict[str, Any],
        blueprint: PhraseBlueprint,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], str]:
        tonality = intent.tonal_center
        tonic_pc, _ = SCALE_INTERVALS.get(tonality, (2, [0, 2, 3, 5, 7, 9, 10]))
        human_pitches = [
            int(n.get("pitch", n.get("note", 54)))
            for n in human_notes
            if int(n.get("pitch", n.get("note", 0))) > 0
        ] or [50, 53, 57, 55]

        # Contrastive flawed draft for transparent stage rejection
        if strategy == "contrastive_flaw_check":
            evs: List[Dict[str, Any]] = []
            t = 0.0
            if turn_idx % 2 == 1:
                cur = 60 + tonic_pc
                for i in range(6):
                    cur = quantize_pitch_to_tonality(cur + 2, tonality, 58, 84)
                    if i > 0 and cur <= evs[-1]["pitch"]:
                        cur = evs[-1]["pitch"] + 2
                    evs.append(
                        {
                            "pitch": cur,
                            "onset_s": round(t, 3),
                            "duration_s": 0.24,
                            "velocity": 70,
                            "hand": "rh",
                            "voice": "scale_flaw",
                        }
                    )
                    t += 0.25
            else:
                primary_human = human_pitches[-1]
                clash_p = max(44, min(65, primary_human + 1))
                for i in range(5):
                    p = clash_p if i in (0, 2) else max(45, min(64, primary_human))
                    for iv in (0, 3, 7):
                        evs.append(
                            {
                                "pitch": p + iv,
                                "onset_s": round(t, 3),
                                "duration_s": 0.24,
                                "velocity": 76,
                                "hand": "lh" if (p + iv) < 60 else "rh",
                                "voice": "mud_flaw",
                            }
                        )
                    t += 0.26
            ped_evs, c64, c67 = _build_continuous_pedal_events(t + 0.2, [100], [0], "contrastive_flaw", 1)
            return evs + ped_evs, c64, c67, "contrastive_flaw"

        act_num = int(macro_plan.get("act_number", 1))
        phrase_func = str(macro_plan.get("phrase_function", "question_half_cadence"))
        bridge_pitch = int(macro_plan.get("voice_leading_bridge_pitch", 69))
        target_apex = int(macro_plan.get("target_apex_pitch", 81))
        seed_pitches = list(macro_plan.get("seed_motif_pitches") or human_pitches)
        seed_ivs = list(macro_plan.get("seed_motif_intervals") or [3, 4, -2, -3])
        boost_inner = bool((macro_plan.get("directive_override_flags") or {}).get("boost_lh_inner", False))

        dna = ThematicDNA(
            seed_pitches=seed_pitches,
            seed_intervals=seed_ivs,
            home_tonality=tonality,
        )

        # Construct 8-note cantabile melody combining ThematicDNA transformation + RTX 3070 Transformer Blueprint
        if strategy == "contrary_open_polyphony":
            t_mode = str(macro_plan.get("motivic_transform") or "inversion_question")
            dna_mel = dna.transform_motif(t_mode, bridge_pitch, tonality, target_len=8)
            bp_mel = [self._degree_offset_to_pitch(bridge_pitch, off, tonality) for off in blueprint.melody_degree_offsets]
            raw_mel = [
                dna_mel[0],
                dna_mel[1],
                bp_mel[2],
                dna_mel[3],
                bp_mel[4],
                max(dna_mel[5], target_apex) if act_num == 3 else dna_mel[5],
                bp_mel[6],
                dna_mel[7],
            ]
            lh_idiom = blueprint.lh_idiom if not boost_inner else "arpeggio_root_5th_10th"
        elif strategy == "suspension_inner_voice":
            dna_mel = dna.transform_motif("sequence_up" if act_num != 4 else "coda_recall", bridge_pitch, tonality, target_len=8)
            # Inject explicit 4-3 / 9-8 suspension figure across steps 3->4 and 6->7
            susp_peak = quantize_pitch_to_tonality(dna_mel[2] + 5, tonality, 65, 86)
            raw_mel = [
                dna_mel[0],
                dna_mel[1],
                susp_peak,
                quantize_pitch_to_tonality(susp_peak - 2, tonality, 63, 84),
                quantize_pitch_to_tonality(dna_mel[4] + 3, tonality, 65, 88),
                quantize_pitch_to_tonality(target_apex, tonality, 70, 90),
                quantize_pitch_to_tonality(target_apex - 2, tonality, 66, 86),
                dna_mel[7],
            ]
            lh_idiom = "pedal_point_inner_voice" if act_num in (2, 3) else "syncopated_offbeat_harmony"
        else:  # "overtone_clear_high_spread"
            dna_mel = dna.transform_motif("coda_recall" if act_num == 4 else "polyphonic_stretto", min(82, bridge_pitch + 4), tonality, target_len=8)
            raw_mel = [
                quantize_pitch_to_tonality(bridge_pitch + (2 if act_num != 4 else 0), tonality, 64, 84),
                dna_mel[1],
                quantize_pitch_to_tonality(dna_mel[1] + 5, tonality, 67, 88),
                quantize_pitch_to_tonality(dna_mel[1] + 3, tonality, 65, 86),
                quantize_pitch_to_tonality(target_apex, tonality, 72, 91),
                quantize_pitch_to_tonality(target_apex - 3, tonality, 68, 88),
                dna_mel[6],
                dna_mel[7],
            ]
            lh_idiom = "una_corda_choral_recall" if act_num == 4 else "arpeggio_root_5th_10th"

        # Enforce Act cadence ending (Question half-cadence vs Tonic resolution) & clear human fundamentals
        mel = self._apply_post_leap_gap_fill(raw_mel, tonality)
        mel = [self._safe_pitch_outside_mud(p, tonality, 61, 90, human_pitches) for p in mel]

        if phrase_func == "question_half_cadence":
            # End on 5th (dom) or 2nd degree for open-ended questioning feel
            q_target = quantize_pitch_to_tonality(72 + tonic_pc + 7, tonality, 65, 83)
            if abs(q_target - mel[-2]) > 7:
                q_target = quantize_pitch_to_tonality(60 + tonic_pc + 7, tonality, 65, 80)
            mel[-1] = self._safe_pitch_outside_mud(q_target, tonality, 63, 84, human_pitches)
            if mel[-1] == mel[-2]:
                mel[-1] = self._safe_pitch_outside_mud(72 + tonic_pc + 2, tonality, 63, 84, human_pitches)
        elif phrase_func == "recapitulation_resolution":
            t_target = quantize_pitch_to_tonality(60 + tonic_pc + 12, tonality, 64, 80)
            if abs(t_target - mel[-2]) > 7:
                t_target = quantize_pitch_to_tonality(60 + tonic_pc, tonality, 62, 78)
            mel[-1] = self._safe_pitch_outside_mud(t_target, tonality, 62, 82, human_pitches)
            if mel[-1] == mel[-2]:
                mel[-2] = self._safe_pitch_outside_mud(mel[-1] + 2, tonality, 63, 84, human_pitches)

        # Guarantee single clear melodic apex in steps 3..6
        apex_idx = 5 if act_num >= 2 else 4
        other_max = max(mel[i] for i in range(len(mel)) if i != apex_idx)
        if mel[apex_idx] <= other_max:
            mel[apex_idx] = self._safe_pitch_outside_mud(min(90, other_max + 2), tonality, 68, 90, human_pitches)

        # Build 2-bar agogic timing (approx 3.1 - 3.8s total) using learned blueprint rubato + velocity arch
        base_beats = [0.46, 0.28, 0.36, 0.52, 0.34, 0.56, 0.32, 0.72]
        ag = blueprint.agogic_ratios or [1.0] * 8
        step_dts = [round(max(0.20, min(0.88, base_beats[i] * float(ag[i % len(ag)]))), 3) for i in range(8)]

        act_vel_boost = 12 if act_num == 3 else (-8 if act_num == 4 else 0)
        bp_vel = blueprint.velocity_curve or [0.64] * 8
        vel_seq = [
            max(42, min(98, int(round(36 + float(bp_vel[i % len(bp_vel)]) * 54 + act_vel_boost + (8 if i == apex_idx else 0)))))
            for i in range(8)
        ]

        events: List[Dict[str, Any]] = []
        t = 0.0
        bar1_start = 0.0
        bar2_start = 0.0

        for i in range(8):
            if i == 4:
                t += 0.07  # phrase breath between Bar 1 and Bar 2
                bar2_start = t

            dt = step_dts[i]
            cur_rh = mel[i]
            vel_rh = vel_seq[i]
            dur_rh = round(max(0.18, dt * (0.76 if intent.pedal_strategy == "dry" else 1.06)), 3)
            if i == 7:
                dur_rh = round(max(0.55, dur_rh * 1.25), 3)

            events.append(
                {
                    "pitch": cur_rh,
                    "onset_s": round(t, 3),
                    "duration_s": dur_rh,
                    "velocity": vel_rh,
                    "hand": "rh",
                    "voice": "rh_melody",
                }
            )

            # Vertical RH harmony dyads/chords on strong harmonic pillars (steps 0, 2, 4, 5, 7)
            if i in (0, 2, 4, 5, 7):
                harm_iv = 4 if i in (0, 5) else (5 if i == 4 else 3)
                harm_p = self._safe_pitch_outside_mud(cur_rh - harm_iv, tonality, 60, cur_rh - 3, human_pitches)
                if harm_p != cur_rh and abs(cur_rh - harm_p) >= 3:
                    events.append(
                        {
                            "pitch": harm_p,
                            "onset_s": round(t + 0.016, 3),  # +16ms Melody Lead offset
                            "duration_s": round(dur_rh * 0.90, 3),
                            "velocity": max(34, vel_rh - 14),
                            "hand": "rh",
                            "voice": "rh_harmony",
                        }
                    )
                # In Act 3 climax, add a rich 3rd chordal voice (6th/7th below soprano)
                if act_num == 3 and i in (0, 5):
                    harm_low = self._safe_pitch_outside_mud(cur_rh - 9, tonality, 58, harm_p - 3, human_pitches)
                    if harm_low != harm_p and abs(harm_p - harm_low) >= 3:
                        events.append(
                            {
                                "pitch": harm_low,
                                "onset_s": round(t + 0.028, 3),
                                "duration_s": round(dur_rh * 0.88, 3),
                                "velocity": max(34, vel_rh - 18),
                                "hand": "rh",
                                "voice": "rh_harmony",
                            }
                        )

            t += dt + (0.04 if i == 6 else 0.0)

        bar1_dur = max(1.1, bar2_start - bar1_start - 0.05)
        bar2_dur = max(1.1, t - bar2_start)

        # Bass roots matching Act harmonic progression
        bass_root1 = 36 + tonic_pc
        while bass_root1 > 44:
            bass_root1 -= 12
        if phrase_func == "question_half_cadence":
            bass_root2 = bass_root1 + 7  # Move to V for open question
        elif phrase_func == "recapitulation_resolution":
            bass_root2 = bass_root1      # Resolve back to I
            bass_root1 = bass_root1 + 7  # From V in bar 1 -> I in bar 2
        elif act_num == 3:
            bass_root2 = bass_root1      # Pedal point anchor
        else:
            bass_root2 = bass_root1 + 5  # IV / subdominant motion

        lh_evs = self._build_idiomatic_lh_events(
            lh_idiom=lh_idiom,
            bar_onsets=[bar1_start, bar2_start],
            bar_durs=[bar1_dur, bar2_dur],
            bass_roots=[bass_root1, bass_root2],
            tonality=tonality,
            human_pitches=human_pitches,
            base_vel=max(46, min(82, int(np.mean(vel_seq)) - 8)),
            boost_inner=boost_inner,
        )
        events.extend(lh_evs)

        ped_evs, c64, c67 = _build_continuous_pedal_events(
            total_dur_s=t + 0.35,
            damper_plan=intent.damper_cc64_plan,
            una_corda_plan=intent.una_corda_cc67_plan,
            strategy=strategy,
            act_number=act_num,
        )
        return events + ped_evs, c64, c67, lh_idiom

    def _repair_to_polyphonic_standard(
        self,
        events: List[Dict[str, Any]],
        human_notes: List[Dict[str, Any]],
        intent: MusicalIntentSchema,
        audit: MusicalityAuditReport,
        macro_plan: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        tonality = intent.tonal_center
        tonic_pc, _ = SCALE_INTERVALS.get(tonality, (2, [0, 2, 3, 5, 7, 9, 10]))
        human_pitches = [
            int(n.get("pitch", n.get("note", 54)))
            for n in human_notes
            if int(n.get("pitch", n.get("note", 0))) > 0
        ] or [50]

        note_evs = [dict(e) for e in events if int(e.get("pitch", 0) or 0) > 0]
        if "monotonic_scale_run" in audit.failures or "mechanical_arpeggio_loop" in audit.failures:
            rh_notes = [n for n in note_evs if str(n.get("hand", "")).lower() == "rh" or int(n["pitch"]) >= 60]
            for idx, n in enumerate(rh_notes):
                if idx % 2 == 1:
                    n["pitch"] = self._safe_pitch_outside_mud(
                        int(n["pitch"]) + (5 if idx % 4 == 1 else -4),
                        tonality,
                        62,
                        92,
                        human_pitches,
                    )

        if "minor2_spectral_mud" in audit.failures:
            for n in note_evs:
                p = int(n["pitch"])
                if any(abs(p - hp) <= 4 for hp in human_pitches):
                    if str(n.get("hand", "")).lower() == "lh" or p < 60:
                        n["pitch"] = self._safe_pitch_outside_mud(p - 12, tonality, 26, 46, human_pitches)
                    else:
                        n["pitch"] = self._safe_pitch_outside_mud(p + 12, tonality, 65, 92, human_pitches)

        if "voice_bridge_disconnect" in audit.failures and macro_plan:
            bridge_p = int(macro_plan.get("voice_leading_bridge_pitch", 69))
            rh_sorted = sorted(
                [n for n in note_evs if str(n.get("hand", "")).lower() == "rh" or int(n["pitch"]) >= 60],
                key=lambda x: float(x.get("onset_s", 0.0)),
            )
            if rh_sorted:
                rh_sorted[0]["pitch"] = self._safe_pitch_outside_mud(bridge_p, tonality, 62, 86, human_pitches)

        if "parallel_fifth_octave" in audit.failures or "excessive_pitch_repetition" in audit.failures:
            mel_notes = sorted(
                [
                    n
                    for n in note_evs
                    if str(n.get("voice", "")).lower() in ("rh_melody", "melody")
                    or (str(n.get("hand", "")).lower() == "rh" and str(n.get("voice", "")).lower() != "rh_harmony")
                ],
                key=lambda x: float(x.get("onset_s", 0.0)),
            )
            for idx in range(1, len(mel_notes)):
                prev_p = int(mel_notes[idx - 1]["pitch"])
                cur_p = int(mel_notes[idx]["pitch"])
                if cur_p == prev_p:
                    mel_notes[idx]["pitch"] = self._safe_pitch_outside_mud(
                        cur_p + (3 if idx % 2 == 0 else -2), tonality, 62, 89, human_pitches
                    )
                elif idx < len(human_pitches):
                    iv_ai = cur_p - prev_p
                    iv_hu = human_pitches[idx] - human_pitches[idx - 1]
                    if iv_ai != 0 and iv_ai == iv_hu and abs(cur_p - human_pitches[idx]) % 12 in (0, 7):
                        contrary_shift = -3 if iv_hu > 0 else 4
                        mel_notes[idx]["pitch"] = self._safe_pitch_outside_mud(
                            prev_p + contrary_shift, tonality, 62, 89, human_pitches
                        )

        has_lh = any(str(n.get("hand", "")).lower() == "lh" or int(n["pitch"]) < 60 for n in note_evs)
        if not has_lh:
            bass_p = quantize_pitch_to_tonality(36 + tonic_pc, tonality, 30, 48)
            note_evs.append({"pitch": bass_p, "onset_s": 0.016, "duration_s": 1.1, "velocity": 58, "hand": "lh", "voice": "lh_bass"})
            note_evs.append({"pitch": bass_p + 7, "onset_s": 0.35, "duration_s": 0.9, "velocity": 50, "hand": "lh", "voice": "lh_fifth"})

        max_end = max((float(n.get("onset_s", 0.0)) + float(n.get("duration_s", 0.35)) for n in note_evs), default=2.8)
        ped_evs, c64, c67 = _build_continuous_pedal_events(
            max_end + 0.35, intent.damper_cc64_plan, intent.una_corda_cc67_plan, "contrary_open_polyphony",
            act_number=int((macro_plan or {}).get("act_number", 1)),
        )
        return note_evs + ped_evs, c64, c67

    def compose_turn(
        self,
        human_notes: List[Dict[str, Any]],
        intent: MusicalIntentSchema,
        human_mode: str = "cello",
        macro_plan: Optional[Any] = None,
    ) -> CompositionTurnResult:
        self.turn_counter += 1
        human_pitches = [
            int(n.get("pitch", n.get("note", 54)))
            for n in human_notes
            if int(n.get("pitch", n.get("note", 0))) > 0
        ] or [50, 53, 57]
        leaps = [abs(human_pitches[i] - human_pitches[i - 1]) for i in range(1, len(human_pitches))]
        tension = min(1.0, 0.30 + 0.12 * sum(1 for x in leaps if x in (1, 2, 6)))
        corpus_refs = self.corpus_loader.retrieve_voicing_references(
            tension=tension,
            momentum=intent.target_density,
            top_k=3,
        )

        if macro_plan is None:
            seed_ivs = [human_pitches[i] - human_pitches[i - 1] for i in range(1, len(human_pitches))] or [3, 4, -2]
            macro_dict: Dict[str, Any] = {
                "act_number": intent.act_number,
                "phrase_function": intent.phrase_function,
                "motivic_transform": intent.motivic_transform,
                "seed_motif_pitches": list(human_pitches),
                "seed_motif_intervals": seed_ivs,
                "voice_leading_bridge_pitch": max(62, min(80, human_pitches[-1] + 12)),
                "target_apex_pitch": 79 if intent.act_number <= 2 else 85,
                "directive_override_flags": {},
            }
        elif isinstance(macro_plan, dict):
            macro_dict = dict(macro_plan)
        else:
            macro_dict = {
                "act_number": getattr(macro_plan, "act_number", 1),
                "phrase_function": getattr(macro_plan, "phrase_function", "question_half_cadence"),
                "motivic_transform": getattr(macro_plan, "motivic_transform", "inversion_question"),
                "seed_motif_pitches": list(getattr(macro_plan, "seed_motif_pitches", human_pitches)),
                "seed_motif_intervals": list(getattr(macro_plan, "seed_motif_intervals", [3, 4, -2])),
                "voice_leading_bridge_pitch": getattr(macro_plan, "voice_leading_bridge_pitch", 69),
                "target_apex_pitch": getattr(macro_plan, "target_apex_pitch", 79),
                "directive_override_flags": dict(getattr(macro_plan, "directive_override_flags", {}) or {}),
            }

        tonic_pc, valid_pcs = SCALE_INTERVALS.get(intent.tonal_center, (2, [0, 2, 3, 5, 7, 9, 10]))
        blueprint = self.phrase_model.predict_blueprint(
            act_number=int(macro_dict["act_number"]),
            phrase_function=str(macro_dict["phrase_function"]),
            seed_intervals=list(macro_dict["seed_motif_intervals"]),
            prev_end_pitch=int(macro_dict["voice_leading_bridge_pitch"]),
            anchor_pitch=int(macro_dict["voice_leading_bridge_pitch"]),
            tonic_pc=tonic_pc,
            tension=tension,
            momentum=intent.target_density,
            articulation="legato" if intent.pedal_strategy != "dry" else "staccato",
            mode=human_mode,
        )

        act_label_short = {
            1: "主題問いかけ・開離10度案",
            2: "模進発展・内声対位法案",
            3: "保続低音・重層頂点統合案",
            4: "ウナ・コルダ回想・終止案",
        }.get(int(macro_dict["act_number"]), "対位法反行・両手開離案")

        specs = [
            ("A", f"{act_label_short} (Transformer)", "contrary_open_polyphony"),
            ("B", "掛留解決・保続音テノール対旋律案", "suspension_inner_voice"),
            ("C", "高域透明・ウナ・コルダ色彩ボイシング案", "overtone_clear_high_spread"),
            ("D", "対照検証案（音階単調・低中域混濁）", "contrastive_flaw_check"),
        ]

        candidates: List[CandidatePhrase] = []
        for idx_spec, (cid, label_ja, strat) in enumerate(specs):
            evs, c64, c67, lh_idiom = self._build_polyphonic_candidate(
                strat,
                human_notes,
                intent,
                self.turn_counter,
                corpus_refs[idx_spec % len(corpus_refs) :],
                macro_dict,
                blueprint,
            )
            audit = MusicalityCritic.audit_candidate(
                evs,
                human_notes=human_notes,
                tonal_center=intent.tonal_center,
                scale_pcs=(tonic_pc, valid_pcs),
                register_range=intent.register_range,
                human_mode=human_mode,
                macro_plan=macro_dict,
            )

            if cid != "D" and not audit.passed:
                evs, c64, c67 = self._repair_to_polyphonic_standard(evs, human_notes, intent, audit, macro_plan=macro_dict)
                audit = MusicalityCritic.audit_candidate(
                    evs,
                    human_notes=human_notes,
                    tonal_center=intent.tonal_center,
                    scale_pcs=(tonic_pc, valid_pcs),
                    register_range=intent.register_range,
                    human_mode=human_mode,
                    macro_plan=macro_dict,
                )

            sc = _events_to_symusic_score(evs)
            sym_count = sum(len(tr.notes) for tr in sc.tracks)
            pitches = sorted({int(e["pitch"]) for e in evs if int(e.get("pitch", 0)) > 0})
            pitch_range_str = (
                f"{midi_to_note_name(pitches[0])}-{midi_to_note_name(pitches[-1])}" if pitches else "--"
            )
            seq_pitches = [int(e["pitch"]) for e in evs if int(e.get("pitch", 0)) > 0][:4]
            sample_notes_str = "-".join(midi_to_note_name(p) for p in seq_pitches) or "--"

            candidates.append(
                CandidatePhrase(
                    candidate_id=cid,
                    label_ja=label_ja,
                    strategy=strat,
                    events=evs,
                    total_score=audit.total_score,
                    subscores=audit.subscores,
                    subscores_pct=audit.subscores_pct,
                    adopted=False,
                    rejection_tag_ja=audit.rejection_tag_ja,
                    symusic_note_count=sym_count,
                    pitch_range_str=pitch_range_str,
                    sample_notes_str=sample_notes_str,
                    problem_note_indices=audit.problem_note_indices,
                    rejection_detail=audit.rejection_detail,
                    audit_passed=audit.passed,
                    pedal_cc64_curve=c64,
                    pedal_cc67_curve=c67,
                    masterwork_total=audit.masterwork_total,
                    masterwork_pct=audit.masterwork_pct,
                    lh_idiom=lh_idiom,
                    blueprint_source=blueprint.source,
                )
            )

        eligible = [c for c in candidates if c.candidate_id != "D"]
        best = max(eligible or candidates, key=lambda c: (1 if c.audit_passed else 0, c.total_score))
        best.adopted = True
        best.rejection_tag_ja = ""
        best.problem_note_indices = []
        best.rejection_detail = {}

        return CompositionTurnResult(
            turn_id=self.turn_counter,
            intent=intent,
            candidates=candidates,
            adopted_candidate=best,
            corpus_source=f"{self.corpus_loader.source_mode} + {blueprint.source}",
            corpus_refs_used=corpus_refs,
        )
