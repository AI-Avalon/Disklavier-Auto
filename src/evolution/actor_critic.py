"""
Actor-Critic Self-Evolving Engine: MusicalGenome, 5-Axis Multi-Objective Critic (100 pts),
and Parallel Multiverse Candidate Generator (Branches A, B, C, D).
"""

from __future__ import annotations

import copy
import math
import random
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.audio.c519ml_monitor import midi_to_name
from src.virtual_twin.virtual_disklavier import DisklavierPhysicsSimulator


ARC_STAGES = [
    ("Exposition", "主題提示 (Exposition)", 0.42),
    ("Development", "動機労作による展開 (Development)", 0.68),
    ("Climax", "対位法的クライマックス (Climax)", 0.92),
    ("Recapitulation", "静謐な再現 (Recapitulation)", 0.50),
    ("Metamorphosis", "新たな主題への変容 (Metamorphosis)", 0.62),
]

CHORD_MODULATION_ROUTES = [
    {"name": "Tonic -> Chromatic Mediant (bVI maj7#11)", "root_shift": 8, "mode": [0, 2, 4, 6, 7, 9, 11], "novelty": 0.92},
    {"name": "Pivot Minor Subdominant (iv9 -> V13)", "root_shift": 5, "mode": [0, 2, 3, 5, 7, 9, 10], "novelty": 0.85},
    {"name": "Neapolitan Pivot (bII maj7 -> V7/vi)", "root_shift": 1, "mode": [0, 2, 4, 6, 7, 9, 11], "novelty": 0.95},
    {"name": "Tritone Substitute Dominant (bII7 -> I)", "root_shift": 1, "mode": [0, 2, 4, 6, 7, 9, 10], "novelty": 0.88},
    {"name": "Upper Mediant Modal Shift (III7 -> vi9)", "root_shift": 4, "mode": [0, 2, 3, 5, 7, 8, 11], "novelty": 0.87},
    {"name": "Dorian Rising Bass (i7 -> IV9)", "root_shift": 5, "mode": [0, 2, 3, 5, 7, 9, 10], "novelty": 0.82},
    {"name": "Relative Minor Pivot (vi9 -> ii7b5)", "root_shift": 9, "mode": [0, 2, 3, 5, 7, 8, 10], "novelty": 0.78},
    {"name": "Plagal Resolution (IVadd9 -> I)", "root_shift": 0, "mode": [0, 2, 4, 5, 7, 9, 11], "novelty": 0.74},
]


