"""
TokenEconomizer Integration Layer (inspired by microsoft/TokenEconomizer).

Optimizes local LLM (Ollama / RTX 3070) context density without sacrificing musical reasoning:
1. Micro-Skills & Acoustic Fact Injection:
   Encodes human phrase leaps, harmonic tension, spectral clearance bands, rhythmic momentum,
   and decay characteristics into high-density symbolic tokens.
2. Context & Voicing Schema Pruning:
   Strips redundant verbose prose/JSON keys from corpus voicing matches and dialogue history,
   measuring token reduction (`raw_tokens_est` -> `compressed_tokens_est`, `compression_ratio_pct`).
3. Failure-Loop Intervention Ledger:
   Records recent MusicalityCritic rejections & MusicalLogicValidator violations and injects
   compact intervention directives to prevent repeated failure loops across turns.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import math
from typing import Any, Deque, Dict, List, Optional

from src.stage.layer1_perception import PerceptionSnapshot, midi_to_note_name


@dataclass
class EconomizedContext:
    compact_prompt: str
    raw_prompt_est: str
    raw_tokens_est: int
    compressed_tokens_est: int
    compression_ratio_pct: float
    tokens_saved: int
    active_interventions: List[str]
    human_summary: Dict[str, Any]


def _estimate_tokens(text: str) -> int:
    """Approximates subword token count accurately for mixed Japanese/ASCII prompts."""
    if not text:
        return 0
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    non_ascii = len(text) - ascii_chars
    # ~3.6 ASCII chars/token, ~1.25 Japanese chars/token
    return max(1, int(math.ceil(ascii_chars / 3.6 + non_ascii / 1.25)))


class TokenEconomizer:
    """
    Black-box experience-driven context compressor & failure-loop guard for live music LLM calls.
    """

    def __init__(self, history_window: int = 4) -> None:
        self.history_window = history_window
        self.harmonic_history: Deque[str] = deque(maxlen=history_window)
        self.failure_ledger: Deque[str] = deque(maxlen=8)
        self.intervention_counts: Dict[str, int] = {}
        self.total_raw_tokens: int = 0
        self.total_compressed_tokens: int = 0
        self.last_stats: Dict[str, Any] = {
            "raw_tokens_est": 0,
            "compressed_tokens_est": 0,
            "compression_ratio_pct": 0.0,
            "tokens_saved": 0,
            "active_interventions": [],
        }

    def record_failure(self, failure_code: str) -> None:
        code = str(failure_code or "").strip()
        if not code:
            return
        self.failure_ledger.append(code)
        self.intervention_counts[code] = self.intervention_counts.get(code, 0) + 1

    def record_turn_outcome(self, tonal_center: str, chords: List[str], failures: List[str]) -> None:
        chord_str = "-".join(chords[:4]) if chords else "--"
        self.harmonic_history.append(f"{tonal_center}:{chord_str}")
        for f in failures:
            self.record_failure(f)

    @staticmethod
    def extract_human_phrase_metrics(
        perception: PerceptionSnapshot,
        human_notes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        pitches = [
            int(n.get("pitch", n.get("note", 0)))
            for n in human_notes
            if int(n.get("pitch", n.get("note", 0))) > 0
        ]
        if not pitches and perception.midi_pitch > 0:
            pitches = [int(perception.midi_pitch)]
        if not pitches:
            pitches = [50]

        leaps = [pitches[i] - pitches[i - 1] for i in range(1, len(pitches))]
        max_leap = max((abs(x) for x in leaps), default=0)
        span = max(pitches) - min(pitches)
        pitch_names = [midi_to_note_name(p) for p in pitches[-6:]]

        # Estimate harmonic tension from semitone/tritone content & register density
        dissonant_steps = sum(1 for iv in leaps if abs(iv) % 12 in (1, 2, 6, 11))
        tension = min(1.0, round(0.25 + 0.18 * dissonant_steps + 0.03 * max_leap, 2))

        # Overtone clearance mud band (100-600 Hz fundamental/2nd harmonic range)
        low_p = min(pitches)
        high_p = max(pitches)
        clear_avoid_lo = max(36, low_p - 2)
        clear_avoid_hi = min(72, high_p + 12)

        durs = [float(n.get("duration_s", 0.25)) for n in human_notes] or [0.25]
        avg_dur = sum(durs) / len(durs)
        momentum = round(min(1.0, 0.22 / max(0.08, avg_dur)), 2)

        return {
            "pitches": pitches,
            "pitches_str": "→".join(pitch_names),
            "leaps": leaps[-5:],
            "max_leap": max_leap,
            "span": span,
            "tension": tension,
            "momentum": momentum,
            "articulation": perception.articulation or "legato",
            "avoid_band_midi": [clear_avoid_lo, clear_avoid_hi],
            "avoid_band_str": f"{midi_to_note_name(clear_avoid_lo)}..{midi_to_note_name(clear_avoid_hi)}",
        }

    def build_economized_context(
        self,
        perception: PerceptionSnapshot,
        human_notes: List[Dict[str, Any]],
        corpus_refs: List[Dict[str, Any]],
        directive_hints: Optional[Dict[str, Any]] = None,
        candidate_summaries: Optional[List[Dict[str, Any]]] = None,
    ) -> EconomizedContext:
        h = self.extract_human_phrase_metrics(perception, human_notes)
        hints = directive_hints or {}

        # Verbose baseline prompt (for transparent compression measurement)
        raw_obj = {
            "human_perception": {
                "instrument": perception.mode,
                "articulation": h["articulation"],
                "detected_notes": h["pitches_str"],
                "interval_leaps": h["leaps"],
                "harmonic_tension": h["tension"],
                "rhythmic_momentum": h["momentum"],
                "avoid_overtone_clash_range": h["avoid_band_str"],
                "vibrato_cents": perception.vibrato_cents,
                "rms_dbfs": perception.rms_dbfs,
            },
            "recent_harmonic_history": list(self.harmonic_history),
            "corpus_voicing_references": [
                {
                    "id": r.get("id"),
                    "rh_intervals": r.get("rh_intervals", r.get("intervals", [])),
                    "lh_bass_intervals": r.get("lh_intervals", [0, 7]),
                    "spread_semitones": r.get("voicing_spread", 19),
                    "pedal_curve": r.get("pedal_curve_summary", "delayed_half_repedal"),
                }
                for r in corpus_refs[:3]
            ],
            "mc_directives": hints,
            "recent_failures_to_avoid": list(self.failure_ledger)[-4:],
            "candidates": candidate_summaries or [],
        }
        raw_prompt_est = json.dumps(raw_obj, ensure_ascii=False)

        # Micro-skill & pruned symbolic prompt
        leap_sig = ",".join(f"{x:+d}" for x in h["leaps"]) or "0"
        hist_sig = "|".join(self.harmonic_history) if self.harmonic_history else "INIT"
        ref_tokens = []
        for r in corpus_refs[:3]:
            rh = ",".join(str(x) for x in (r.get("rh_intervals") or r.get("intervals") or [0, 4, 7])[:5])
            lh = ",".join(str(x) for x in (r.get("lh_intervals") or [0, 7])[:3])
            sp = int(r.get("voicing_spread", 19))
            ref_tokens.append(f"V(lh=[{lh}],rh=[{rh}],sp={sp})")
        ref_str = ";".join(ref_tokens) or "V(lh=[0,7],rh=[0,4,9,14],sp=19)"

        interventions: List[str] = ["no_scale_run>=4", "require_2hand_polyphony", "dual_pedal_cc64_cc67"]
        if any("minor2" in f or "clash" in f for f in self.failure_ledger):
            interventions.append(f"clear_{h['avoid_band_str']}")
        if any("rhythm" in f or "uniform" in f for f in self.failure_ledger):
            interventions.append("vary_rhythm_rests")

        cand_str = ""
        if candidate_summaries:
            parts = []
            for cs in candidate_summaries:
                cid = cs.get("id", "?")
                sc = cs.get("score", 0)
                rg = cs.get("range", "--")
                rs = cs.get("reason", "ok")
                parts.append(f"{cid}:{rg}({sc})[{rs}]")
            cand_str = "\nCAND=" + " | ".join(parts)

        mc_raw = str(hints.get("raw_text") or "").strip()
        mc_str = f" MC={mc_raw}" if mc_raw else ""

        compact_prompt = (
            f"IN[{perception.mode}|{h['articulation']}|N={h['pitches_str']}|IV={leap_sig}|"
            f"T={h['tension']}|M={h['momentum']}|AVOID={h['avoid_band_str']}]{mc_str}\n"
            f"HIST={hist_sig}\n"
            f"CORPUS={ref_str}\n"
            f"RULE={','.join(interventions)}"
            f"{cand_str}"
        )

        raw_tok = _estimate_tokens(raw_prompt_est)
        comp_tok = _estimate_tokens(compact_prompt)
        saved = max(0, raw_tok - comp_tok)
        ratio = round((saved / max(1, raw_tok)) * 100.0, 1)

        self.total_raw_tokens += raw_tok
        self.total_compressed_tokens += comp_tok
        self.last_stats = {
            "raw_tokens_est": raw_tok,
            "compressed_tokens_est": comp_tok,
            "compression_ratio_pct": ratio,
            "tokens_saved": saved,
            "cumulative_saved": max(0, self.total_raw_tokens - self.total_compressed_tokens),
            "active_interventions": interventions,
        }

        return EconomizedContext(
            compact_prompt=compact_prompt,
            raw_prompt_est=raw_prompt_est,
            raw_tokens_est=raw_tok,
            compressed_tokens_est=comp_tok,
            compression_ratio_pct=ratio,
            tokens_saved=saved,
            active_interventions=interventions,
            human_summary=h,
        )
