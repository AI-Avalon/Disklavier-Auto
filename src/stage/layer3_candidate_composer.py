r"""
Layer 3: Polyphonic Two-Hand Composition, Classical Corpus Grammar Indexing,
MusicalityCritic 6-Axis Auditing & Dual-Pedal Curve Generation.

Core Architecture:
- Scans classical MIDI corpora (`D:\Disklavier-Data`, `data/imslp_midi`, `data/human_references`,
  `data/maestro-v3.0.0`, `data/midi`) via `symusic`, indexing universal acoustic/voicing laws
  (LH/RH spacing, contrary motion ratio, suspension patterns, agogic rhythm contrast, pedal curves)
  rather than composer imitation labels.
- Uses CUDA (RTX 3070) or NumPy vectorized cosine similarity to retrieve top voicing references
  matching current tension, momentum, and overtone clearance constraints.
- Generates 4 distinct two-hand polyphonic candidates (Candidate A/B/C/D) using `symusic` +
  LLM autonomous compositional intent + corpus voicings, with continuous Damper (CC#64) &
  Una Corda (CC#67) envelopes.
- Audits all 4 candidates with `MusicalityCritic` (6 pillars), automatically repairing any
  accidental scale run or monophonic artifact in A/B/C before selecting the winner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from symusic import Note, Score, Track

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

SCALE_INTERVALS: Dict[str, Tuple[int, List[int]]] = {
    "D_dorian": (2, [0, 2, 3, 5, 7, 9, 10]),
    "D_minor": (2, [0, 2, 3, 5, 7, 8, 10]),
    "G_minor": (7, [0, 2, 3, 5, 7, 8, 10]),
    "C_major": (0, [0, 2, 4, 5, 7, 9, 11]),
    "F_major": (5, [0, 2, 4, 5, 7, 9, 11]),
    "A_minor": (9, [0, 2, 3, 5, 7, 8, 10]),
}

# Universal polyphonic voicing & voice-leading seed grammar (no composer-imitation presets)
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

                # Focus on an expressive phrase excerpt
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

                # Measure contrary motion between LH and RH contours
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


def _quantize_to_scale(pitch: int, tonal_center: str, min_midi: int, max_midi: int) -> int:
    tonic_pc, intervals = SCALE_INTERVALS.get(tonal_center, (2, [0, 2, 3, 5, 7, 9, 10]))
    best_p = max(min_midi, min(max_midi, int(pitch)))
    best_dist = 999
    for candidate in range(max(21, best_p - 5), min(108, best_p + 6)):
        if candidate < min_midi or candidate > max_midi:
            continue
        pc = (candidate - tonic_pc) % 12
        if pc in intervals:
            d = abs(candidate - pitch)
            if d < best_dist:
                best_dist = d
                best_p = candidate
    return best_p


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
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Generates continuous time-series CC#64 (Damper) and CC#67 (Una Corda) curves:
    - Delayed depression after attack, half-pedal clarity (35..80), sharp chord-change
      repedaling dip (110 -> 0 -> 92), and clean end release to 0.
    - Continuous Una Corda lateral hammer shift curve matching dynamic contour.
    """
    dur = max(1.2, float(total_dur_s))
    cc64_pts: List[Tuple[float, int]]
    cc67_pts: List[Tuple[float, int]]

    if strategy == "contrastive_flaw":
        # Deliberately poor/missing dual pedal for Candidate D
        return (
            [{"pitch": 0, "onset_s": 0.0, "control": 64, "value": 100, "voice": "pedal"}],
            [{"t": 0.0, "val": 100}],
            [{"t": 0.0, "val": 0}],
        )

    plan64 = [max(0, min(127, int(v))) for v in (damper_plan or [0, 74, 112, 8, 86, 0])]
    plan67 = [max(0, min(127, int(v))) for v in (una_corda_plan or [24, 76, 94, 48, 16])]

    if strategy == "overtone_clear_high_spread":
        # Deeper Una Corda veil + floating half-pedal with repedal
        cc64_pts = [
            (0.00, 0),
            (0.08, min(78, max(44, plan64[1] if len(plan64) > 1 else 62))),
            (round(dur * 0.44, 3), min(82, max(48, plan64[2] if len(plan64) > 2 else 74))),
            (round(dur * 0.50, 3), 0),     # crisp repedaling dip
            (round(dur * 0.57, 3), 68),    # half-pedal re-entry
            (round(dur * 0.94, 3), 0),
        ]
        cc67_pts = [
            (0.00, 32),
            (round(dur * 0.22, 3), 96),
            (round(dur * 0.55, 3), 112),
            (round(dur * 0.80, 3), 72),
            (round(dur * 0.96, 3), 18),
        ]
    elif strategy == "suspension_inner_voice":
        # Syncopated damper repedaling across two harmonic pillars + moderate Una Corda
        cc64_pts = [
            (0.00, 0),
            (0.07, 84),
            (round(dur * 0.34, 3), 108),
            (round(dur * 0.42, 3), 4),     # repedal #1
            (round(dur * 0.49, 3), 92),
            (round(dur * 0.72, 3), 10),    # repedal #2
            (round(dur * 0.79, 3), 76),
            (round(dur * 0.95, 3), 0),
        ]
        cc67_pts = [
            (0.00, 16),
            (round(dur * 0.25, 3), 64),
            (round(dur * 0.55, 3), 42),
            (round(dur * 0.82, 3), 84),
            (round(dur * 0.96, 3), 20),
        ]
    else:
        # "contrary_open_polyphony": delayed catch, dynamic half-to-full arch, repedal & clean tail
        p_mid = max(55, min(116, plan64[2] if len(plan64) > 2 else 104))
        cc64_pts = [
            (0.00, 0),
            (0.07, max(48, min(88, plan64[1] if len(plan64) > 1 else 68))),
            (round(dur * 0.38, 3), p_mid),
            (round(dur * 0.48, 3), 0),     # clean harmonic-change repedaling
            (round(dur * 0.56, 3), 86),
            (round(dur * 0.88, 3), 52),
            (round(dur * 0.96, 3), 0),
        ]
        u_peak = max(48, min(115, max(plan67)))
        cc67_pts = [
            (0.00, max(12, plan67[0] if plan67 else 20)),
            (round(dur * 0.25, 3), int(u_peak * 0.68)),
            (round(dur * 0.52, 3), int(u_peak * 0.35)),  # open brightness at harmonic peak
            (round(dur * 0.76, 3), u_peak),              # veil for lyrical tail
            (round(dur * 0.96, 3), 14),
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
    Layer 3 Two-Hand Polyphonic Composer & MusicalityCritic Auditor.
    """

    def __init__(self, corpus_loader: Optional[ZeroConfigCorpusLoader] = None) -> None:
        self.corpus_loader = corpus_loader or ZeroConfigCorpusLoader()
        self.turn_counter: int = 0

    def _safe_pitch_outside_mud(
        self,
        raw_pitch: int,
        tonal_center: str,
        reg_lo: int,
        reg_hi: int,
        human_pitches: List[int],
    ) -> int:
        p = _quantize_to_scale(raw_pitch, tonal_center, reg_lo, reg_hi)
        def _clashes(cand: int) -> bool:
            if any(abs(cand - hp) <= 2 for hp in human_pitches):
                return True
            if 40 <= cand <= 66 and any(abs(cand - hp) <= 4 for hp in human_pitches):
                return True
            return False

        if _clashes(p):
            for shift in (-12, 12, -5, 5, 7, -7, 4, -3):
                cand = _quantize_to_scale(p + shift, tonal_center, max(24, reg_lo - 7), min(98, reg_hi + 7))
                if not _clashes(cand):
                    return cand
        return p

    def _build_polyphonic_candidate(
        self,
        strategy: str,
        human_notes: List[Dict[str, Any]],
        intent: MusicalIntentSchema,
        turn_idx: int,
        corpus_refs: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        tonic_pc, _ = SCALE_INTERVALS.get(intent.tonal_center, (2, [0, 2, 3, 5, 7, 9, 10]))
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
                # Flaw Type 1: Monotonous 5-note single-direction scale run, monophonic, flat rhythm
                start_p = 60 + tonic_pc
                cur = start_p
                for i in range(6):
                    cur = _quantize_to_scale(cur + 2, intent.tonal_center, 58, 84)
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
                # Flaw Type 2: Minor-2nd & thick triad mud inside human 100-600Hz fundamental band
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
            ped_evs, c64, c67 = _build_continuous_pedal_events(t + 0.2, [100], [0], "contrastive_flaw")
            return evs + ped_evs, c64, c67

        # Genuine two-hand polyphonic strategies
        ref = corpus_refs[0] if corpus_refs else BUILTIN_CHAMBER_SEED_DICTIONARY[turn_idx % len(BUILTIN_CHAMBER_SEED_DICTIONARY)]
        lh_base = max(36, min(46, 36 + tonic_pc))
        if abs(lh_base - human_pitches[-1]) <= 4:
            lh_base = max(33, lh_base - 12)

        rh_anchor = 69 + tonic_pc if max(human_pitches) >= 57 else 65 + tonic_pc
        if strategy == "overtone_clear_high_spread":
            rh_anchor = min(79, rh_anchor + 5)
            lh_base = max(34, lh_base - 2)

        human_diffs = [human_pitches[i] - human_pitches[i - 1] for i in range(1, len(human_pitches))] or [3, -2, 4, -3]
        ref_rh_ivs = list(ref.get("rh_intervals") or [0, 5, 12, 9, 7, 14])
        ref_lh_ivs = list(ref.get("lh_intervals") or [0, 7, 12, 16])

        # Diverse rhythmic durations (never uniform!) and agogic breathing gaps
        if strategy == "contrary_open_polyphony":
            rhythm_steps = [0.36, 0.22, 0.44, 0.24, 0.34, 0.56]
            vel_arch = [62, 72, 82, 76, 68, 56]
            rh_contour_offsets = [-human_diffs[0], -human_diffs[1 % len(human_diffs)] + 4, -5, 7, -3, 5]
        elif strategy == "suspension_inner_voice":
            rhythm_steps = [0.46, 0.24, 0.26, 0.48, 0.28, 0.58]
            vel_arch = [58, 70, 78, 66, 74, 54]
            # Explicit suspension-resolution shape (+5 -> +4, +9 -> +7)
            rh_contour_offsets = [0, 5, -1, 6, -2, 4]
        else:
            # "overtone_clear_high_spread"
            rhythm_steps = [0.48, 0.28, 0.38, 0.22, 0.42, 0.62]
            vel_arch = [48, 60, 70, 64, 54, 44]
            rh_contour_offsets = [0, 7, -3, 9, -5, 4]

        events: List[Dict[str, Any]] = []
        t = 0.0
        cur_rh = _quantize_to_scale(rh_anchor, intent.tonal_center, 62, 84)

        for i in range(6):
            step_dt = rhythm_steps[i % len(rhythm_steps)]
            # Non-monotonic melodic jump + turn to guarantee zero scale runs
            if strategy == "suspension_inner_voice":
                raw_rh = rh_anchor + (ref_rh_ivs[i % len(ref_rh_ivs)] % 12) + rh_contour_offsets[i]
            else:
                delta = rh_contour_offsets[i]
                # Ensure alternating direction or leap >= 3 so it never forms a 4-note monotonic scale
                if i % 2 == 1 and abs(delta) < 3:
                    delta = 5 if delta >= 0 else -4
                raw_rh = cur_rh + delta

            cur_rh = self._safe_pitch_outside_mud(raw_rh, intent.tonal_center, 62, 86, human_pitches)
            dur_rh = round(max(0.14, step_dt * (0.72 if intent.pedal_strategy == "dry" else 1.04)), 3)
            vel_rh = vel_arch[i % len(vel_arch)]

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

            # Vertical RH harmony support (3rd, 6th, or 4th/7th chord tone below top melody)
            if i in (0, 2, 3, 5):
                harm_iv = 4 if (i % 2 == 0) else 5
                harm_p = self._safe_pitch_outside_mud(cur_rh - harm_iv, intent.tonal_center, 60, cur_rh - 3, human_pitches)
                if harm_p != cur_rh and abs(cur_rh - harm_p) >= 3:
                    events.append(
                        {
                            "pitch": harm_p,
                            "onset_s": round(t + 0.014, 3),  # gentle chord roll
                            "duration_s": round(dur_rh * 0.92, 3),
                            "velocity": max(40, vel_rh - 12),
                            "hand": "rh",
                            "voice": "rh_harmony",
                        }
                    )

            # Left-Hand Bass + Inner Tenor Counterline (cleared from human mud collisions)
            if i in (0, 3):
                bass_root = self._safe_pitch_outside_mud(
                    lh_base + (0 if i == 0 else 5), intent.tonal_center, 26, 46, human_pitches
                )
                fifth_iv = ref_lh_ivs[1] if len(ref_lh_ivs) > 1 and 5 <= ref_lh_ivs[1] <= 12 else 7
                bass_fifth = self._safe_pitch_outside_mud(
                    bass_root + fifth_iv, intent.tonal_center, 31, 52, human_pitches
                )
                if bass_fifth == bass_root:
                    bass_fifth = max(28, bass_root + 7)
                lh_dur = round(step_dt * 2.15, 3)
                events.append(
                    {
                        "pitch": bass_root,
                        "onset_s": round(t, 3),
                        "duration_s": lh_dur,
                        "velocity": max(44, vel_rh - 10),
                        "hand": "lh",
                        "voice": "lh_bass",
                    }
                )
                events.append(
                    {
                        "pitch": bass_fifth,
                        "onset_s": round(t + 0.018, 3),
                        "duration_s": round(lh_dur * 0.92, 3),
                        "velocity": max(40, vel_rh - 16),
                        "hand": "lh",
                        "voice": "lh_fifth",
                    }
                )
            elif i in (1, 4):
                # Inner tenor/alto counterline avoiding mud register around human pitch
                inner_target = lh_base + 14 + (1 if i == 1 else -1)
                inner_p = self._safe_pitch_outside_mud(
                    inner_target,
                    intent.tonal_center,
                    40,
                    59,
                    human_pitches,
                )
                if any(abs(inner_p - hp) <= 4 for hp in human_pitches):
                    inner_p = self._safe_pitch_outside_mud(
                        lh_base + 7, intent.tonal_center, 31, 46, human_pitches
                    )
                events.append(
                    {
                        "pitch": inner_p,
                        "onset_s": round(t, 3),
                        "duration_s": round(step_dt * 1.35, 3),
                        "velocity": max(38, vel_rh - 16),
                        "hand": "lh",
                        "voice": "lh_inner",
                    }
                )

            # Add subtle breathing rest before final cadence note (i == 4)
            breath_gap = 0.09 if i == 4 else 0.015
            t += step_dt + breath_gap

        ped_evs, c64, c67 = _build_continuous_pedal_events(
            total_dur_s=t + 0.25,
            damper_plan=intent.damper_cc64_plan,
            una_corda_plan=intent.una_corda_cc67_plan,
            strategy=strategy,
        )
        return events + ped_evs, c64, c67

    def _repair_to_polyphonic_standard(
        self,
        events: List[Dict[str, Any]],
        human_notes: List[Dict[str, Any]],
        intent: MusicalIntentSchema,
        audit: MusicalityAuditReport,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Auto-repairs any candidate failing MusicalityCritic (breaks scale runs, adds LH/RH harmony & dual pedal)."""
        tonic_pc, _ = SCALE_INTERVALS.get(intent.tonal_center, (2, [0, 2, 3, 5, 7, 9, 10]))
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
                        intent.tonal_center,
                        62,
                        92,
                        human_pitches,
                    )

        if "minor2_spectral_mud" in audit.failures:
            for n in note_evs:
                p = int(n["pitch"])
                if any(abs(p - hp) <= 4 for hp in human_pitches):
                    if str(n.get("hand", "")).lower() == "lh" or p < 60:
                        n["pitch"] = self._safe_pitch_outside_mud(p - 12, intent.tonal_center, 24, 46, human_pitches)
                    else:
                        n["pitch"] = self._safe_pitch_outside_mud(p + 12, intent.tonal_center, 66, 94, human_pitches)

        # Guarantee LH bass + vertical harmony
        has_lh = any(str(n.get("hand", "")).lower() == "lh" or int(n["pitch"]) < 60 for n in note_evs)
        if not has_lh:
            bass_p = _quantize_to_scale(36 + tonic_pc, intent.tonal_center, 30, 48)
            note_evs.append({"pitch": bass_p, "onset_s": 0.0, "duration_s": 0.9, "velocity": 58, "hand": "lh", "voice": "lh_bass"})
            note_evs.append({"pitch": bass_p + 7, "onset_s": 0.02, "duration_s": 0.85, "velocity": 50, "hand": "lh", "voice": "lh_fifth"})

        max_end = max((float(n.get("onset_s", 0.0)) + float(n.get("duration_s", 0.35)) for n in note_evs), default=1.6)
        ped_evs, c64, c67 = _build_continuous_pedal_events(
            max_end + 0.25, intent.damper_cc64_plan, intent.una_corda_cc67_plan, "contrary_open_polyphony"
        )
        return note_evs + ped_evs, c64, c67

    def compose_turn(
        self,
        human_notes: List[Dict[str, Any]],
        intent: MusicalIntentSchema,
        human_mode: str = "cello",
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

        specs = [
            ("A", "対位法反行・両手開離ボイシング案", "contrary_open_polyphony"),
            ("B", "掛留解決・立体内声ポリフォニー案", "suspension_inner_voice"),
            ("C", "高次倍音回避・透明色彩ボイシング案", "overtone_clear_high_spread"),
            ("D", "対照検証案（音階単調・低中域混濁）", "contrastive_flaw_check"),
        ]

        scale_tuple = SCALE_INTERVALS.get(intent.tonal_center, (2, [0, 2, 3, 5, 7, 9, 10]))
        candidates: List[CandidatePhrase] = []

        for idx_spec, (cid, label_ja, strat) in enumerate(specs):
            evs, c64, c67 = self._build_polyphonic_candidate(
                strat, human_notes, intent, self.turn_counter, corpus_refs[idx_spec % len(corpus_refs) :]
            )
            audit = MusicalityCritic.audit_candidate(
                evs,
                human_notes=human_notes,
                tonal_center=intent.tonal_center,
                scale_pcs=scale_tuple,
                register_range=intent.register_range,
                human_mode=human_mode,
            )

            # Auto-repair A/B/C if any MusicalityCritic violation sneaks in
            if cid != "D" and not audit.passed:
                evs, c64, c67 = self._repair_to_polyphonic_standard(evs, human_notes, intent, audit)
                audit = MusicalityCritic.audit_candidate(
                    evs,
                    human_notes=human_notes,
                    tonal_center=intent.tonal_center,
                    scale_pcs=scale_tuple,
                    register_range=intent.register_range,
                    human_mode=human_mode,
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
                )
            )

        eligible = [c for c in candidates if c.candidate_id != "D"]
        best = max(eligible or candidates, key=lambda c: c.total_score)
        best.adopted = True
        best.rejection_tag_ja = ""
        best.problem_note_indices = []
        best.rejection_detail = {}

        return CompositionTurnResult(
            turn_id=self.turn_counter,
            intent=intent,
            candidates=candidates,
            adopted_candidate=best,
            corpus_source=self.corpus_loader.source_mode,
            corpus_refs_used=corpus_refs,
        )
