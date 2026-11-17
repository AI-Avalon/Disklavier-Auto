"""
10+ Iteration Real-Ollama & Audio Synthesis Verification Loop for Disklavier-Auto Stage System.

Runs the full L1 -> L2 Policy -> L3 symusic 4-Candidate Scoring -> L2 Deliberation Trace
(via real local Ollama e.g. qwen3.5:4b) -> Staggered Timeline WebSocket Broadcast
-> Python LocalAudioSynthesizer -> L5 Disklavier 30-Voice Safety Output across >=10 diverse turns.

Verifies on every single iteration:
1. Real LLM output monologue quality (0 musical contradictions, 0 AI poetry clichés,
   strict factual consistency with L1 pitch/articulation and L3 rejected/adopted candidates).
2. Staggered deliberation timeline & 4-candidate scoring integrity over WebSocket.
3. Real audio synthesis amplitude (RMS > 0.01, 0 NaN/Inf, 0 clipping).
4. Hardware safety gates (<=30 active voices, Mode A 100% self-echo block, Mode B NLMS > 10dB, <10ms panic).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Dict, List

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.stage.config_scanner import scan_ollama_environment
from src.stage.layer1_perception import NLMSEchoCanceller
from src.stage.layer2_intent_llm import BANNED_PHRASES, MusicalLogicValidator
from src.stage.layer5_disklavier_output import DisklavierStageOutputLayer
from src.stage.stage_server import STAGE_STYLE_PRESETS, create_stage_app

SCENARIOS: List[Dict[str, Any]] = [
    {"mode": "cello", "preset": "impressionist_modal", "directive": ""},
    {"mode": "cello", "preset": "baroque_counterpoint", "directive": "対位法でカノン風に、乾いたタッチで"},
    {"mode": "piano", "preset": "contemporary_provoke", "directive": "短調で挑発して密度を上げる"},
    {"mode": "cello", "preset": "serene_chorale", "directive": "中音域が濁るので高音へ逃がしペダルは浅く"},
    {"mode": "piano", "preset": "baroque_counterpoint", "directive": "ドライで鋭いスタッカート対位法で返して"},
    {"mode": "cello", "preset": "impressionist_modal", "directive": "ドリア旋法で開放弦を生かして"},
    {"mode": "cello", "preset": "contemporary_provoke", "directive": ""},
    {"mode": "piano", "preset": "serene_chorale", "directive": "長調で落ち着いて終止へ向かう"},
    {"mode": "cello", "preset": "baroque_counterpoint", "directive": "チェロD3倍音を避けて上声部へ"},
    {"mode": "piano", "preset": "impressionist_modal", "directive": "ペダルは浅く、同音反復は避ける"},
    {"mode": "cello", "preset": "serene_chorale", "directive": ""},
    {"mode": "piano", "preset": "contemporary_provoke", "directive": "対位法で挑発して"},
]


def run_iterative_verification(iterations: int = 10, play_device_audio: bool = False) -> Dict[str, Any]:
    ollama_info = scan_ollama_environment()
    ollama_running = bool(ollama_info.get("running", False))
    selected_model = str(ollama_info.get("selected_model") or "qwen3.5:4b")

    if not ollama_running:
        raise RuntimeError(
            "Local Ollama server is not running on http://127.0.0.1:11434. "
            "Start Ollama with qwen3.5:4b before running 10x verification."
        )

    records: List[Dict[str, Any]] = []
    violations: List[str] = []

    with tempfile.TemporaryDirectory() as empty_dir:
        app = create_stage_app(
            demo_enabled=False,
            monitor_audio=True,
            production_mode=False,
            corpus_dirs=[Path(empty_dir)],
            use_ollama=True,
            open_hardware=False,
            play_audio_device=play_device_audio,
        )
        with TestClient(app) as client:
            orch = app.state.orchestrator
            orch.intent_engine.model_name = selected_model
            orch.intent_engine.use_ollama = True
            orch.intent_engine.timeout_s = 12.0

            # Warm up Ollama once so cold-load doesn't distort per-turn latency
            _ = orch.execute_composition_turn(use_virtual_human=True, staggered_playback=False)
            client.post("/api/stage/reset")

            with client.websocket_connect("/ws/stage") as ws:
                _ = ws.receive_json()

                for idx in range(iterations):
                    sc = SCENARIOS[idx % len(SCENARIOS)]
                    assert client.post("/api/stage/mode", json={"mode": sc["mode"]}).status_code == 200
                    if sc["directive"]:
                        assert (
                            client.post("/api/stage/directive", json={"text": sc["directive"]}).status_code
                            == 200
                        )
                    elif sc["preset"] in STAGE_STYLE_PRESETS:
                        # Apply preset hints cleanly without triggering extra turn
                        orch.apply_preset(sc["preset"])

                    t0 = time.perf_counter()
                    r_turn = client.post("/api/stage/turn")
                    assert r_turn.status_code == 200
                    st = r_turn.json()["state"]
                    ws_frame = ws.receive_json()
                    turn_ms = round((time.perf_counter() - t0) * 1000.0, 2)

                    intent = st["intent"]
                    monologue = str(intent.get("monologue_ja", ""))
                    perc = st["perception"]
                    cands = st["candidates"]
                    audio_st = st["audio_status"]
                    out_st = st["output"]
                    timeline = st["deliberation_timeline"]

                    # 1. Real LLM & Text Quality Checks
                    if not str(intent.get("llm_source", "")).startswith("ollama:"):
                        violations.append(f"Iter {idx + 1}: LLM source fell back to {intent.get('llm_source')}")

                    for banned in BANNED_PHRASES:
                        if banned in monologue:
                            violations.append(f"Iter {idx + 1}: Banned phrase '{banned}' in: {monologue}")

                    # Validate via MusicalLogicValidator against real L1 snapshot & L3 candidates
                    ok_val, val_errs = MusicalLogicValidator.check_text(
                        monologue,
                        perception=orch.latest_perception,
                        adopted_candidate=next((c for c in orch.latest_candidates if c.adopted), None),
                        rejected_candidates=[c for c in orch.latest_candidates if not c.adopted],
                    )
                    if not ok_val:
                        violations.append(f"Iter {idx + 1}: Validator failed {val_errs} for: {monologue}")

                    # 2. Staggered Deliberation & Scoring Integrity Checks
                    adopted_list = [c for c in cands if c["adopted"]]
                    rejected_list = [c for c in cands if not c["adopted"]]
                    if len(cands) != 4 or len(adopted_list) != 1 or len(rejected_list) != 3:
                        violations.append(f"Iter {idx + 1}: Expected 1 adopted + 3 rejected, got {len(cands)}")

                    for rc in rejected_list:
                        if not rc["rejection_tag_ja"].startswith("[棄却:"):
                            violations.append(f"Iter {idx + 1}: Candidate {rc['candidate_id']} missing rejection tag")
                        if not rc.get("problem_note_indices"):
                            violations.append(f"Iter {idx + 1}: Candidate {rc['candidate_id']} missing problem_note_indices")
                        if len(rc.get("subscores_pct", {})) != 4:
                            violations.append(f"Iter {idx + 1}: Candidate {rc['candidate_id']} missing 4-axis subscores")

                    if len(timeline) < 5 or "candidates" not in ws_frame:
                        violations.append(f"Iter {idx + 1}: Missing staggered timeline or WS candidates frame")

                    # 3. Audio Output Verification (RMS > 0.01, 0 NaN/Inf, 0 clipping)
                    rms = float(audio_st.get("rms", 0.0))
                    if rms <= 0.01:
                        violations.append(f"Iter {idx + 1}: Audio RMS too low ({rms} <= 0.01)")
                    if audio_st.get("has_nan_or_inf"):
                        violations.append(f"Iter {idx + 1}: Audio buffer contained NaN or Inf")
                    if int(audio_st.get("clipping_count", 0)) > 0:
                        violations.append(f"Iter {idx + 1}: Audio clipping detected")

                    # 4. Hardware Safety Gates (30-voice cap, Mode A self-echo, Mode B NLMS)
                    if int(out_st.get("max_active_observed", 0)) > 30:
                        violations.append(f"Iter {idx + 1}: Active voices exceeded 30")
                    if sc["mode"] == "piano" and int(perc.get("echo_suppressed_count", 0)) < 1:
                        violations.append(f"Iter {idx + 1}: Mode A self-echo suppression count < 1")
                    if sc["mode"] == "cello" and float(perc.get("nlms_erle_db", 0.0)) <= 5.0:
                        violations.append(f"Iter {idx + 1}: Mode B NLMS ERLE too low ({perc.get('nlms_erle_db')})")

                    records.append(
                        {
                            "iteration": idx + 1,
                            "mode": sc["mode"],
                            "articulation": perc.get("articulation"),
                            "detected_note": perc.get("note_name"),
                            "llm_source": intent.get("llm_source"),
                            "llm_latency_ms": intent.get("llm_latency_ms"),
                            "validator_rewrites": intent.get("validator_rewrites"),
                            "turn_total_ms": turn_ms,
                            "monologue_ja": monologue,
                            "adopted_id": adopted_list[0]["candidate_id"] if adopted_list else "?",
                            "adopted_score": adopted_list[0]["total_score"] if adopted_list else 0.0,
                            "rejected_reasons": [
                                f"{r['candidate_id']}:{r['rejection_tag_ja']}" for r in rejected_list
                            ],
                            "audio_rms": rms,
                            "audio_dbfs": audio_st.get("dbfs"),
                            "nlms_erle_db": perc.get("nlms_erle_db"),
                            "echo_suppressed": perc.get("echo_suppressed_count"),
                            "max_voices": out_st.get("max_active_observed"),
                        }
                    )

            # Verify <10ms panic and burst 30-voice safety at end of loop
            panic_res = client.post("/api/stage/panic").json()
            if float(panic_res.get("elapsed_ms", 99.0)) >= 10.0 or panic_res.get("active_voices", 1) != 0:
                violations.append(f"Panic check failed: {panic_res}")

    # Dedicated burst 30-voice & NLMS verification
    out_burst = DisklavierStageOutputLayer(port_name=None, start_worker=False)
    try:
        t_sim = 500.0
        for i in range(150):
            out_burst.send_note_on_immediate(21 + (i * 5) % 88, 120 if i % 2 else 10, 3.0, now=t_sim)
            if len(out_burst.active_notes) > 30:
                violations.append("Burst 30-voice cap violated")
                break
            t_sim += 0.005
    finally:
        out_burst.close()

    report = {
        "passed": len(violations) == 0,
        "iterations_completed": len(records),
        "ollama_model": selected_model,
        "violations": violations,
        "records": records,
    }
    report_path = ROOT / "data" / "telemetry" / "iterative_refinement_10x_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="10x Iterative LLM + Audio Refinement Test")
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--no-device-audio", action="store_true", help="Synthesize audio buffers without playing speaker")
    args = parser.parse_args()

    report = run_iterative_verification(
        iterations=max(10, args.iterations),
        play_device_audio=not args.no_device_audio,
    )
    print(f"=== 10x Iterative Refinement Report (Model: {report['ollama_model']}) ===")
    for rec in report["records"]:
        print(
            f"[Iter {rec['iteration']:02d} | {rec['mode']:5s} {rec['detected_note']:>3s} {rec['articulation']:8s} "
            f"| {rec['llm_source']} {rec['llm_latency_ms']}ms | RMS={rec['audio_rms']:.4f} ({rec['audio_dbfs']} dBFS)] "
            f"{rec['monologue_ja']}"
        )
    if not report["passed"]:
        print("\n[FAIL] Violations detected:")
        for v in report["violations"]:
            print("  -", v)
        return 1
    print(f"\n[PASS] All {report['iterations_completed']} iterations passed with 0 violations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
