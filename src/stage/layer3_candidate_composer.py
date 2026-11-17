r"""
Layer 3: Multiple Candidate Composition, Scoring & Pruning Layer (symusic + Zero-Config Corpus).

Core Process Visualization Mechanism:
- Scans `D:\Disklavier-Data` or `data/midi/` via `symusic` when MIDI files exist.
- Automatically falls back to built-in Classical Chamber Music & Counterpoint Seed Dictionary
  (Bach, Fauré, Brahms, Debussy) when zero MIDI files are present (Zero-Config).
- Per turn, generates 4 distinct candidate phrases (Candidate A/B/C/D) using `symusic`:
  1. Theory Motif Variation (contrary-motion inversion / rhythmic augmentation / modal shift)
  2. Contextual Corpus / Seed Accompaniment Matching (Somax2-style)
  3. Lightweight Modal Voice-Leading Continuation
  4. Contrastive Draft exposing register clash or monotonous literal repetition
- Scores candidates on harmonic fit, human register collision avoidance (100-600Hz Cello zone),
  contrary/oblique counterpoint, and density match.
- Selects top 1 as `adopted=True` for L5, and tags remaining 3 with explicit musical rejection tags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from symusic import Note, Score, Track

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
    adopted: bool
    rejection_tag_ja: str
    symusic_note_count: int


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


class MultipleCandidateComposer:
    """
    Layer 3 Composer & Pruner.
    Generates 4 candidate continuations per turn using `symusic`, scores counterpoint & harmony,
    adopts the best candidate, and labels rejected drafts with clear Japanese rejection reasons.
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
            # Invert human melodic intervals and space cleanly within target register
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
                # Add open-fifth bass support on downbeats (avoiding major 3rd collision)
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
                p = _quantize_to_scale(anchor_pitch + (iv % 14) - 5, intent.tonal_center, reg_lo, reg_hi)
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
            # Lightweight Markov/voice-leading arpeggio avoiding middle register muddiness
            scale_steps = [0, 7, 10, 14, 12, 7]
            for i, iv in enumerate(scale_steps):
                p = _quantize_to_scale(reg_lo + 4 + iv, intent.tonal_center, reg_lo, reg_hi)
                dur = round(step_base * 1.1, 3)
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
            # Contrastive draft ("dense_mid_repetition"): intentionally crowds human middle register
            # or repeats literal pitch pattern so the scorer rejects it for transparent display
            mid_clash = int(sum(human_pitches) / len(human_pitches))
            for i in range(8):
                p = max(48, min(72, mid_clash + (0 if i % 2 == 0 else 4)))
                events.append(
                    {
                        "pitch": p,
                        "onset_s": round(t, 3),
                        "duration_s": round(step_base * 0.55, 3),
                        "velocity": 76,
                        "voice": "dense_draft",
                    }
                )
                if i % 2 == 0:
                    # Add thick tertian triad inside human fundamental band (48-64)
                    for triad_iv in (3, 7):
                        events.append(
                            {
                                "pitch": min(74, p + triad_iv),
                                "onset_s": round(t, 3),
                                "duration_s": round(step_base * 0.5, 3),
                                "velocity": 72,
                                "voice": "dense_draft",
                            }
                        )
                t += step_base * 0.52

        # Add CC#64 pedal event
        events.append({"pitch": 0, "onset_s": 0.0, "control": 64, "value": pedal_val, "voice": "pedal"})
        return events

    def _score_candidate(
        self,
        events: List[Dict[str, Any]],
        human_notes: List[Dict[str, Any]],
        intent: MusicalIntentSchema,
        strategy: str,
        human_mode: str,
    ) -> Tuple[float, Dict[str, float], str]:
        note_evs = [e for e in events if int(e.get("pitch", 0)) > 0]
        if not note_evs:
            return 0.0, {}, "[棄却: 音符欠如]"

        tonic_pc, scale_pcs = SCALE_INTERVALS.get(intent.tonal_center, (2, [0, 2, 3, 5, 7, 9, 10]))
        reg_lo, reg_hi = intent.register_range[0], intent.register_range[1]
        human_pitches = [
            int(n.get("pitch", n.get("note", 62)))
            for n in human_notes
            if int(n.get("pitch", n.get("note", 0))) > 0
        ] or [62]

        # 1. Harmonic Fit (0..30)
        in_scale = sum(1 for e in note_evs if (int(e["pitch"]) - tonic_pc) % 12 in scale_pcs)
        harmonic_fit = 30.0 * (in_scale / len(note_evs))

        # 2. Register Collision Avoidance (0..30) — penalize colliding inside human register / Cello 100-600Hz (MIDI 44-72)
        in_target_reg = sum(1 for e in note_evs if reg_lo <= int(e["pitch"]) <= reg_hi)
        clash_count = 0
        for e in note_evs:
            p = int(e["pitch"])
            if any(abs(p - hp) <= 4 for hp in human_pitches):
                clash_count += 1
        collision_ratio = clash_count / len(note_evs)
        register_sep = max(0.0, 30.0 * (in_target_reg / len(note_evs)) - 26.0 * collision_ratio)

        # 3. Counterpoint & Parallel-5th/8ve + Repetition Check (0..25)
        pitches = [int(e["pitch"]) for e in note_evs]
        unique_ratio = len(set(pitches)) / len(pitches)
        contrary_bonus = 6.0 if strategy == "contrary_counterpoint" else 2.5
        parallel_penalty = 0.0
        for i in range(1, len(pitches)):
            if abs(pitches[i] - pitches[i - 1]) in (7, 12) and i < len(human_pitches):
                if abs(human_pitches[i % len(human_pitches)] - human_pitches[(i - 1) % len(human_pitches)]) == abs(pitches[i] - pitches[i - 1]):
                    parallel_penalty += 4.5
        counterpoint_score = max(0.0, min(25.0, 14.0 * unique_ratio + contrary_bonus + 7.0 - parallel_penalty))

        # 4. Target Density Match (0..15)
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

        # Determine most informative musical rejection tag if rejected
        if collision_ratio > 0.35 or register_sep < 16.0:
            rej_tag = "[棄却: チェロ中音域との濁り]" if human_mode == "cello" else "[棄却: 厚すぎる三度重複]"
        elif unique_ratio < 0.45:
            rej_tag = "[棄却: 同形反復による停滞]"
        elif density_diff > 0.32 or len(note_evs) >= 14:
            rej_tag = "[棄却: 密度過多]"
        elif parallel_penalty > 0:
            rej_tag = "[棄却: 並達五度の発生]"
        else:
            rej_tag = "[棄却: 対位法的推進力の不足]"

        return total, subscores, rej_tag

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
            # Build genuine symusic.Score for each candidate to verify/measure symbolic representation
            sc = _events_to_symusic_score(evs)
            sym_count = sum(len(tr.notes) for tr in sc.tracks)
            score, subscores, rej_tag = self._score_candidate(
                evs, human_notes, intent, strat, human_mode
            )
            candidates.append(
                CandidatePhrase(
                    candidate_id=cid,
                    label_ja=label_ja,
                    strategy=strat,
                    events=evs,
                    total_score=score,
                    subscores=subscores,
                    adopted=False,
                    rejection_tag_ja=rej_tag,
                    symusic_note_count=sym_count,
                )
            )

        best = max(candidates, key=lambda c: c.total_score)
        best.adopted = True
        best.rejection_tag_ja = ""

        return CompositionTurnResult(
            turn_id=self.turn_counter,
            intent=intent,
            candidates=candidates,
            adopted_candidate=best,
            corpus_source=self.corpus_loader.source_mode,
        )
