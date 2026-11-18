"""
MusicalityCritic & MasterworkEvaluator:
6-Pillar Polyphonic & Acoustic Audit + 4-Pillar Masterwork Composition Evaluator.

6 Core Pillars (`subscores` / `subscores_pct`, 100 pts total):
  1. anti_scale (20 pts): blocks >=4-note monotonic stepwise scale runs & mechanical loops
  2. polyphony_harmony (20 pts): requires two-hand LH+RH voices & vertical consonant harmonies
  3. voice_leading (18 pts): rewards contrary/oblique motion & suspensions, penalizes parallel 5th/8ve
  4. spectral_clearance (18 pts): avoids minor-2nd & 100-600Hz harmonic mud against human notes
  5. rhythmic_diversity (12 pts): enforces duration contrast, rests/breathing, and velocity dynamics
  6. dual_pedal (12 pts): verifies continuous CC#64 Damper (repedaling/half-pedal) & CC#67 Una Corda

4 Masterwork Structural Pillars (`masterwork_scores` / `masterwork_pct`, 100 pts total):
  1. thematic_unity (25 pts): organic transformation/echo of Human Seed Motif intervals
  2. melodic_arch (25 pts): post-leap gap-fill recovery & single clear melodic apex
  3. harmonic_trajectory (25 pts): cadence function matches current Act (question half-cadence vs climax vs tonic resolution)
  4. inter_turn_continuity (25 pts): smooth voice-leading connection from previous phrase ending

Accelerated via PyTorch CUDA (RTX 3070) when available, with vectorized NumPy fallback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from src.stage.layer1_perception import midi_to_note_name

try:
    import torch

    _HAS_CUDA = bool(torch.cuda.is_available())
except Exception:  # pragma: no cover
    torch = None  # type: ignore[assignment]
    _HAS_CUDA = False


@dataclass
class MusicalityAuditReport:
    passed: bool
    total_score: float
    subscores: Dict[str, float]
    subscores_pct: Dict[str, int]
    failures: List[str]
    problem_note_indices: List[int]
    rejection_tag_ja: str
    rejection_detail: Dict[str, Any]
    accel_backend: str = "numpy_simd"
    masterwork_total: float = 88.0
    masterwork_scores: Dict[str, float] = field(default_factory=dict)
    masterwork_pct: Dict[str, int] = field(default_factory=dict)


def _onset_to_measure_beat(onset_s: float, beat_s: float = 0.5) -> Tuple[int, int]:
    total_beats = max(0.0, float(onset_s) / beat_s)
    measure = int(total_beats // 4) + 1
    beat = int(total_beats % 4) + 1
    return measure, beat


def _compute_overtone_clash_matrix(
    cand_pitches: np.ndarray,
    cand_onsets: np.ndarray,
    cand_durs: np.ndarray,
    human_pitches: np.ndarray,
) -> Tuple[np.ndarray, str]:
    """
    Computes spectral mud & minor-2nd clash penalties per candidate note against human fundamentals
    and lower harmonics (100-600 Hz mud band). Uses CUDA tensors when RTX 3070 is available.
    """
    if len(cand_pitches) == 0 or len(human_pitches) == 0:
        return np.zeros(0, dtype=np.float64), "numpy_simd"

    if _HAS_CUDA and torch is not None:
        try:
            cp = torch.tensor(cand_pitches, dtype=torch.float32, device="cuda").unsqueeze(1)
            hp = torch.tensor(human_pitches, dtype=torch.float32, device="cuda").unsqueeze(0)
            diff = torch.abs(cp - hp)
            min_diff, _ = torch.min(diff, dim=1)
            mud_mask = (cp.squeeze(1) >= 40.0) & (cp.squeeze(1) <= 66.0)
            m2_mask = (min_diff >= 0.5) & (min_diff <= 2.2)
            close_mud = (min_diff <= 4.2) & mud_mask
            pen = torch.where(m2_mask, 1.0, torch.where(close_mud, 0.55, 0.0))
            return pen.detach().cpu().numpy().astype(np.float64), "cuda_rtx3070"
        except Exception:
            pass

    cp = cand_pitches[:, None]
    hp = human_pitches[None, :]
    diff = np.abs(cp - hp)
    min_diff = np.min(diff, axis=1)
    mud_mask = (cand_pitches >= 40) & (cand_pitches <= 66)
    m2_mask = (min_diff >= 0.5) & (min_diff <= 2.2)
    close_mud = (min_diff <= 4.2) & mud_mask
    pen = np.where(m2_mask, 1.0, np.where(close_mud, 0.55, 0.0))
    return pen.astype(np.float64), "numpy_simd"


class MasterworkEvaluator:
    """
    Evaluates macro-musicality across 4 masterwork composition pillars (25 pts each = 100 pts):
    1. Thematic Unity: organic connection to Human Seed Motif intervals
    2. Melodic Arch & Leap Recovery: post-leap gap-fill + clear single apex
    3. Harmonic Trajectory & Cadential Logic: Act-appropriate cadence (half-cadence question vs tonic resolution)
    4. Inter-Turn Voice Continuity: smooth voice-leading bridge from previous ending
    """

    @staticmethod
    def _extract_rh_melody(note_evs: List[Dict[str, Any]]) -> List[int]:
        rh = [
            e
            for e in note_evs
            if str(e.get("voice", "")).lower() in ("rh_melody", "melody")
            or (str(e.get("hand", "")).lower() == "rh" and str(e.get("voice", "")).lower() != "rh_harmony")
            or (not e.get("hand") and int(e.get("pitch", 0)) >= 60)
        ]
        if len(rh) < 3:
            rh = [e for e in note_evs if int(e.get("pitch", 0)) >= 60]
        if not rh:
            rh = note_evs
        # Top note per onset window
        mel: List[int] = []
        last_t = -999.0
        for ev in rh:
            t = float(ev.get("onset_s", 0.0))
            p = int(ev["pitch"])
            if abs(t - last_t) < 0.05 and mel:
                if p > mel[-1]:
                    mel[-1] = p
            else:
                mel.append(p)
                last_t = t
        return mel

    @classmethod
    def evaluate(
        cls,
        note_evs: List[Dict[str, Any]],
        human_notes: List[Dict[str, Any]],
        tonal_center: str,
        scale_pcs: Tuple[int, List[int]],
        macro_plan: Optional[Any] = None,
    ) -> Tuple[Dict[str, float], Dict[str, int], float, List[str]]:
        mel = cls._extract_rh_melody(note_evs)
        if len(mel) < 2:
            zero = {"thematic_unity": 5.0, "melodic_arch": 5.0, "harmonic_trajectory": 5.0, "inter_turn_continuity": 5.0}
            pct = {k: 20 for k in zero}
            return zero, pct, 20.0, ["missing_thematic_unity"]

        mel_ivs = [mel[i] - mel[i - 1] for i in range(1, len(mel))]
        seed_ivs: List[int] = []
        phrase_func = "dialogic_sequence"
        bridge_pitch = mel[0]
        if macro_plan is not None:
            if isinstance(macro_plan, dict):
                seed_ivs = list(macro_plan.get("seed_motif_intervals") or [])
                phrase_func = str(macro_plan.get("phrase_function") or "dialogic_sequence")
                bridge_pitch = int(macro_plan.get("voice_leading_bridge_pitch") or mel[0])
            else:
                seed_ivs = list(getattr(macro_plan, "seed_motif_intervals", []) or [])
                phrase_func = str(getattr(macro_plan, "phrase_function", "dialogic_sequence"))
                bridge_pitch = int(getattr(macro_plan, "voice_leading_bridge_pitch", mel[0]))
        if not seed_ivs:
            hp = [int(n.get("pitch", n.get("note", 55))) for n in human_notes if int(n.get("pitch", n.get("note", 0))) > 0]
            seed_ivs = [hp[i] - hp[i - 1] for i in range(1, len(hp))] or [3, 4, -2]

        # 1. Thematic Unity (0..25)
        seed_signs = [1 if x > 0 else (-1 if x < 0 else 0) for x in seed_ivs[:4] if x != 0]
        mel_signs = [1 if x > 0 else (-1 if x < 0 else 0) for x in mel_ivs if x != 0]
        dir_match = 0.0
        if seed_signs and mel_signs:
            for off in range(max(1, len(mel_signs) - len(seed_signs) + 1)):
                seg = mel_signs[off : off + len(seed_signs)]
                same = sum(1 for a, b in zip(seg, seed_signs) if a == b)
                inv = sum(1 for a, b in zip(seg, seed_signs) if a == -b)
                dir_match = max(dir_match, max(same, inv) / max(1, len(seed_signs)))
        # Also check interval size echo (within +/-1 semitone of seed intervals or inverted)
        abs_seed = {abs(x) for x in seed_ivs if x != 0}
        iv_echo = sum(1 for x in mel_ivs if any(abs(abs(x) - s) <= 1 for s in abs_seed)) / max(1, len(mel_ivs))
        unity_score = round(min(25.0, 9.0 + dir_match * 9.0 + iv_echo * 7.0), 1)

        # 2. Melodic Arch & Post-Leap Gap-Fill (0..25)
        big_leaps = 0
        gap_filled = 0
        for i, lv in enumerate(mel_ivs[:-1]):
            if abs(lv) >= 5:
                big_leaps += 1
                nxt = mel_ivs[i + 1]
                if lv * nxt < 0 and 1 <= abs(nxt) <= 4:
                    gap_filled += 1
        gap_ratio = (gap_filled / big_leaps) if big_leaps > 0 else 0.85
        max_p = max(mel)
        apex_count = mel.count(max_p)
        apex_pos = mel.index(max_p) / max(1, len(mel) - 1)
        single_apex_bonus = 7.0 if (apex_count == 1 and 0.25 <= apex_pos <= 0.88) else (4.5 if apex_count <= 2 else 1.5)
        span_bonus = 6.0 if (max(mel) - min(mel)) >= 7 else 2.5
        arch_score = round(min(25.0, 4.0 + gap_ratio * 8.0 + single_apex_bonus + span_bonus), 1)

        # 3. Harmonic Trajectory & Cadential Logic (0..25)
        tonic_pc, _ = scale_pcs
        end_pc_dist = (mel[-1] - tonic_pc) % 12
        bass_notes = [int(e["pitch"]) for e in note_evs if str(e.get("hand", "")).lower() == "lh" or int(e["pitch"]) < 58]
        has_wide_lh = len(bass_notes) >= 2 and (max(bass_notes) - min(bass_notes)) >= 7

        if phrase_func == "question_half_cadence":
            # Open-ended question prefers ending on 5th (7), 2nd (2), 4th (5), or 7th (10/11), not square root-root unison
            cadence_ok = end_pc_dist in (2, 5, 7, 10, 11)
            traj_score = 22.0 if cadence_ok else 14.0
        elif phrase_func == "recapitulation_resolution":
            # Resolution prefers tonic (0), 3rd (3/4), or 5th (7) with serene Una Corda support
            cadence_ok = end_pc_dist in (0, 3, 4, 7)
            traj_score = 23.0 if cadence_ok else 15.0
        elif phrase_func == "polyphonic_climax":
            cadence_ok = max(mel) >= 76 and has_wide_lh
            traj_score = 23.5 if cadence_ok else 16.0
        else:  # dialogic_sequence
            cadence_ok = len(set(mel)) >= 5 and has_wide_lh
            traj_score = 22.5 if cadence_ok else 15.5
        if has_wide_lh:
            traj_score = min(25.0, traj_score + 2.0)

        # 4. Inter-Turn Voice Continuity (0..25)
        bridge_dist = abs(mel[0] - bridge_pitch)
        if bridge_dist <= 3:
            cont_score = 24.5
        elif bridge_dist <= 5:
            cont_score = 21.5
        elif bridge_dist <= 8:
            cont_score = 18.0
        elif bridge_dist <= 12:
            cont_score = 14.0
        else:
            cont_score = 9.0

        mw_scores = {
            "thematic_unity": unity_score,
            "melodic_arch": arch_score,
            "harmonic_trajectory": round(traj_score, 1),
            "inter_turn_continuity": cont_score,
        }
        mw_pct = {k: int(round((v / 25.0) * 100.0)) for k, v in mw_scores.items()}
        mw_total = round(sum(mw_scores.values()), 2)

        mw_failures: List[str] = []
        if macro_plan is not None:
            if unity_score < 12.0:
                mw_failures.append("missing_thematic_unity")
            if arch_score < 12.0:
                mw_failures.append("missing_gap_fill_arch")
            if cont_score < 11.0:
                mw_failures.append("voice_bridge_disconnect")

        return mw_scores, mw_pct, mw_total, mw_failures


class MusicalityCritic:
    """
    Strict 6-axis + 4-pillar Masterwork musicality auditor:
    1. anti_scale (20 pts): blocks >=4-note monotonic stepwise scale runs & mechanical arpeggio loops
    2. polyphony_harmony (20 pts): requires two-hand LH+RH voices & vertical consonant harmonies
    3. voice_leading (18 pts): rewards contrary/oblique motion & suspensions, penalizes parallel 5th/8ve & repeats
    4. spectral_clearance (18 pts): avoids minor-2nd & 100-600Hz harmonic mud against human notes
    5. rhythmic_diversity (12 pts): enforces duration contrast, rests/breathing, and velocity dynamics
    6. dual_pedal (12 pts): verifies continuous CC#64 Damper (repedaling/half-pedal) & CC#67 Una Corda
    """

    @staticmethod
    def _detect_monotonic_scale_indices(notes: List[Dict[str, Any]]) -> Tuple[List[int], str]:
        if len(notes) < 4:
            return [], ""

        upper = [(i, n) for i, n in enumerate(notes) if str(n.get("hand", "")).lower() == "rh" or int(n["pitch"]) >= 60]
        if len(upper) < 4:
            upper = list(enumerate(notes))

        melody_pts: List[Tuple[int, int]] = []
        last_t = -999.0
        for orig_idx, n in upper:
            t = float(n.get("onset_s", 0.0))
            p = int(n["pitch"])
            if abs(t - last_t) < 0.045 and melody_pts:
                if p > melody_pts[-1][1]:
                    melody_pts[-1] = (orig_idx, p)
            else:
                melody_pts.append((orig_idx, p))
                last_t = t

        run_dir = 0
        run_indices: List[int] = []
        if len(melody_pts) >= 4:
            run_indices = [melody_pts[0][0]]
            for k in range(1, len(melody_pts)):
                diff = melody_pts[k][1] - melody_pts[k - 1][1]
                if 1 <= abs(diff) <= 2:
                    d = 1 if diff > 0 else -1
                    if run_dir == 0 or d == run_dir:
                        run_dir = d
                        run_indices.append(melody_pts[k][0])
                        if len(run_indices) >= 4:
                            return run_indices[:4], "monotonic_scale_run"
                    else:
                        run_dir = d
                        run_indices = [melody_pts[k - 1][0], melody_pts[k][0]]
                else:
                    run_dir = 0
                    run_indices = [melody_pts[k][0]]

        if len(melody_pts) >= 6:
            ps = [p for _, p in melody_pts]
            if len(set(ps)) <= 3 and all(ps[i] != ps[i - 1] for i in range(1, len(ps))):
                return [idx for idx, _ in melody_pts[:4]], "mechanical_arpeggio_loop"

        return [], ""

    @staticmethod
    def _has_vertical_harmony(notes: List[Dict[str, Any]]) -> Tuple[bool, int, float]:
        """Counts vertical multi-note harmonic clusters (3rd, 5th, 6th, 7th, 9th, 10th)."""
        if len(notes) < 2:
            return False, 0, 0.0

        harm_events = 0
        rich_intervals = 0
        for i, ni in enumerate(notes):
            ti = float(ni.get("onset_s", 0.0))
            pi = int(ni["pitch"])
            sounding_others: List[int] = []
            for j, nj in enumerate(notes):
                if i == j:
                    continue
                tj = float(nj.get("onset_s", 0.0))
                dj = float(nj.get("duration_s", 0.35))
                if tj <= ti + 0.02 and (tj + dj) >= ti + 0.05:
                    sounding_others.append(int(nj["pitch"]))
            if sounding_others:
                harm_events += 1
                for po in sounding_others:
                    iv = abs(pi - po) % 12
                    if iv in (3, 4, 5, 7, 8, 9, 10, 11):
                        rich_intervals += 1

        has_lh = any(str(n.get("hand", "")).lower() == "lh" or int(n["pitch"]) < 60 for n in notes)
        has_rh = any(str(n.get("hand", "")).lower() == "rh" or int(n["pitch"]) >= 60 for n in notes)
        both_hands = has_lh and has_rh
        ratio = rich_intervals / max(1, len(notes))
        return both_hands and harm_events >= 2 and rich_intervals >= 2, harm_events, ratio

    @classmethod
    def audit_candidate(
        cls,
        events: List[Dict[str, Any]],
        human_notes: List[Dict[str, Any]],
        tonal_center: str = "D_dorian",
        scale_pcs: Optional[Tuple[int, List[int]]] = None,
        register_range: Optional[List[int]] = None,
        human_mode: str = "cello",
        macro_plan: Optional[Any] = None,
    ) -> MusicalityAuditReport:
        note_evs = [e for e in events if int(e.get("pitch", 0) or 0) > 0]
        cc64_evs = [e for e in events if int(e.get("pitch", 0) or 0) == 0 and int(e.get("control", 0) or 0) == 64]
        cc67_evs = [e for e in events if int(e.get("pitch", 0) or 0) == 0 and int(e.get("control", 0) or 0) == 67]

        if not note_evs:
            return MusicalityAuditReport(
                passed=False,
                total_score=0.0,
                subscores={k: 0.0 for k in ("anti_scale", "polyphony_harmony", "voice_leading", "spectral_clearance", "rhythmic_diversity", "dual_pedal")},
                subscores_pct={k: 0 for k in ("anti_scale", "polyphony_harmony", "voice_leading", "spectral_clearance", "rhythmic_diversity", "dual_pedal")},
                failures=["empty_notes"],
                problem_note_indices=[],
                rejection_tag_ja="[× 棄却: 音符欠如]",
                rejection_detail={"short_reason_ja": "音符が存在しない", "hud_badge_ja": "[× 棄却: 音符欠如]"},
            )

        note_evs = sorted(note_evs, key=lambda e: (float(e.get("onset_s", 0.0)), int(e["pitch"])))
        human_pitches = [
            int(n.get("pitch", n.get("note", 62)))
            for n in human_notes
            if int(n.get("pitch", n.get("note", 0))) > 0
        ] or [50, 53, 57]
        human_primary = human_pitches[-1]
        human_primary_name = midi_to_note_name(human_primary)

        failures: List[str] = []

        # 1. Anti-Scale Audit (0..20)
        scale_idxs, scale_kind = cls._detect_monotonic_scale_indices(note_evs)
        if scale_idxs:
            anti_scale_score = 3.0
            failures.append(scale_kind)
        else:
            rh_pitches = [int(n["pitch"]) for n in note_evs if str(n.get("hand", "")).lower() == "rh" or int(n["pitch"]) >= 60]
            if len(rh_pitches) >= 3:
                diffs = [abs(rh_pitches[i] - rh_pitches[i - 1]) for i in range(1, len(rh_pitches))]
                has_leap = any(d >= 3 for d in diffs)
                anti_scale_score = 20.0 if has_leap else 15.0
            else:
                anti_scale_score = 14.0

        # 2. Two-Hand Polyphony & Vertical Harmony (0..20)
        has_poly, harm_events, harm_ratio = cls._has_vertical_harmony(note_evs)
        has_lh = any(str(n.get("hand", "")).lower() == "lh" or int(n["pitch"]) < 60 for n in note_evs)
        has_rh = any(str(n.get("hand", "")).lower() == "rh" or int(n["pitch"]) >= 60 for n in note_evs)
        if not (has_lh and has_rh):
            polyphony_score = 4.0
            failures.append("monophonic_single_hand")
        elif not has_poly:
            polyphony_score = 8.0
            failures.append("missing_vertical_harmony")
        else:
            polyphony_score = min(20.0, 12.0 + min(8.0, harm_ratio * 3.5))

        # 3. Counterpoint & Voice Leading (0..18)
        pitches_seq = [int(n["pitch"]) for n in note_evs]
        rh_seq = MasterworkEvaluator._extract_rh_melody(note_evs)
        if not rh_seq:
            rh_seq = pitches_seq
        repeat_run = sum(1 for i in range(1, len(rh_seq)) if rh_seq[i] == rh_seq[i - 1])
        parallel_idxs: List[int] = []
        if len(human_pitches) >= 2 and len(rh_seq) >= 2:
            for i in range(1, min(len(rh_seq), len(human_pitches))):
                iv_ai = rh_seq[i] - rh_seq[i - 1]
                iv_hu = human_pitches[i] - human_pitches[i - 1]
                if iv_ai != 0 and iv_ai == iv_hu and abs(rh_seq[i] - human_pitches[i]) % 12 in (0, 7):
                    parallel_idxs.append(i)
        contrary_count = 0
        for i in range(1, min(len(rh_seq), len(human_pitches))):
            if (rh_seq[i] - rh_seq[i - 1]) * (human_pitches[i] - human_pitches[i - 1]) < 0:
                contrary_count += 1
        if parallel_idxs:
            failures.append("parallel_fifth_octave")
        if repeat_run >= 2:
            failures.append("excessive_pitch_repetition")

        vl_score = 11.0 + min(5.0, contrary_count * 1.6) + (2.0 if len(set(pitches_seq)) >= 5 else 0.0)
        vl_score -= min(7.0, len(parallel_idxs) * 3.5 + max(0, repeat_run - 2) * 2.5)
        voice_leading_score = max(0.0, min(18.0, vl_score))

        # 4. Spectral & Register Clearance (0..18) — GPU / NumPy accelerated
        cp_arr = np.array([int(n["pitch"]) for n in note_evs], dtype=np.float64)
        co_arr = np.array([float(n.get("onset_s", 0.0)) for n in note_evs], dtype=np.float64)
        cd_arr = np.array([float(n.get("duration_s", 0.35)) for n in note_evs], dtype=np.float64)
        hp_arr = np.array(human_pitches, dtype=np.float64)
        clash_vec, accel_backend = _compute_overtone_clash_matrix(cp_arr, co_arr, cd_arr, hp_arr)
        clash_idxs = [int(i) for i, v in enumerate(clash_vec) if v >= 0.5]
        m2_idxs = [int(i) for i, v in enumerate(clash_vec) if v >= 0.9]
        clash_ratio = float(np.mean(clash_vec)) if len(clash_vec) else 0.0

        if m2_idxs or clash_ratio > 0.25:
            failures.append("minor2_spectral_mud")
        spectral_score = max(0.0, min(18.0, 18.0 - clash_ratio * 28.0 - (4.0 if m2_idxs else 0.0)))

        eff_scale_pcs = scale_pcs or (2, [0, 2, 3, 5, 7, 9, 10])
        tonic_pc, valid_pcs = eff_scale_pcs
        in_mode = sum(1 for p in pitches_seq if (p - tonic_pc) % 12 in valid_pcs)
        if (in_mode / len(pitches_seq)) < 0.65:
            spectral_score = max(0.0, spectral_score - 3.5)

        # 5. Rhythmic, Rest & Agogic Diversity (0..12)
        durs = [round(float(n.get("duration_s", 0.35)), 2) for n in note_evs]
        vels = [int(n.get("velocity", 64)) for n in note_evs]
        dur_ratio = (max(durs) / max(0.05, min(durs))) if durs else 1.0
        distinct_durs = len(set(durs))
        vel_span = (max(vels) - min(vels)) if vels else 0
        if distinct_durs <= 1 and vel_span < 8:
            rhythm_score = 3.5
            failures.append("uniform_mechanical_rhythm")
        else:
            rhythm_score = min(
                12.0,
                5.0
                + (3.5 if dur_ratio >= 1.4 or distinct_durs >= 2 else 1.0)
                + (3.5 if vel_span >= 10 else 1.0),
            )

        # 6. Dual Pedal Phrasing Audit (0..12)
        cc64_vals = [int(e.get("value", 0)) for e in cc64_evs]
        cc67_vals = [int(e.get("value", 0)) for e in cc67_evs]
        has_cc64_curve = len(cc64_vals) >= 3 and (max(cc64_vals) - min(cc64_vals)) >= 20
        has_cc67_curve = len(cc67_vals) >= 2 and (max(cc67_vals) >= 15)
        if not has_cc64_curve or not has_cc67_curve:
            pedal_score = 4.0 if (has_cc64_curve or has_cc67_curve) else 1.0
            failures.append("missing_dual_pedal_envelope")
        else:
            has_repedal = any(
                cc64_vals[i] < cc64_vals[i - 1] - 25 and i + 1 < len(cc64_vals) and cc64_vals[i + 1] > cc64_vals[i] + 20
                for i in range(1, len(cc64_vals))
            )
            pedal_score = 12.0 if has_repedal else 10.5

        # Evaluate 4 Masterwork Structural Criteria
        mw_scores, mw_pct, mw_total, mw_failures = MasterworkEvaluator.evaluate(
            note_evs, human_notes, tonal_center, eff_scale_pcs, macro_plan=macro_plan
        )
        failures.extend(mw_failures)

        raw_6axis = (
            anti_scale_score
            + polyphony_score
            + voice_leading_score
            + spectral_score
            + rhythm_score
            + pedal_score
        )
        # Blend 6-axis score with masterwork structural coherence when macro_plan is active
        total = round((0.72 * raw_6axis + 0.28 * mw_total) if macro_plan is not None else raw_6axis, 2)

        subscores = {
            "anti_scale": round(anti_scale_score, 1),
            "polyphony_harmony": round(polyphony_score, 1),
            "voice_leading": round(voice_leading_score, 1),
            "spectral_clearance": round(spectral_score, 1),
            "rhythmic_diversity": round(rhythm_score, 1),
            "dual_pedal": round(pedal_score, 1),
        }
        subscores_pct = {
            "anti_scale": int(round((anti_scale_score / 20.0) * 100.0)),
            "polyphony_harmony": int(round((polyphony_score / 20.0) * 100.0)),
            "voice_leading": int(round((voice_leading_score / 18.0) * 100.0)),
            "spectral_clearance": int(round((spectral_score / 18.0) * 100.0)),
            "rhythmic_diversity": int(round((rhythm_score / 12.0) * 100.0)),
            "dual_pedal": int(round((pedal_score / 12.0) * 100.0)),
        }

        prob_idxs: List[int] = []
        if scale_idxs:
            prob_idxs = scale_idxs[:3]
            ev0 = note_evs[prob_idxs[0]]
            m_no, b_no = _onset_to_measure_beat(float(ev0.get("onset_s", 0.0)))
            tname = midi_to_note_name(int(ev0["pitch"]))
            pen = -max(35, int(round(100 - total)))
            reason_core = f"{m_no}小節{b_no}拍 {tname}から単調な順次音階進行（動機・和声構築の欠如 {pen}）"
            short_reason = "単調な順次音階で和声構築を欠く"
        elif m2_idxs or clash_idxs:
            prob_idxs = (m2_idxs or clash_idxs)[:2]
            ev0 = note_evs[prob_idxs[0]]
            m_no, b_no = _onset_to_measure_beat(float(ev0.get("onset_s", 0.0)))
            tp = int(ev0["pitch"])
            tname = midi_to_note_name(tp)
            hp_match = min(human_pitches, key=lambda hp: abs(hp - tp))
            hp_name = midi_to_note_name(hp_match)
            pen = -max(24, int(round(48 - spectral_score)))
            if m2_idxs:
                reason_core = f"{m_no}小節{b_no}拍 {hp_name}基音と{tname}が短2度衝突・低中域混濁 ({pen})"
                short_reason = f"{hp_name}と{tname}が短2度衝突し濁る"
            else:
                reason_core = f"{m_no}小節{b_no}拍 {hp_name}倍音帯と{tname}が密集干渉 ({pen})"
                short_reason = f"{hp_name}倍音帯で密集し濁る"
        elif not (has_lh and has_rh) or not has_poly:
            prob_idxs = [0, min(len(note_evs) - 1, 1)]
            ev0 = note_evs[prob_idxs[0]]
            m_no, b_no = _onset_to_measure_beat(float(ev0.get("onset_s", 0.0)))
            pen = -max(28, int(round(45 - polyphony_score)))
            reason_core = f"{m_no}小節{b_no}拍 単声配置で両手垂直和声が不足 ({pen})"
            short_reason = "単声で両手垂直和声が不足する"
        elif parallel_idxs or repeat_run >= 2:
            prob_idxs = [min(len(note_evs) - 1, (parallel_idxs or [1])[0])]
            ev0 = note_evs[prob_idxs[0]]
            m_no, b_no = _onset_to_measure_beat(float(ev0.get("onset_s", 0.0)))
            tname = midi_to_note_name(int(ev0["pitch"]))
            pen = -max(18, int(round(30 - voice_leading_score)))
            reason_core = f"{m_no}小節{b_no}拍 {tname}で並達・同音停滞が発生 ({pen})"
            short_reason = f"{tname}周辺で声部進行が停滞する"
        elif "uniform_mechanical_rhythm" in failures:
            prob_idxs = [len(note_evs) - 1]
            ev0 = note_evs[prob_idxs[0]]
            m_no, b_no = _onset_to_measure_beat(float(ev0.get("onset_s", 0.0)))
            pen = -18
            reason_core = f"{m_no}小節{b_no}拍 音価と強弱が均一で呼吸を欠く ({pen})"
            short_reason = "リズムと強弱が均一で平板"
        elif "missing_dual_pedal_envelope" in failures:
            prob_idxs = [len(note_evs) - 1]
            ev0 = note_evs[prob_idxs[0]]
            m_no, b_no = _onset_to_measure_beat(float(ev0.get("onset_s", 0.0)))
            pen = -16
            reason_core = f"{m_no}小節{b_no}拍 両ペダル連続踏み替え制御が欠落 ({pen})"
            short_reason = "両ペダル連続制御が不足する"
        elif mw_failures:
            prob_idxs = [0]
            ev0 = note_evs[0]
            m_no, b_no = _onset_to_measure_beat(float(ev0.get("onset_s", 0.0)))
            tname = midi_to_note_name(int(ev0["pitch"]))
            pen = -15
            reason_core = f"{m_no}小節{b_no}拍 {tname}で主題統一・声部接続が不足 ({pen})"
            short_reason = "主題統一または声部接続が弱い"
        else:
            prob_idxs = [len(note_evs) - 1]
            ev0 = note_evs[prob_idxs[0]]
            m_no, b_no = _onset_to_measure_beat(float(ev0.get("onset_s", 0.0)))
            tname = midi_to_note_name(int(ev0["pitch"]))
            pen = -max(8, int(round(100.0 - total)))
            reason_core = f"{m_no}小節{b_no}拍 {tname}の開離余白・内声推進が劣る ({pen})"
            short_reason = "開離余白と内声推進力が劣る"

        passed = (len(failures) == 0) and (total >= 78.0)
        rej_tag = f"[× 棄却: {reason_core}]"
        rej_detail = {
            "measure": m_no,
            "beat": b_no,
            "note_name": midi_to_note_name(int(note_evs[prob_idxs[0]]["pitch"])),
            "human_note_name": human_primary_name,
            "penalty_score": pen,
            "reason_core_ja": reason_core,
            "short_reason_ja": short_reason,
            "hud_badge_ja": rej_tag,
            "failures": list(failures),
        }

        return MusicalityAuditReport(
            passed=passed,
            total_score=total,
            subscores=subscores,
            subscores_pct=subscores_pct,
            failures=failures,
            problem_note_indices=prob_idxs,
            rejection_tag_ja=rej_tag,
            rejection_detail=rej_detail,
            accel_backend=accel_backend,
            masterwork_total=mw_total,
            masterwork_scores=mw_scores,
            masterwork_pct=mw_pct,
        )