@dataclass
class MusicalGenome:
    generation: int = 1
    genome_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    contrary_motion_weight: float = 0.22
    chromatic_mediant_prob: float = 0.18
    counterpoint_density: float = 0.24
    legato_overlap_ms: float = 2.0  # Target sweet spot: 5..15 ms
    staccato_contrast_ratio: float = 0.04
    pedal_preroll_ms: float = 12.0  # Target sweet spot: 30..50 ms
    half_pedal_depth: int = 96  # Target sweet spot: 35..80
    velocity_center: int = 84  # Target sweet spot: 52..72
    velocity_span: int = 8  # Target sweet spot: 12..24
    bass_chromaticism: float = 0.15
    motivic_transform_weights: Dict[str, float] = field(
        default_factory=lambda: {
            "original": 0.45,
            "inversion": 0.15,
            "retrograde_inversion": 0.08,
            "augmentation": 0.12,
            "diminution": 0.10,
            "stretto": 0.10,
        }
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def mutate(
        self,
        directives_bias: Optional[Dict[str, Any]] = None,
        generation: Optional[int] = None,
        rng: Optional[random.Random] = None,
    ) -> "MusicalGenome":
        r = rng or random.Random()
        g = copy.deepcopy(self)
        g.generation = generation if generation is not None else (self.generation + 1)
        g.genome_id = uuid.uuid4().hex[:8]

        # Directional evolutionary drift + Gaussian exploration
        step_scale = max(0.04, 0.16 * math.exp(-0.08 * max(0, g.generation - 1)))

        def nudge(val: float, target: float, lo: float, hi: float, jitter: float) -> float:
            new_v = val + (target - val) * (0.28 + step_scale) + r.uniform(-jitter, jitter)
            return round(max(lo, min(hi, new_v)), 4)

        directives_bias = directives_bias or {}
        target_contrary = 0.86
        target_chroma_bass = float(directives_bias.get("bass_chromaticism_target", 0.62))
        target_staccato = float(directives_bias.get("staccato_contrast_target", 0.28))
        target_half_pedal = int(directives_bias.get("half_pedal_target", 62))
        target_cp_density = float(directives_bias.get("counterpoint_density_target", 0.78))

        g.contrary_motion_weight = nudge(g.contrary_motion_weight, target_contrary, 0.2, 0.98, 0.06)
        g.chromatic_mediant_prob = nudge(g.chromatic_mediant_prob, 0.68, 0.1, 0.95, 0.07)
        g.counterpoint_density = nudge(g.counterpoint_density, target_cp_density, 0.2, 0.95, 0.06)
        g.legato_overlap_ms = nudge(g.legato_overlap_ms, 11.0, 4.0, 16.0, 1.5)
        g.staccato_contrast_ratio = nudge(g.staccato_contrast_ratio, target_staccato, 0.05, 0.55, 0.05)
        g.pedal_preroll_ms = nudge(g.pedal_preroll_ms, 40.0, 20.0, 52.0, 3.0)
        g.half_pedal_depth = int(round(nudge(float(g.half_pedal_depth), float(target_half_pedal), 35.0, 82.0, 4.0)))
        g.velocity_center = int(round(nudge(float(g.velocity_center), 62.0, 48.0, 74.0, 3.0)))
        g.velocity_span = int(round(nudge(float(g.velocity_span), 17.0, 10.0, 24.0, 2.0)))
        g.bass_chromaticism = nudge(g.bass_chromaticism, target_chroma_bass, 0.1, 0.95, 0.07)

        # Normalize motivic transform weights toward balanced contrapuntal richness
        targets = {
            "original": 0.24,
            "inversion": 0.20,
            "retrograde_inversion": 0.14,
            "augmentation": 0.15,
            "diminution": 0.14,
            "stretto": 0.13,
        }
        new_w: Dict[str, float] = {}
        for k, tgt in targets.items():
            cur = g.motivic_transform_weights.get(k, 0.15)
            new_w[k] = max(0.05, cur + (tgt - cur) * 0.30 + r.uniform(-0.02, 0.02))
        s = sum(new_w.values())
        g.motivic_transform_weights = {k: round(v / s, 3) for k, v in new_w.items()}
        return g

    def crossover(self, other: "MusicalGenome", generation: int, rng: Optional[random.Random] = None) -> "MusicalGenome":
        r = rng or random.Random()
        child = MusicalGenome(generation=generation)
        for fld in (
            "contrary_motion_weight",
            "chromatic_mediant_prob",
            "counterpoint_density",
            "legato_overlap_ms",
            "staccato_contrast_ratio",
            "pedal_preroll_ms",
            "bass_chromaticism",
        ):
            v1 = getattr(self, fld)
            v2 = getattr(other, fld)
            alpha = r.uniform(0.3, 0.7)
            setattr(child, fld, round(alpha * v1 + (1.0 - alpha) * v2, 4))

        for fld in ("half_pedal_depth", "velocity_center", "velocity_span"):
            v1 = getattr(self, fld)
            v2 = getattr(other, fld)
            child_val = int(round(0.5 * (v1 + v2)))
            setattr(child, fld, child_val)

        w: Dict[str, float] = {}
        for k in self.motivic_transform_weights:
            w[k] = 0.5 * (
                self.motivic_transform_weights.get(k, 0.16)
                + other.motivic_transform_weights.get(k, 0.16)
            )
        s = sum(w.values()) or 1.0
        child.motivic_transform_weights = {k: round(v / s, 3) for k, v in w.items()}
        return child


def _quantize_to_mode(pitch: int, root_pc: int, mode_intervals: List[int]) -> int:
    rel = (pitch - root_pc) % 12
    octave = (pitch - root_pc) // 12
    nearest = min(mode_intervals, key=lambda m: abs(m - rel))
    return max(28, min(96, root_pc + octave * 12 + nearest))


class MultiObjectiveCritic:
    """
    5-Axis, 100-Point Aesthetic & Physical Self-Scoring Engine:
      1. Voice-Leading & Counterpoint (0..25)
      2. Motivic Continuity & Flow (0..20)
      3. Harmonic Originality & Tension Arc (0..20)
      4. Expressive Articulation (0..15)
      5. Disklavier Physical Playability (0..20)
    """

    def score_candidate(
        self,
        candidate: Dict[str, Any],
        prev_bar_state: Dict[str, Any],
        story_arc_stage: Tuple[str, str, float],
        history_chords: List[str],
        directives: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        events = candidate.get("events", [])
        genome_dict = candidate.get("genome", {})
        directives = directives or {}

        mel_notes = sorted(
            [e for e in events if e.get("voice") in ("melody", "cello_motif") and int(e.get("note", 0)) > 0],
            key=lambda x: float(x.get("time_offset_s", 0.0)),
        )
        bass_notes = sorted(
            [e for e in events if e.get("voice") == "bass" and int(e.get("note", 0)) > 0],
            key=lambda x: float(x.get("time_offset_s", 0.0)),
        )
        inner_notes = sorted(
            [e for e in events if e.get("voice") in ("counter_melody", "arpeggio") and int(e.get("note", 0)) > 0],
            key=lambda x: float(x.get("time_offset_s", 0.0)),
        )
        pedal_events = [e for e in events if e.get("pedal_cc64") is not None]

        # ------------------------------------------------------------------
        # Axis 1: Voice-Leading & Counterpoint Score (25 pts)
        # ------------------------------------------------------------------
        contrary_count = 0
        motion_pairs = 0
        parallel_5_8_penalties = 0
        pairs = min(len(mel_notes), len(bass_notes)) - 1
        for i in range(max(0, pairs)):
            dm = int(mel_notes[i + 1]["note"]) - int(mel_notes[i]["note"])
            db = int(bass_notes[i + 1]["note"]) - int(bass_notes[i]["note"])
            if dm != 0 or db != 0:
                motion_pairs += 1
                if (dm > 0 and db < 0) or (dm < 0 and db > 0):
                    contrary_count += 1
            interval_prev = abs(int(mel_notes[i]["note"]) - int(bass_notes[i]["note"])) % 12
            interval_next = abs(int(mel_notes[i + 1]["note"]) - int(bass_notes[i + 1]["note"])) % 12
            if dm != 0 and db != 0 and (dm * db > 0) and interval_prev in (0, 7) and interval_next == interval_prev:
                parallel_5_8_penalties += 1

        contrary_ratio = (contrary_count / max(1, motion_pairs)) if motion_pairs > 0 else 0.55
        # Inner voice independence (distinct pitch classes vs bass/melody)
        inner_pcs = len(set(int(e["note"]) % 12 for e in inner_notes))
        inner_bonus = min(5.0, inner_pcs * 1.1)
        # Stepwise resolution after leaps
        leap_res_bonus = 0.0
        for i in range(max(0, len(mel_notes) - 2)):
            leap = abs(int(mel_notes[i + 1]["note"]) - int(mel_notes[i]["note"]))
            next_step = abs(int(mel_notes[i + 2]["note"]) - int(mel_notes[i + 1]["note"]))
            if leap >= 5 and next_step <= 3:
                leap_res_bonus += 1.0
        leap_res_bonus = min(3.5, leap_res_bonus)

        vl_score = (
            10.5
            + contrary_ratio * 8.5
            + inner_bonus
            + leap_res_bonus
            - parallel_5_8_penalties * 3.0
        )
        vl_score = round(max(0.0, min(25.0, vl_score)), 2)

        # ------------------------------------------------------------------
        # Axis 2: Motivic Continuity & Flow Score (20 pts)
        # ------------------------------------------------------------------
        prev_mel = prev_bar_state.get("last_melody_note")
        seam_score = 7.0
        if prev_mel is not None and mel_notes:
            seam_leap = abs(int(mel_notes[0]["note"]) - int(prev_mel))
            if seam_leap <= 4:
                seam_score = 9.5
            elif seam_leap <= 7:
                seam_score = 8.2
            else:
                seam_score = max(3.5, 9.0 - (seam_leap - 7) * 0.8)

        # Check seamless timing (no dead gap at t=0 and legato overlap in 5..15ms)
        first_t = min((float(e.get("time_offset_s", 0.0)) for e in mel_notes), default=0.5)
        timing_continuity = 4.0 if first_t <= 0.05 else max(1.0, 4.0 - first_t * 4.0)
        transform_type = candidate.get("transform_type", "original")
        transform_bonus = 4.5 if transform_type in ("inversion", "augmentation", "diminution", "retrograde_inversion", "stretto") else 2.8
        legato_ms = float(genome_dict.get("legato_overlap_ms", 10.0))
        overlap_bonus = 2.0 if 5.0 <= legato_ms <= 15.0 else 0.8
        motivic_score = round(max(0.0, min(20.0, seam_score + timing_continuity + transform_bonus + overlap_bonus)), 2)

        # ------------------------------------------------------------------
        # Axis 3: Harmonic Originality & Tension Arc Score (20 pts)
        # ------------------------------------------------------------------
        chord_label = candidate.get("chord_label", "I")
        recent_repeats = history_chords[-4:].count(chord_label) if history_chords else 0
        novelty = float(candidate.get("harmonic_novelty", 0.78))
        arc_target_tension = float(story_arc_stage[2])
        cand_tension = float(candidate.get("tension", arc_target_tension))
        tension_match = max(0.0, 1.0 - abs(cand_tension - arc_target_tension) * 1.8)

        # Directives alignment (e.g. minor/chromatic bass, tension)
        dir_bonus = 0.0
        if directives.get("prefer_chromatic_bass") and float(genome_dict.get("bass_chromaticism", 0.3)) >= 0.50:
            dir_bonus += 1.6
        if directives.get("prefer_minor") and candidate.get("is_minor", False):
            dir_bonus += 1.4

        harm_score = (
            6.5
            + novelty * 6.5
            + tension_match * 5.2
            + dir_bonus
            - recent_repeats * 2.8
        )
        harm_score = round(max(0.0, min(20.0, harm_score)), 2)

        # ------------------------------------------------------------------
        # Axis 4: Expressive Articulation Score (15 pts)
        # ------------------------------------------------------------------
        durs = [float(e.get("duration_s", 0.5)) for e in events if int(e.get("note", 0)) > 0]
        has_staccato = any(0.035 <= d <= 0.095 for d in durs)
        has_legato_tenuto = any(d >= 0.45 for d in durs)
        contrast_bonus = 4.2 if (has_staccato and has_legato_tenuto) else (2.4 if has_legato_tenuto else 1.2)
        if directives.get("prefer_articulation_contrast") and has_staccato and has_legato_tenuto:
            contrast_bonus += 1.3

        # Half-pedal CC#64 (35..80) + pre-roll (30..50 ms)
        pedal_vals = [int(e["pedal_cc64"]) for e in pedal_events if int(e["pedal_cc64"]) > 25]
        in_half_pedal = any(35 <= p <= 80 for p in pedal_vals)
        preroll_ms = float(genome_dict.get("pedal_preroll_ms", 35.0))
        pedal_score = (4.2 if in_half_pedal else 2.0) + (2.5 if 30.0 <= preroll_ms <= 50.0 else 1.0)

        # Low release velocity 15..30 & dynamic variety
        vels = [int(e.get("velocity", 60)) for e in events if int(e.get("note", 0)) > 0 and not e.get("sympathetic_halo")]
        vel_range = (max(vels) - min(vels)) if len(vels) >= 2 else 0
        dyn_variety = min(4.2, vel_range / 7.5)
        artic_score = round(max(0.0, min(15.0, contrast_bonus + pedal_score + dyn_variety)), 2)

        # ------------------------------------------------------------------
        # Axis 5: Disklavier Physical Playability Score (20 pts)
        # ------------------------------------------------------------------
        phys = DisklavierPhysicsSimulator.evaluate_raw_events(events)
        playability_score = float(phys["physics_score_20"])
        # Bonus/check for linear velocity sweet spot (30..85)
        if vels:
            linear_ratio = sum(1 for v in vels if 30 <= v <= 85) / len(vels)
            if linear_ratio < 0.85:
                playability_score = max(0.0, playability_score - (1.0 - linear_ratio) * 5.0)
        playability_score = round(max(0.0, min(20.0, playability_score)), 2)

        total_score = round(vl_score + motivic_score + harm_score + artic_score + playability_score, 1)

        commentary_ja = self._build_commentary(
            total_score=total_score,
            vl_score=vl_score,
            motivic_score=motivic_score,
            harm_score=harm_score,
            artic_score=artic_score,
            playability_score=playability_score,
            contrary_ratio=contrary_ratio,
            chord_label=chord_label,
            transform_type=transform_type,
            has_staccato=has_staccato,
            legato_ms=legato_ms,
            pedal_vals=pedal_vals,
        )

        return {
            "total_score": total_score,
            "axes": {
                "voice_leading": vl_score,
                "motivic_continuity": motivic_score,
                "harmonic_originality": harm_score,
                "expressive_articulation": artic_score,
                "physical_playability": playability_score,
            },
            "contrary_motion_ratio": round(contrary_ratio, 2),
            "physics_report": phys,
            "commentary_ja": commentary_ja,
        }

    @staticmethod
    def _build_commentary(
        total_score: float,
        vl_score: float,
        motivic_score: float,
        harm_score: float,
        artic_score: float,
        playability_score: float,
        contrary_ratio: float,
        chord_label: str,
        transform_type: str,
        has_staccato: bool,
        legato_ms: float,
        pedal_vals: List[int],
    ) -> str:
        p_str = f"CC#64={pedal_vals[0]}" if pedal_vals else "Dry"
        art_note = "スタッカートとレガートの鮮明な対比" if has_staccato else "滑らかなカンタービレ・レガート"
        return (
            f"[{total_score:.1f}点] バス反行率{int(contrary_ratio*100)}%・"
            f"{chord_label}への展開と動機({transform_type})が有機的に接続。"
            f"{art_note}(Overlap+{legato_ms:.0f}ms, {p_str})。物理適性{playability_score:.1f}/20点。"
        )


class ParallelCandidateGenerator:
    """
    Generates 4 parallel multiverse candidate continuations (Branches A, B, C, D)
    for the next 2 bars (M_{t+1}, M_{t+2}), evaluates them with MultiObjectiveCritic,
    adopts the highest-scoring branch, and evolves the MusicalGenome population.
    """

    BRANCH_STRATEGIES = [
        ("Branch A", "Chromatic Mediant & Contrary Counterpoint", 0),
        ("Branch B", "Pivot Modulation & Imitative Stretto", 1),
        ("Branch C", "Impressionist Half-Pedal Planing", 2),
        ("Branch D", "Articulated Staccato-Legato Dialogue", 3),
    ]

    def __init__(self, critic: Optional[MultiObjectiveCritic] = None) -> None:
        self.critic = critic or MultiObjectiveCritic()
        self.rng = random.Random(42)
        self.elite_genome = MusicalGenome(generation=1)
        self.population: List[MusicalGenome] = [
            self.elite_genome,
            self.elite_genome.mutate(generation=1, rng=self.rng),
            self.elite_genome.mutate(generation=1, rng=self.rng),
        ]

    def _transform_motif(
        self, intervals: List[int], transform: str
    ) -> List[int]:
        if not intervals:
            return [0, 2, 4, 7]
        if transform == "inversion":
            return [-x for x in intervals]
        if transform == "retrograde_inversion":
            return [-x for x in reversed(intervals)]
        if transform == "augmentation":
            res: List[int] = []
            for x in intervals:
                res.extend([x, x])
            return res[: len(intervals) * 2]
        if transform == "diminution":
            return intervals[::2] or intervals
        if transform == "stretto":
            return intervals + [x + 2 for x in intervals[:3]]
        return list(intervals)

    def _generate_single_branch(
        self,
        branch_name: str,
        strategy_desc: str,
        strategy_idx: int,
        genome: MusicalGenome,
        motif: Dict[str, Any],
        prev_bar_state: Dict[str, Any],
        story_arc_stage: Tuple[str, str, float],
        bar_dur_s: float = 2.2,
        num_bars: int = 2,
        directives: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        directives = directives or {}
        tonic = int(motif.get("tonic_midi", 61))
        base_intervals = list(motif.get("interval_vector", [0, 4, 7, 11, 7, 4]))
        route = CHORD_MODULATION_ROUTES[
            (genome.generation + strategy_idx * 2) % len(CHORD_MODULATION_ROUTES)
        ]

        if directives.get("prefer_minor"):
            mode = [0, 2, 3, 5, 7, 8, 10]
            is_minor = True
        else:
            mode = list(route["mode"])
            is_minor = 3 in mode and 4 not in mode

        root_pc = (tonic + route["root_shift"]) % 12
        arc_name, _, arc_tension = story_arc_stage

        # Choose motivic transformation weighted by genome
        transforms = list(genome.motivic_transform_weights.keys())
        weights = list(genome.motivic_transform_weights.values())
        if strategy_idx == 1:
            transform = "stretto"
        elif strategy_idx == 3:
            transform = "diminution"
        else:
            transform = self.rng.choices(transforms, weights=weights, k=1)[0]

        transformed_ints = self._transform_motif(base_intervals, transform)

        events: List[Dict[str, Any]] = []
        last_mel = int(prev_bar_state.get("last_melody_note") or (tonic + 12))
        last_bass = int(prev_bar_state.get("last_bass_note") or (tonic - 12))

        legato_overlap_s = max(0.002, min(0.016, genome.legato_overlap_ms / 1000.0))
        pedal_preroll_s = max(0.010, min(0.050, genome.pedal_preroll_ms / 1000.0))
        half_pedal_cc = max(35, min(98, int(genome.half_pedal_depth)))
        vel_center = max(46, min(88, int(genome.velocity_center)))
        vel_span = max(8, min(24, int(genome.velocity_span)))

        for b in range(num_bars):
            t0 = round(b * bar_dur_s, 4)

            # 1. Syncopated / Anticipatory CC#64 Half-Pedal (-30 to -50 ms pre-roll at bar seam!)
            clear_t = round(max(0.0, t0 - pedal_preroll_s), 4) if (b > 0 or prev_bar_state.get("bar_index", 0) > 0) else 0.0
            if b > 0 or prev_bar_state.get("bar_index", 0) > 0:
                events.append(
                    {
                        "time_offset_s": clear_t,
                        "note": 0,
                        "velocity": 0,
                        "duration_s": 0.035,
                        "pedal_cc64": 20,
                        "voice": "pedal",
                    }
                )
            events.append(
                {
                    "time_offset_s": round(t0 + 0.045, 4),
                    "note": 0,
                    "velocity": 0,
                    "duration_s": round(bar_dur_s - 0.06, 4),
                    "pedal_cc64": half_pedal_cc,
                    "voice": "pedal",
                }
            )

            # 2. Melody & Bass in Deliberate Contrary Motion
            mel_count = 4 if strategy_idx in (1, 3) else 3
            mel_step = bar_dur_s / mel_count
            bass_step = bar_dur_s / 2.0

            mel_bar_pitches: List[int] = []
            for m_i in range(mel_count):
                raw_int = transformed_ints[(b * mel_count + m_i) % len(transformed_ints)]
                cand_pitch = _quantize_to_mode(tonic + 12 + raw_int, root_pc, mode)
                # Ensure smooth voice leading across the very first note of the bar seam!
                if b == 0 and m_i == 0:
                    while cand_pitch - last_mel > 6:
                        cand_pitch -= 12
                    while last_mel - cand_pitch > 6:
                        cand_pitch += 12
                    cand_pitch = max(55, min(84, _quantize_to_mode(cand_pitch, root_pc, mode)))

                mel_bar_pitches.append(cand_pitch)
                last_mel = cand_pitch

                # Expressive articulation contrast (staccato vs tenuto vs legato)
                use_staccato = (
                    (
                        (strategy_idx == 3 and genome.staccato_contrast_ratio >= 0.08)
                        or self.rng.random() < genome.staccato_contrast_ratio
                    )
                    and m_i == 1
                    and mel_count >= 4
                )
                if use_staccato:
                    # Staccato: 42 ms gate with >= 20 ms gap before next note
                    m_dur = 0.044
                else:
                    # Legato with +5..+15 ms overlap across note and bar seams!
                    m_dur = round(mel_step + legato_overlap_s, 4)

                m_vel = int(
                    max(
                        34,
                        min(
                            88,
                            vel_center + int(arc_tension * 10) + (4 if m_i == 0 else 0) + self.rng.randint(-4, 5),
                        ),
                    )
                )
                events.append(
                    {
                        "time_offset_s": round(t0 + m_i * mel_step, 4),
                        "note": cand_pitch,
                        "velocity": m_vel,
                        "duration_s": m_dur,
                        "release_velocity": 22,
                        "voice": "cello_motif" if (m_i % 2 == 0 and arc_name in ("Exposition", "Climax")) else "melody",
                        "source_tag": "virtuoso_cello" if (m_i % 2 == 0 and arc_name in ("Exposition", "Climax")) else "virtuoso",
                    }
                )

            # Bass voice moves in contrary motion to melody trend (with optional chromatic passing tones)
            mel_dir = mel_bar_pitches[-1] - mel_bar_pitches[0]
            for b_i in range(2):
                if b_i == 0:
                    bass_pitch = _quantize_to_mode(36 + root_pc, root_pc, mode)
                else:
                    if self.rng.random() < genome.bass_chromaticism:
                        # Chromatic passing step in opposite direction to melody!
                        step_dir = -1 if mel_dir >= 0 else 1
                        bass_pitch = max(31, min(52, last_bass + step_dir))
                    else:
                        step_dir = -2 if mel_dir >= 0 else 2
                        bass_pitch = _quantize_to_mode(max(31, min(52, last_bass + step_dir)), root_pc, mode)
                # Ensure contrary motion relative to melody when genome.contrary_motion_weight is high
                if b_i == 1 and self.rng.random() < genome.contrary_motion_weight:
                    if mel_dir > 0 and bass_pitch >= last_bass:
                        bass_pitch = max(31, last_bass - 2)
                    elif mel_dir < 0 and bass_pitch <= last_bass:
                        bass_pitch = min(52, last_bass + 2)
                last_bass = bass_pitch

                b_vel = int(max(36, min(78, vel_center - 8 + int(arc_tension * 8))))
                events.append(
                    {
                        "time_offset_s": round(t0 + b_i * bass_step, 4),
                        "note": bass_pitch,
                        "velocity": b_vel,
                        "duration_s": round(bass_step + legato_overlap_s, 4),
                        "release_velocity": 20,
                        "voice": "bass",
                        "source_tag": "virtuoso",
                    }
                )

            # 3. Independent Inner Counter-Melody & Flowing Arpeggio Cushion
            inner_count = max(2, int(round(2 + genome.counterpoint_density * 3)))
            inner_step = (bar_dur_s * 0.88) / inner_count
            for a_i in range(inner_count):
                # Counter-motion inner voice
                offset_semis = [7, 11, 14, 16, 19][a_i % 5]
                a_pitch = _quantize_to_mode(last_bass + 12 + offset_semis, root_pc, mode)
                a_pitch = max(48, min(79, a_pitch))
                a_vel = int(max(34, min(68, vel_center - vel_span // 2 + (a_i % 3) * 3)))
                events.append(
                    {
                        "time_offset_s": round(t0 + 0.04 + a_i * inner_step, 4),
                        "note": a_pitch,
                        "velocity": a_vel,
                        "duration_s": round(max(0.55, inner_step * 1.45), 4),
                        "release_velocity": 18,
                        "voice": "counter_melody" if a_i % 2 == 0 else "arpeggio",
                        "source_tag": "virtuoso",
                    }
                )

            # 4. Subtle non-colliding Velocity-1 Sympathetic Halo on high octave overtones
            if strategy_idx in (0, 2):
                halo_note = min(96, 72 + root_pc + 12)
                events.append(
                    {
                        "time_offset_s": round(t0, 4),
                        "note": halo_note,
                        "velocity": 1,
                        "duration_s": round(bar_dur_s * 0.92, 4),
                        "sympathetic_halo": True,
                        "voice": "sympathetic_halo",
                        "source_tag": "virtuoso",
                    }
                )

        return {
            "branch_id": branch_name,
            "strategy": strategy_desc,
            "chord_label": route["name"],
            "harmonic_novelty": route["novelty"],
            "transform_type": transform,
            "is_minor": is_minor,
            "tension": round(max(0.25, min(0.98, arc_tension + (strategy_idx - 1.5) * 0.05)), 2),
            "duration_s": round(num_bars * bar_dur_s, 3),
            "last_melody_note": last_mel,
            "last_bass_note": last_bass,
            "genome": genome.to_dict(),
            "events": events,
        }

    def generate_and_select(
        self,
        motif: Dict[str, Any],
        prev_bar_state: Dict[str, Any],
        story_arc_stage: Tuple[str, str, float],
        history_chords: List[str],
        bar_dur_s: float = 2.2,
        num_bars: int = 2,
        directives: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Generates 4 parallel future candidate branches, scores each with MultiObjectiveCritic,
        picks the highest-scoring branch as ADOPTED, and evolves the genome.
        """
        next_gen = self.elite_genome.generation + 1
        candidate_genomes = [
            self.elite_genome,
            self.elite_genome.mutate(directives_bias=directives, generation=next_gen, rng=self.rng),
            self.elite_genome.crossover(self.population[-1], generation=next_gen, rng=self.rng).mutate(
                directives_bias=directives, generation=next_gen, rng=self.rng
            ),
            self.population[0].mutate(directives_bias=directives, generation=next_gen, rng=self.rng),
        ]

        evaluated: List[Dict[str, Any]] = []
        for idx, (b_name, b_desc, s_idx) in enumerate(self.BRANCH_STRATEGIES):
            cand = self._generate_single_branch(
                branch_name=b_name,
                strategy_desc=b_desc,
                strategy_idx=s_idx,
                genome=candidate_genomes[idx],
                motif=motif,
                prev_bar_state=prev_bar_state,
                story_arc_stage=story_arc_stage,
                bar_dur_s=bar_dur_s,
                num_bars=num_bars,
                directives=directives,
            )
            score_res = self.critic.score_candidate(
                candidate=cand,
                prev_bar_state=prev_bar_state,
                story_arc_stage=story_arc_stage,
                history_chords=history_chords,
                directives=directives,
            )
            cand["evaluation"] = score_res
            cand["score"] = score_res["total_score"]
            cand["_genome_obj"] = candidate_genomes[idx]
            evaluated.append(cand)

        ranked = sorted(evaluated, key=lambda c: c["score"], reverse=True)
        for rank_idx, cand in enumerate(ranked):
            if rank_idx == 0:
                cand["status"] = "ADOPTED"
            elif rank_idx == 1:
                cand["status"] = "BACKUP"
            else:
                cand["status"] = "REJECTED"

        winner = ranked[0]
        runner_up = ranked[1]

        # Evolve elite genome from winner + runner-up
        winner_genome: MusicalGenome = winner["_genome_obj"]
        winner_genome.generation = next_gen
        self.elite_genome = winner_genome
        self.population = [
            winner_genome,
            runner_up["_genome_obj"],
            winner_genome.crossover(runner_up["_genome_obj"], generation=next_gen, rng=self.rng),
        ]

        mean_score = round(sum(c["score"] for c in ranked) / len(ranked), 1)
        # Strip internal object before returning JSON-friendly summary
        branches_summary = []
        for c in evaluated:
            branches_summary.append(
                {
                    "branch_id": c["branch_id"],
                    "strategy": c["strategy"],
                    "chord_label": c["chord_label"],
                    "transform_type": c["transform_type"],
                    "score": c["score"],
                    "status": c["status"],
                    "axes": c["evaluation"]["axes"],
                    "commentary_ja": c["evaluation"]["commentary_ja"],
                }
            )

        winner_clean = {k: v for k, v in winner.items() if k != "_genome_obj"}
        return {
            "generation": next_gen,
            "winner": winner_clean,
            "branches": branches_summary,
            "mean_candidate_score": mean_score,
            "best_score": winner["score"],
            "elite_genome": self.elite_genome.to_dict(),
        }
