"""
Real-Time 6-Stage Logic Trace Builder & Live JSONL Logger (`src/stage/logic_trace.py`).

Captures every composition turn's complete internal reasoning chain for stage/field
verification (both in the live HUD Inspector tab and `logic_trace_live.jsonl` /
`recordings/session_*/logic_trace.jsonl`):
  1. `L1_PERCEPTION`   : detected pitch, F0, chord estimate, articulation, breath gauge, NLMS ERLE
  2. `TOKEN_ECON`      : raw vs compressed token counts & context excerpt sent to LLM
  3. `L2_LOGIC_CHAIN`  : musical hypothesis (act, function, voicing, pedal plan, factual memo)
  4. `L3_CANDIDATES`   : 4-candidate breakdown (consonance/polyphony/cantabile/pedal) & rejection reason
  5. `REALIZER_DIFF`   : before/after HarmonicBeautyRealizer monophonic & dissonance cleanup counts
  6. `TIMING_BUDGET`   : perception / network RTT / GPU inference / lookahead / total response ms
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
from typing import Any, Deque, Dict, List, Optional

from src.stage.layer1_perception import midi_to_note_name

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOGIC_TRACE_PATH = REPO_ROOT / "data" / "telemetry" / "logic_trace_live.jsonl"

_PHRASE_FUNC_HYPOTHESIS_JA: Dict[str, str] = {
    "question_half_cadence": "第1幕の問いかけとして半終止(V)を開き、高音域または対位声部で応答の余地を残す",
    "dialogic_sequence": "第2幕の展開として動機を近親調へ模進させ、内声の掛留解決で対話を推進する",
    "polyphonic_climax": "第3幕の頂点として保続低音上に3〜4声の対位法を積層し単一最高音へ到達する",
    "recapitulation_resolution": "第4幕の回想としてウナ・コルダで音色を抑制し、種子動機を再提示して完全終止する",
}

_TONAL_JA: Dict[str, str] = {
    "D_minor": "ニ短調",
    "Dm": "ニ短調",
    "D_dorian": "ニ・ドリア旋法",
    "C_major": "ハ長調",
    "A_minor": "イ短調",
    "F_major": "ヘ長調",
    "G_minor": "ト短調",
    "G_major": "ト長調",
    "Bb_major": "変ロ長調",
    "Eb_major": "変ホ長調",
    "C_minor": "ハ短調",
    "E_minor": "ホ短調",
}

_STANCE_JA: Dict[str, str] = {
    "imitate": "模倣・追従",
    "counterpoint": "対位法対話",
    "cadence": "終止接続",
    "provoke": "和声的展開",
    "echo_support": "持続和声支え",
    "resolve": "解決導出",
}

_PEDAL_JA: Dict[str, str] = {
    "half_clear": "ハーフペダル清澄踏み替え",
    "resonant": "豊かな共鳴保持",
    "dry": "ドライ（ペダル抑制）",
    "una_corda_veil": "ウナ・コルダ弱音ベール",
    "repedal_sync": "和声境界同期踏み替え",
}


def _format_voicing_ja(voicing: str) -> str:
    s = str(voicing or "LH_open5th_10th + RH_cantabile").strip()
    repls = (
        ("LH_root_5th_10th_arpeggio", "左手根音・5度・10度分散"),
        ("LH_open5th_10th", "左手開離5度・10度"),
        ("LH_open5th", "左手開離5度"),
        ("RH_cantabile_9th_spread", "右手歌謡9度開離"),
        ("RH_cantabile", "右手歌謡旋律"),
    )
    for src, dst in repls:
        s = s.replace(src, dst)
    return s.replace("_", " ")


def _estimate_detected_chord(
    human_notes: List[Dict[str, Any]],
    fallback_chords: Optional[List[str]] = None,
) -> str:
    """Estimates human phrase harmony from pitch classes without mutating pipeline state."""
    pitches = [
        int(n.get("pitch", n.get("note", 0)) or 0)
        for n in (human_notes or [])
        if int(n.get("pitch", n.get("note", 0)) or 0) > 0
    ]
    if not pitches:
        return (fallback_chords[0] if fallback_chords else "--")
    pcs = sorted({p % 12 for p in pitches})
    root_pc = pitches[0] % 12
    names = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
    root_name = names[root_pc]
    rel = {(pc - root_pc) % 12 for pc in pcs}
    if 3 in rel and 7 in rel:
        return f"{root_name}m7" if 10 in rel else f"{root_name}m"
    if 4 in rel and 7 in rel:
        return f"{root_name}M7" if 11 in rel else (f"{root_name}7" if 10 in rel else root_name)
    if 5 in rel and 7 in rel:
        return f"{root_name}sus4"
    if fallback_chords:
        return str(fallback_chords[0])
    return root_name


def _extract_attr(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def build_logic_trace(
    *,
    turn_id: int,
    mode: str,
    perception: Any,
    human_notes: List[Dict[str, Any]],
    intent: Any,
    candidates: List[Any],
    token_stats: Optional[Dict[str, Any]] = None,
    compact_prompt: str = "",
    llm_source: str = "rule_fallback",
    validator_rewrites: int = 0,
    timing_budget: Optional[Dict[str, Any]] = None,
    recorded_at_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Constructs the canonical 6-stage structured logic trace payload for one turn."""
    ts_iso = recorded_at_iso or datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    h_pitches = [
        int(n.get("pitch", n.get("note", 0)) or 0)
        for n in (human_notes or [])
        if int(n.get("pitch", n.get("note", 0)) or 0) > 0
    ]
    h_note_names = [midi_to_note_name(p) for p in h_pitches[-8:]]
    chords_plan = list(_extract_attr(intent, "chord_progression", []) or [])

    # 1. L1_PERCEPTION
    l1 = {
        "mode": str(_extract_attr(perception, "mode", mode) or mode),
        "note_name": str(_extract_attr(perception, "note_name", h_note_names[-1] if h_note_names else "--")),
        "midi_pitch": int(_extract_attr(perception, "midi_pitch", h_pitches[-1] if h_pitches else 0)),
        "f0_hz": round(float(_extract_attr(perception, "f0_hz", 0.0) or 0.0), 2),
        "detected_chord": _estimate_detected_chord(human_notes, chords_plan),
        "articulation": str(_extract_attr(perception, "articulation", "legato") or "legato"),
        "vibrato_cents": round(float(_extract_attr(perception, "vibrato_cents", 0.0) or 0.0), 1),
        "rms_dbfs": round(float(_extract_attr(perception, "rms_dbfs", -60.0) or -60.0), 1),
        "breath_gauge": round(float(_extract_attr(perception, "breath_gauge", 0.0) or 0.0), 3),
        "turn_ready": bool(_extract_attr(perception, "turn_ready", False)),
        "turn_reason": str(_extract_attr(perception, "turn_reason", "--") or "--"),
        "nlms_erle_db": round(float(_extract_attr(perception, "nlms_erle_db", 0.0) or 0.0), 2),
        "echo_suppressed_count": int(_extract_attr(perception, "echo_suppressed_count", 0) or 0),
        "human_notes_pitches": h_note_names,
    }

    # 2. TOKEN_ECON
    tstats = dict(token_stats or {})
    raw_tok = int(tstats.get("raw_tokens_est", 0) or 0)
    comp_tok = int(tstats.get("compressed_tokens_est", 0) or 0)
    saved_tok = int(tstats.get("tokens_saved", max(0, raw_tok - comp_tok)) or 0)
    ratio_pct = float(tstats.get("compression_ratio_pct", 0.0) or 0.0)
    preview = " / ".join(line.strip() for line in (compact_prompt or "").splitlines() if line.strip())
    if len(preview) > 420:
        preview = preview[:417] + "..."
    token_econ = {
        "raw_tokens_est": raw_tok,
        "compressed_tokens_est": comp_tok,
        "tokens_saved": saved_tok,
        "compression_ratio_pct": round(ratio_pct, 1),
        "active_interventions": list(tstats.get("active_interventions", []) or []),
        "compact_context_preview": preview or "(compact context unavailable)",
    }

    # 3. L2_LOGIC_CHAIN
    phrase_fn = str(_extract_attr(intent, "phrase_function", "question_half_cadence"))
    stance = str(_extract_attr(intent, "interaction_stance", "counterpoint"))
    tonal = str(_extract_attr(intent, "tonal_center", "D_minor"))
    voicing = str(_extract_attr(intent, "voicing_structure", "LH_open5th_10th + RH_cantabile"))
    pedal_strat = str(_extract_attr(intent, "pedal_strategy", "half_clear"))
    act_num = int(_extract_attr(intent, "act_number", 1) or 1)
    act_label = str(_extract_attr(intent, "act_label_ja", f"第{act_num}幕"))
    hyp_base = _PHRASE_FUNC_HYPOTHESIS_JA.get(phrase_fn, "対位法的応答と両手開離ボイシングで接続する")
    tonal_ja = _TONAL_JA.get(tonal, tonal.replace("_", " "))
    stance_ja = _STANCE_JA.get(stance, stance.replace("_", " "))
    voicing_ja = _format_voicing_ja(voicing)
    pedal_ja = _PEDAL_JA.get(pedal_strat, pedal_strat.replace("_", " "))
    hypothesis_ja = (
        f"{hyp_base}（調性: {tonal_ja} / {stance_ja} / {voicing_ja} / ペダル: {pedal_ja}）"
    )
    l2_logic = {
        "act_number": act_num,
        "act_label_ja": act_label,
        "phrase_function": phrase_fn,
        "hypothesis_ja": hypothesis_ja,
        "interaction_stance": stance,
        "tonal_center": tonal,
        "chord_progression": chords_plan,
        "voicing_structure": voicing,
        "rh_contour": str(_extract_attr(intent, "rh_contour", "")),
        "lh_motion": str(_extract_attr(intent, "lh_motion", "")),
        "voice_leading_bridge": str(_extract_attr(intent, "voice_leading_bridge", "--")),
        "pedal_strategy": pedal_strat,
        "damper_cc64_plan": list(_extract_attr(intent, "damper_cc64_plan", []) or []),
        "una_corda_cc67_plan": list(_extract_attr(intent, "una_corda_cc67_plan", []) or []),
        "llm_source": str(llm_source or "rule_fallback"),
        "monologue_ja": str(_extract_attr(intent, "monologue_ja", "")),
        "validator_rewrites": int(validator_rewrites or 0),
    }

    # 4. L3_CANDIDATES
    l3_list: List[Dict[str, Any]] = []
    adopted_obj: Any = None
    for cand in candidates or []:
        is_adopted = bool(_extract_attr(cand, "adopted", False))
        if is_adopted:
            adopted_obj = cand
        sub_pct = dict(_extract_attr(cand, "subscores_pct", {}) or {})
        beauty = dict(_extract_attr(cand, "beauty_summary", {}) or {})
        raw_score = float(_extract_attr(cand, "total_score", 0.0) or 0.0)
        score_pct = round(raw_score * 100.0 if raw_score <= 1.0 else raw_score, 1)
        rej_detail = dict(_extract_attr(cand, "rejection_detail", {}) or {})
        rej_reason = (
            "採用（両手声部・和声・ペダル監査通過）"
            if is_adopted
            else str(
                rej_detail.get("hud_badge_ja")
                or _extract_attr(cand, "rejection_tag_ja", "")
                or "スコア基準未達により棄却"
            )
        )
        l3_list.append(
            {
                "candidate_id": str(_extract_attr(cand, "candidate_id", "?")),
                "label_ja": str(_extract_attr(cand, "label_ja", "")),
                "adopted": is_adopted,
                "total_score_pct": score_pct,
                "pitch_range_str": str(_extract_attr(cand, "pitch_range_str", "--")),
                "scores": {
                    "consonance_pct": round(float(beauty.get("consonance_score", sub_pct.get("spectral_clearance", 0.0)) or 0.0), 1),
                    "polyphony_pct": int(sub_pct.get("polyphony_harmony", 0) or 0),
                    "cantabile_pct": int(sub_pct.get("voice_leading", 0) or 0),
                    "pedal_sync_pct": int(sub_pct.get("dual_pedal", 0) or 0),
                },
                "avg_polyphony": round(float(beauty.get("avg_polyphony", 0.0) or 0.0), 2),
                "rejection_reason_ja": rej_reason,
            }
        )

    # 5. REALIZER_DIFF
    adopted_diff = dict(_extract_attr(adopted_obj, "realizer_diff", {}) or {}) if adopted_obj is not None else {}
    if not adopted_diff and candidates:
        adopted_diff = dict(_extract_attr(candidates[0], "realizer_diff", {}) or {})
    realizer_diff = {
        "raw_note_count": int(adopted_diff.get("raw_note_count", 0) or 0),
        "realized_note_count": int(adopted_diff.get("realized_note_count", 0) or 0),
        "monophonic_gaps_before_pct": float(adopted_diff.get("monophonic_gaps_before_pct", 0.0) or 0.0),
        "monophonic_gaps_after_pct": float(adopted_diff.get("monophonic_gaps_after_pct", 0.0) or 0.0),
        "mono_gaps_fixed_pct": float(adopted_diff.get("mono_gaps_fixed_pct", 0.0) or 0.0),
        "vertical_clashes_before": int(adopted_diff.get("vertical_clashes_before", 0) or 0),
        "vertical_clashes_after": int(adopted_diff.get("vertical_clashes_after", 0) or 0),
        "dissonances_resolved": int(adopted_diff.get("dissonances_resolved", 0) or 0),
        "repair_pass_triggered": bool(adopted_diff.get("repair_pass_triggered", False)),
        "pedal_events_added": int(adopted_diff.get("pedal_events_added", 0) or 0),
        "summary_ja": str(adopted_diff.get("summary_ja") or "単音・不協和音発生 0件（和声監査クリア）"),
    }

    # 6. TIMING_BUDGET
    tb = dict(timing_budget or {})
    perc_ms = round(float(tb.get("perception_ms", 0.0) or 0.0), 2)
    rtt_ms = round(float(tb.get("network_rtt_ms", 0.0) or 0.0), 2)
    gpu_ms = round(float(tb.get("gpu_infer_ms", 0.0) or 0.0), 2)
    lookahead_ms = round(float(tb.get("lookahead_ms", 0.0) or 0.0), 2)
    total_ms = round(float(tb.get("total_response_ms", perc_ms + rtt_ms + gpu_ms) or 0.0), 2)
    timing = {
        "perception_ms": perc_ms,
        "network_rtt_ms": rtt_ms,
        "gpu_infer_ms": gpu_ms,
        "lookahead_ms": lookahead_ms,
        "total_response_ms": total_ms,
        "backend_source": str(tb.get("backend_source", "mac_local")),
        "speculative_hit": bool(tb.get("speculative_hit", False)),
    }

    return {
        "turn_id": int(turn_id),
        "recorded_at_iso": ts_iso,
        "perf_ts": round(time.perf_counter(), 4),
        "L1_PERCEPTION": l1,
        "TOKEN_ECON": token_econ,
        "L2_LOGIC_CHAIN": l2_logic,
        "L3_CANDIDATES": l3_list,
        "REALIZER_DIFF": realizer_diff,
        "TIMING_BUDGET": timing,
    }


class LogicTraceLogger:
    """Thread-safe bounded memory history + live JSONL logger for 6-stage logic traces."""

    def __init__(self, log_path: Optional[Path] = DEFAULT_LOGIC_TRACE_PATH, maxlen: int = 30) -> None:
        self.log_path = log_path
        self._lock = threading.Lock()
        self._history: Deque[Dict[str, Any]] = deque(maxlen=maxlen)
        self._latest: Optional[Dict[str, Any]] = None

    def record(self, trace: Dict[str, Any], write_disk: bool = True) -> Dict[str, Any]:
        with self._lock:
            self._latest = trace
            self._history.append(trace)
            if write_disk and self.log_path is not None:
                try:
                    self.log_path.parent.mkdir(parents=True, exist_ok=True)
                    with self.log_path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(trace, ensure_ascii=False) + "\n")
                except Exception:
                    pass
        return trace

    def get_latest(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._latest) if self._latest is not None else None

    def get_history(self, limit: int = 12) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._history)
        return items[-max(1, int(limit)) :]

    def clear(self) -> None:
        with self._lock:
            self._latest = None
            self._history.clear()
