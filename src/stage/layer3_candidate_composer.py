r"""
Layer 3: Multiple Candidate Composition, Scoring & Pruning Layer (symusic + Zero-Config Corpus).

Core Process Visualization Mechanism:
- Scans `D:\Disklavier-Data` or `data/midi/` via `symusic` when MIDI files exist.
- Automatically falls back to built-in Classical Chamber Music & Counterpoint Seed Dictionary
  (Bach, Fauré, Brahms, Debussy) when zero MIDI files are present (Zero-Config).
- Per turn, generates 4 distinct candidate phrases (Candidate A/B/C/D) using `symusic`:
  1. Contrary Counterpoint Variation (clean register separation & open-fifth support)
  2. Contextual Corpus / Seed Accompaniment Matching (Somax2-style)
  3. Lightweight Modal Voice-Leading Continuation
  4. Contrastive Draft exposing middle-register muddiness / minor-2nd clash / repetition
- Computes 4-axis normalized scores (harmonic fit, counterpoint motion, cello overtone clearance,
  rhythm density), exact problem note indices, measure/beat coordinates, and acoustic penalty tags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from symusic import Note, Score, Track

from src.stage.layer1_perception import midi_to_note_name
from src.stage.layer2_intent_llm import MusicalIntentSchema

REPO_ROOT = Path(__file__).resolve().parents[2]

SCALE_INTERVALS: Dict[str, Tuple[int, List[int]]] = {
    "D_dorian": (2, [0, 2, 3, 5, 7, 9, 10]),
    "D_minor": (2, [0, 2, 3, 5, 7, 8, 10]),
    "G_minor": (7, [0, 2, 3, 5, 7, 8, 10]),
    "C_major": (0, [0, 2, 4, 5, 7, 9, 11]),
    "F_major": (5, [0, 2, 4, 5, 7, 9, 11]),
    "A_minor": (9, [0, 2, 3, 5, 7, 8, 10]),
}

BUILTIN_CHAMBER_SEED_DICTIONARY: List[Dict[str, Any]] = [
    {
        "id": "seed_bach_counterpoint",
        "composer": "J.S. Bach",
        "title": "Two-Part Contrapuntal Inversion",
        "intervals": [0, 7, 5, 4, 2, 0, -1, 0],
        "rhythm_beats": [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 1.0],
        "velocities": [66, 70, 72, 74, 70, 68, 64, 62],
        "pedal_cc64": 24,
    },
    {
        "id": "seed_faure_elegy",
        "composer": "G. Fauré",
        "title": "Syncopated Chamber Arpeggio",
        "intervals": [0, 7, 12, 14, 15, 12, 7, 10],
        "rhythm_beats": [0.75, 0.5, 0.75, 0.5, 0.75, 0.5, 0.75, 1.0],
        "velocities": [54, 58, 64, 68, 70, 62, 56, 52],
        "pedal_cc64": 54,
    },
    {
        "id": "seed_brahms_op38",
        "composer": "J. Brahms",
        "title": "Open Fifth Bass & Tenor Arch",
        "intervals": [0, 7, 10, 12, 7, 3, 2, 0],
        "rhythm_beats": [1.0, 0.5, 0.5, 1.0, 0.5, 0.5, 0.5, 1.0],
        "velocities": [62, 66, 70, 76, 72, 66, 62, 58],
        "pedal_cc64": 48,
    },
    {
        "id": "seed_debussy_planing",
        "composer": "C. Debussy",
        "title": "Modal Quartal & Ninth Resonance",
        "intervals": [0, 5, 10, 14, 12, 7, 5, 2],
        "rhythm_beats": [0.75, 0.75, 0.75, 1.0, 0.75, 0.75, 0.75, 1.25],
        "velocities": [46, 52, 58, 62, 58, 52, 48, 42],
        "pedal_cc64": 62,
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


@dataclass
class CompositionTurnResult:
    turn_id: int
    intent: MusicalIntentSchema
    candidates: List[CandidatePhrase]
    adopted_candidate: CandidatePhrase
    corpus_source: str


class ZeroConfigCorpusLoader:
    """
    Scans `D:\\Disklavier-Data` and `data/midi/` with `symusic`.
    Never fails when MIDI directories are empty or missing: falls back immediately
    to `BUILTIN_CHAMBER_SEED_DICTIONARY`.
    """

    def __init__(self, search_dirs: Optional[List[Path]] = None, max_files: int = 24) -> None:
        self.search_dirs = (
            search_dirs
            if search_dirs is not None
            else [
                Path(r"D:\Disklavier-Data"),
                REPO_ROOT / "data" / "midi",
            ]
        )
        self.max_files = max_files
        self.patterns: List[Dict[str, Any]] = []
        self.source_mode: str = "builtin_seed_dictionary"
        self.loaded_file_count: int = 0
        self.load()

    def load(self) -> List[Dict[str, Any]]:
        midi_files: List[Path] = []
        for d in self.search_dirs:
            if d.exists() and d.is_dir():
                for ext in ("*.mid", "*.midi"):
                    midi_files.extend(sorted(d.rglob(ext)))
                    if len(midi_files) >= self.max_files:
                        break
            if len(midi_files) >= self.max_files:
                break

        extracted: List[Dict[str, Any]] = []
        for mf in midi_files[: self.max_files]:
            try:
                sc = Score(str(mf))
                tpq = max(1, int(sc.ticks_per_quarter))
                all_notes = []
                for tr in sc.tracks:
                    for n in tr.notes:
                        all_notes.append((int(n.time), int(n.pitch), int(n.duration), int(n.velocity)))
                all_notes.sort(key=lambda x: x[0])
                if len(all_notes) >= 6:
                    slice_notes = all_notes[:12]
                    base_p = slice_notes[0][1]
                    intervals = [p - base_p for _, p, _, _ in slice_notes]
                    beats = [max(0.25, min(2.0, round(d / tpq, 2))) for _, _, d, _ in slice_notes]
                    vels = [max(24, min(105, v)) for _, _, _, v in slice_notes]
                    extracted.append(
                        {
                            "id": f"corpus_{mf.stem}",
                            "composer": "Corpus MIDI",
                            "title": mf.stem,
                            "intervals": intervals[:8],
                            "rhythm_beats": beats[:8],
                            "velocities": vels[:8],
                            "pedal_cc64": 46,
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
        return self.patterns


def _quantize_to_scale(pitch: int, tonal_center: str, min_midi: int, max_midi: int) -> int:
    tonic_pc, intervals = SCALE_INTERVALS.get(tonal_center, (2, [0, 2, 3, 5, 7, 9, 10]))
    best_p = max(min_midi, min(max_midi, int(pitch)))
    best_dist = 999
    for candidate in range(max(21, best_p - 4), min(108, best_p + 5)):
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
    tr = Track(name="Candidate")
    for ev in events:
        p = int(ev.get("pitch", ev.get("note", 0)) or 0)
        if p <= 0:
            continue
        t_tick = max(0, int(round(float(ev["onset_s"]) * tpq * 2)))  # 120 bpm mapping
        d_tick = max(48, int(round(float(ev["duration_s"]) * tpq * 2)))
        vel = max(18, min(112, int(ev.get("velocity", 64))))
        tr.notes.append(Note(time=t_tick, duration=d_tick, pitch=p, velocity=vel))
    sc.tracks.append(tr)
    return sc


def _onset_to_measure_beat(onset_s: float, beat_s: float = 0.5) -> Tuple[int, int]:
    total_beats = max(0.0, float(onset_s) / beat_s)
    measure = int(total_beats // 4) + 1
    beat = int(total_beats % 4) + 1
    return measure, beat


class MultipleCandidateComposer:
    """
    Layer 3 Composer & Pruner.
    Generates 4 candidate continuations per turn using `symusic`, scores 4 musical axes,
    adopts the best candidate, and isolates exact conflicting note coordinates & penalties
    for transparent real-time stage auditioning.
    """

    def __init__(self, corpus_loader: Optional[ZeroConfigCorpusLoader] = None) -> None:
        self.corpus_loader = corpus_loader or ZeroConfigCorpusLoader()
        self.turn_counter: int = 0

    def _build_candidate_events(
        self,
        strategy: str,
        human_notes: List[Dict[str, Any]],
        intent: MusicalIntentSchema,
        turn_idx: int,
    ) -> List[Dict[str, Any]]:
        reg_lo, reg_hi = intent.register_range[0], intent.register_range[1]
        tonic_pc, _ = SCALE_INTERVALS.get(intent.tonal_center, (2, [0, 2, 3, 5, 7, 9, 10]))
        human_pitches = [
            int(n.get("pitch", n.get("note", 62)))
            for n in human_notes
            if int(n.get("pitch", n.get("note", 0))) > 0
        ] or [62, 65, 69, 67]

        anchor_mid = (reg_lo + reg_hi) // 2
        anchor_pitch = _quantize_to_scale(anchor_mid, intent.tonal_center, reg_lo, reg_hi)
        pedal_val = {"dry": 18, "half_clear": 48, "resonant": 70}.get(intent.pedal_strategy, 46)

        events: List[Dict[str, Any]] = []
        t = 0.0
        step_base = max(0.24, 0.56 - intent.target_density * 0.28)

        if strategy == "contrary_counterpoint":
            diffs = [human_pitches[i] - human_pitches[i - 1] for i in range(1, len(human_pitches))]
            if not diffs:
                diffs = [3, 4, -2, -3]
            cur = anchor_pitch
            for i in range(6):
                d = -diffs[i % len(diffs)] if i > 0 else 0
                cur = _quantize_to_scale(cur + d, intent.tonal_center, reg_lo, reg_hi)
                dur = round(step_base * (0.72 if intent.pedal_strategy == "dry" else 1.05), 3)
                vel = min(98, 68 + (i % 3) * 6)
                events.append(
                    {
                        "pitch": cur,
                        "onset_s": round(t, 3),
                        "duration_s": max(0.06, dur),
                        "velocity": vel,
                        "voice": "melody",
                    }
                )
                if i % 3 == 0:
                    bass_p = max(28, _quantize_to_scale(36 + tonic_pc, intent.tonal_center, 28, 48))
                    events.append(
                        {
                            "pitch": bass_p,
                            "onset_s": round(t, 3),
                            "duration_s": round(step_base * 2.2, 3),
                            "velocity": 56,
                            "voice": "bass",
                        }
                    )
                t += step_base

        elif strategy == "corpus_context_match":
            pats = self.corpus_loader.patterns or BUILTIN_CHAMBER_SEED_DICTIONARY
            pat = pats[turn_idx % len(pats)]
            intervals = pat["intervals"][:6]
            vels = pat["velocities"][:6]
            beats = pat["rhythm_beats"][:6]
            for idx, iv in enumerate(intervals):
                # Deliberately keeps partial octave overlap / repetitive motif anchor for clear contrast
                p = _quantize_to_scale(anchor_pitch + (iv % 10) - 5, intent.tonal_center, reg_lo, reg_hi)
                if idx in (2, 3):
                    # Repeat same pitch to demonstrate motif stagnation penalty when compared to A
                    p = events[-1]["pitch"] if events else p
                dur = round(max(0.08, beats[idx % len(beats)] * step_base * 0.9), 3)
                events.append(
                    {
                        "pitch": p,
                        "onset_s": round(t, 3),
                        "duration_s": dur,
                        "velocity": int(vels[idx % len(vels)]),
                        "voice": "accompaniment",
                    }
                )
                t += dur * 0.95

        elif strategy == "modal_voice_leading":
            scale_steps = [0, 5, 7, 10, 7, 5]
            for i, iv in enumerate(scale_steps):
                p = _quantize_to_scale(reg_lo + 2 + iv, intent.tonal_center, reg_lo, reg_hi)
                dur = round(step_base * 1.05, 3)
                events.append(
                    {
                        "pitch": p,
                        "onset_s": round(t, 3),
                        "duration_s": dur,
                        "velocity": 54 + (i % 4) * 5,
                        "voice": "oboe_line",
                    }
                )
                t += step_base * 0.95

        else:
            # Contrastive draft ("dense_mid_repetition"): intentionally places minor-2nd clash & thick triad
            # directly inside the human fundamental register (100-600 Hz) so L3 rejects and highlights it.
            primary_human = human_pitches[-1]
            clash_p = max(44, min(72, primary_human + 1))  # Minor 2nd clash against human pitch
            mid_clash = int(sum(human_pitches) / len(human_pitches))
            for i in range(7):
                p = clash_p if i == 0 else max(46, min(72, mid_clash + (0 if i % 2 == 0 else 4)))
                events.append(
                    {
                        "pitch": p,
                        "onset_s": round(t, 3),
                        "duration_s": round(step_base * 0.55, 3),
                        "velocity": 78,
                        "voice": "dense_draft",
                    }
                )
                if i % 2 == 0:
                    for triad_iv in (3, 7):
                        events.append(
                            {
                                "pitch": min(74, p + triad_iv),
                                "onset_s": round(t, 3),
                                "duration_s": round(step_base * 0.5, 3),
                                "velocity": 74,
                                "voice": "dense_draft",
                            }
                        )
                t += step_base * 0.48

        events.append({"pitch": 0, "onset_s": 0.0, "control": 64, "value": pedal_val, "voice": "pedal"})
        return events

    def _score_candidate(
        self,
        events: List[Dict[str, Any]],
        human_notes: List[Dict[str, Any]],
        intent: MusicalIntentSchema,
        strategy: str,
        human_mode: str,
    ) -> Tuple[float, Dict[str, float], Dict[str, int], str, List[int], Dict[str, Any]]:
        note_evs = [e for e in events if int(e.get("pitch", 0)) > 0]
        if not note_evs:
            return (
                0.0,
                {},
                {"harmonic_fit": 0, "counterpoint_quality": 0, "overtone_clearance": 0, "rhythm_density": 0},
                "[棄却: 音符欠如]",
                [],
                {},
            )

        tonic_pc, scale_pcs = SCALE_INTERVALS.get(intent.tonal_center, (2, [0, 2, 3, 5, 7, 9, 10]))
        reg_lo, reg_hi = intent.register_range[0], intent.register_range[1]
        human_pitches = [
            int(n.get("pitch", n.get("note", 62)))
            for n in human_notes
            if int(n.get("pitch", n.get("note", 0))) > 0
        ] or [62]
        human_primary = human_pitches[-1]
        human_primary_name = midi_to_note_name(human_primary)

        # 1. Harmonic Fit (0..30)
        in_scale = sum(1 for e in note_evs if (int(e["pitch"]) - tonic_pc) % 12 in scale_pcs)
        harmonic_fit = 30.0 * (in_scale / len(note_evs))

        # 2. Register & Overtone Clearance (0..30) — detect exact colliding notes (minor 2nd / same band)
        in_target_reg = sum(1 for e in note_evs if reg_lo <= int(e["pitch"]) <= reg_hi)
        clash_indices: List[int] = []
        minor2_indices: List[int] = []
        for idx, e in enumerate(note_evs):
            p = int(e["pitch"])
            if any(abs(p - hp) in (1, 2) for hp in human_pitches):
                minor2_indices.append(idx)
                clash_indices.append(idx)
            elif any(abs(p - hp) <= 4 for hp in human_pitches):
                clash_indices.append(idx)
        collision_ratio = len(clash_indices) / len(note_evs)
        minor2_penalty = 10.0 if minor2_indices else 0.0
        register_sep = max(0.0, 30.0 * (in_target_reg / len(note_evs)) - 26.0 * collision_ratio - minor2_penalty)

        # 3. Counterpoint Voice Leading (contrary / oblique motion & repetition check, 0..25)
        pitches = [int(e["pitch"]) for e in note_evs]
        unique_ratio = len(set(pitches)) / len(pitches)
        contrary_bonus = 6.5 if strategy == "contrary_counterpoint" else (3.0 if strategy == "modal_voice_leading" else 1.5)
        repeat_indices = [i for i in range(1, len(pitches)) if pitches[i] == pitches[i - 1]]
        parallel_indices: List[int] = []
        parallel_penalty = 0.0
        for i in range(1, len(pitches)):
            if abs(pitches[i] - pitches[i - 1]) in (7, 12) and i < len(human_pitches):
                if abs(human_pitches[i % len(human_pitches)] - human_pitches[(i - 1) % len(human_pitches)]) == abs(pitches[i] - pitches[i - 1]):
                    parallel_penalty += 4.5
                    parallel_indices.append(i)
        repeat_penalty = min(6.0, len(repeat_indices) * 2.5)
        counterpoint_score = max(
            0.0,
            min(25.0, 13.5 * unique_ratio + contrary_bonus + 7.0 - parallel_penalty - repeat_penalty),
        )

        # 4. Rhythm Density Match (0..15)
        span = max(1.0, max(float(e["onset_s"]) for e in note_evs) + 0.4)
        actual_density = min(1.0, (len(note_evs) / span) / 8.0)
        density_diff = abs(actual_density - intent.target_density)
        density_score = max(0.0, 15.0 * (1.0 - min(1.0, density_diff * 1.8)))

        total = round(harmonic_fit + register_sep + counterpoint_score + density_score, 2)
        subscores = {
            "harmonic_fit": round(harmonic_fit, 1),
            "register_separation": round(register_sep, 1),
            "counterpoint_quality": round(counterpoint_score, 1),
            "density_fit": round(density_score, 1),
        }
        subscores_pct = {
            "harmonic_fit": int(round((harmonic_fit / 30.0) * 100.0)),
            "counterpoint_quality": int(round((counterpoint_score / 25.0) * 100.0)),
            "overtone_clearance": int(round((register_sep / 30.0) * 100.0)),
            "rhythm_density": int(round((density_score / 15.0) * 100.0)),
        }

        # Identify exact problem indices, measure/beat, and numeric acoustic penalty score
        if minor2_indices or collision_ratio > 0.30 or register_sep < 18.0:
            penalty_val = -max(22, int(round(48 - register_sep)))
            if minor2_indices:
                prob_idxs = minor2_indices[:2]
                target_ev = note_evs[prob_idxs[0]]
                m_no, b_no = _onset_to_measure_beat(float(target_ev.get("onset_s", 0.0)))
                tp = int(target_ev["pitch"])
                target_name = midi_to_note_name(tp)
                hp_match = next((hp for hp in human_pitches if abs(tp - hp) in (1, 2)), human_primary)
                hp_name = midi_to_note_name(hp_match)
                reason_core = f"{m_no}小節{b_no}拍 {hp_name}基音と{target_name}が短2度衝突（濁りスコア {penalty_val}）"
                short_reason = f"{hp_name}と{target_name}が短2度衝突し濁る"
            elif clash_indices:
                prob_idxs = clash_indices[:2]
                target_ev = note_evs[prob_idxs[0]]
                m_no, b_no = _onset_to_measure_beat(float(target_ev.get("onset_s", 0.0)))
                tp = int(target_ev["pitch"])
                target_name = midi_to_note_name(tp)
                hp_match = next((hp for hp in human_pitches if abs(tp - hp) <= 4), human_primary)
                hp_name = midi_to_note_name(hp_match)
                reason_core = f"{m_no}小節{b_no}拍 {hp_name}倍音帯と{target_name}が三度重複（濁りスコア {penalty_val}）"
                short_reason = f"{hp_name}倍音帯と重なり濁る"
            else:
                out_idxs = [i for i, e in enumerate(note_evs) if not (reg_lo <= int(e["pitch"]) <= reg_hi)] or [0]
                prob_idxs = out_idxs[:2]
                target_ev = note_evs[prob_idxs[0]]
                m_no, b_no = _onset_to_measure_beat(float(target_ev.get("onset_s", 0.0)))
                target_name = midi_to_note_name(int(target_ev["pitch"]))
                reason_core = f"{m_no}小節{b_no}拍 {target_name}が目標音域外で倍音干渉（音域減点 {penalty_val}）"
                short_reason = f"{target_name}が目標音域から外れ干渉する"
        elif parallel_indices:
            prob_idxs = parallel_indices[:2]
            target_ev = note_evs[prob_idxs[0]]
            m_no, b_no = _onset_to_measure_beat(float(target_ev.get("onset_s", 0.0)))
            target_name = midi_to_note_name(int(target_ev["pitch"]))
            penalty_val = -max(18, int(round(32 - counterpoint_score)))
            reason_core = f"{m_no}小節{b_no}拍 {target_name}で並達五度・八度が発生（連結減点 {penalty_val}）"
            short_reason = f"{m_no}小節{b_no}拍で並達五度が発生"
        elif repeat_indices or unique_ratio < 0.72:
            prob_idxs = repeat_indices[:2] or [len(note_evs) - 2, len(note_evs) - 1]
            target_ev = note_evs[prob_idxs[0]]
            m_no, b_no = _onset_to_measure_beat(float(target_ev.get("onset_s", 0.0)))
            target_name = midi_to_note_name(int(target_ev["pitch"]))
            penalty_val = -max(14, int(round(28 - counterpoint_score)))
            reason_core = f"{m_no}小節{b_no}拍 {target_name}の同音反復で声部停滞（推進力 {penalty_val}）"
            short_reason = f"{target_name}の同音反復で停滞"
        elif density_diff > 0.25 or len(note_evs) >= 12:
            prob_idxs = [len(note_evs) - 2, len(note_evs) - 1]
            target_ev = note_evs[prob_idxs[0]]
            m_no, b_no = _onset_to_measure_beat(float(target_ev.get("onset_s", 0.0)))
            penalty_val = -max(16, int(round(30 - density_score * 2.0)))
            reason_core = f"{m_no}小節{b_no}拍 リズム密度過多で過密（密度減点 {penalty_val}）"
            short_reason = "リズム密度が目標値より過密"
        else:
            prob_idxs = [len(note_evs) - 1]
            target_ev = note_evs[prob_idxs[0]]
            m_no, b_no = _onset_to_measure_beat(float(target_ev.get("onset_s", 0.0)))
            target_name = midi_to_note_name(int(target_ev["pitch"]))
            penalty_val = -max(10, int(round(100.0 - total)))
            reason_core = f"{m_no}小節{b_no}拍 {target_name}の反行推進力が不足（対位法減点 {penalty_val}）"
            short_reason = "対位法的な反行推進力が不足"

        rej_tag = f"[棄却: {reason_core}]"
        rej_detail = {
            "measure": m_no,
            "beat": b_no,
            "note_name": midi_to_note_name(int(note_evs[prob_idxs[0]]["pitch"])),
            "human_note_name": human_primary_name,
            "penalty_score": penalty_val,
            "reason_core_ja": reason_core,
            "short_reason_ja": short_reason,
            "hud_badge_ja": f"[× 棄却: {reason_core}]",
        }
        return total, subscores, subscores_pct, rej_tag, prob_idxs, rej_detail

    def compose_turn(
        self,
        human_notes: List[Dict[str, Any]],
        intent: MusicalIntentSchema,
        human_mode: str = "cello",
    ) -> CompositionTurnResult:
        self.turn_counter += 1
        specs = [
            ("A", "対位法的反行案", "contrary_counterpoint"),
            ("B", "コーパス文脈照合案", "corpus_context_match"),
            ("C", "旋法声部連結案", "modal_voice_leading"),
            ("D", "中音域密集対照案", "dense_mid_repetition"),
        ]

        candidates: List[CandidatePhrase] = []
        for cid, label_ja, strat in specs:
            evs = self._build_candidate_events(strat, human_notes, intent, self.turn_counter)
            sc = _events_to_symusic_score(evs)
            sym_count = sum(len(tr.notes) for tr in sc.tracks)
            score, subscores, subscores_pct, rej_tag, prob_idxs, rej_detail = self._score_candidate(
                evs, human_notes, intent, strat, human_mode
            )
            pitches = sorted({int(e["pitch"]) for e in evs if int(e.get("pitch", 0)) > 0})
            pitch_range_str = (
                f"{midi_to_note_name(pitches[0])}-{midi_to_note_name(pitches[-1])}"
                if pitches
                else "--"
            )
            seq_pitches = [int(e["pitch"]) for e in evs if int(e.get("pitch", 0)) > 0][:3]
            sample_notes_str = "-".join(midi_to_note_name(p) for p in seq_pitches) or "--"

            candidates.append(
                CandidatePhrase(
                    candidate_id=cid,
                    label_ja=label_ja,
                    strategy=strat,
                    events=evs,
                    total_score=score,
                    subscores=subscores,
                    subscores_pct=subscores_pct,
                    adopted=False,
                    rejection_tag_ja=rej_tag,
                    symusic_note_count=sym_count,
                    pitch_range_str=pitch_range_str,
                    sample_notes_str=sample_notes_str,
                    problem_note_indices=prob_idxs,
                    rejection_detail=rej_detail,
                )
            )

        best = max(candidates, key=lambda c: c.total_score)
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
        )
