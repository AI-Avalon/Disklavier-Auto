"""
5-Gate Automated Verification Suite for Disklavier-Auto Stage Process Visualization System.

Verifies:
1. 30-Note Crash Prevention & <10ms Panic under 200 notes/sec burst.
2. Mode A Self-Strike Echo Loop Suppression (100% AI echo rejection, human pass-through).
3. Mode B Cello Acoustic Perception & 2ch NLMS Echo Cancellation (F0 + staccato/legato).
4. Multiple Candidate Generation (1 Adopted + >=2 Rejected with musical tags) & Anti-AI Slop check.
5. Zero-MIDI-File Fallback, Demo Mode Showcase & WebSocket /ws/stage end-to-end streaming.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

from fastapi.testclient import TestClient
import numpy as np

from src.stage.layer1_perception import (
    CelloAcousticPerception,
    EchoSuppressionLedger,
    NLMSEchoCanceller,
    PerceptionSnapshot,
    TurnTakingDetector,
    VerbalDirectiveRecognizer,
)
from src.stage.layer2_intent_llm import (
    BANNED_PHRASES,
    MusicalIntentEngine,
    sanitize_monologue_ja,
    validate_chamber_music_monologue,
)
from src.stage.layer3_candidate_composer import (
    MultipleCandidateComposer,
    ZeroConfigCorpusLoader,
)
from src.stage.layer5_disklavier_output import DisklavierStageOutputLayer
from src.stage.stage_server import create_stage_app


def test_1_thirty_voice_crash_prevention_and_fast_panic() -> None:
    emitted: List[Dict[str, Any]] = []
    out = DisklavierStageOutputLayer(
        port_name=None,
        midi_sink=emitted.append,
        start_worker=False,
    )
    try:
        # Simulate 200 notes/sec burst (220 notes spaced at 5ms across 88 piano keys)
        t_sim = 100.0
        for i in range(220):
            pitch = 21 + (i * 7) % 88
            vel = 5 if i % 10 == 0 else (127 if i % 10 == 1 else 78)
            out.send_note_on_immediate(pitch=pitch, velocity=vel, duration_s=4.0, now=t_sim)
            assert len(out.active_notes) <= 30, f"Active voices exceeded 30: {len(out.active_notes)}"
            t_sim += 0.005  # 200 notes/sec

        tel = out.get_telemetry()
        assert tel.max_active_observed <= 30
        assert tel.voice_steals > 0, "Expected voice stealing to engage during sustained 200/s burst"
        assert tel.velocity_clamps > 0, "Expected extreme velocities (5, 127) to be clamped to [18, 112]"

        # Verify emitted velocities stay within [18, 112]
        note_ons = [e for e in emitted if e["type"] == "note_on"]
        assert all(18 <= e["velocity"] <= 112 for e in note_ons)

        # Verify CC#64 thinning filter (min 40ms interval & +/-3 deadband)
        assert out.send_pedal_cc64(50, now=200.000) is True
        assert out.send_pedal_cc64(52, now=200.050) is False  # within +/-3 deadband
        assert out.send_pedal_cc64(64, now=200.015) is False  # within 40ms interval
        assert out.send_pedal_cc64(64, now=200.060) is True

        # Verify emergency panic clears all active notes and sends CC#123, CC#120, CC#64=0 in < 10ms
        emitted.clear()
        panic_res = out.panic()
        assert panic_res["active_voices"] == 0
        assert panic_res["elapsed_ms"] < 10.0, f"Panic took too long: {panic_res['elapsed_ms']}ms"
        ccs = {(e["control"], e["value"]) for e in emitted if e["type"] == "control_change"}
        assert (64, 0) in ccs and (123, 0) in ccs and (120, 0) in ccs
    finally:
        out.close()


def test_2_mode_a_self_echo_loop_suppression() -> None:
    ledger = EchoSuppressionLedger(min_lag_s=0.0, max_lag_s=0.250)
    out = DisklavierStageOutputLayer(
        port_name=None,
        echo_register_cb=ledger.register_sent_note,
        start_worker=False,
    )
    try:
        t0 = 1000.0
        ai_pitches = [48, 55, 60, 64, 72]
        for idx, p in enumerate(ai_pitches):
            assert out.send_note_on_immediate(pitch=p, velocity=74, duration_s=0.5, now=t0 + idx * 0.01)

        # Every AI note echoed back within Disklavier physical loopback window must be 100% dropped
        for idx, p in enumerate(ai_pitches):
            echo_recv = ledger.filter_incoming_note(pitch=p, velocity=70, recv_ts=t0 + idx * 0.01 + 0.045)
            assert echo_recv is None, f"Self-echo on pitch {p} leaked through ledger!"

        # Human pianist notes on distinct pitches or outside window must pass through 100%
        human_pitches = [62, 65, 69]
        extracted = []
        for idx, hp in enumerate(human_pitches):
            res = ledger.filter_incoming_note(pitch=hp, velocity=82, recv_ts=t0 + 0.30 + idx * 0.15)
            if res is not None:
                extracted.append(res)
        assert [n["pitch"] for n in extracted] == human_pitches
        assert ledger.suppressed_count == len(ai_pitches)

        # Verify Turn-Taking Detector (CC#67 explicit cue + IOI ritardando breathing)
        detector = TurnTakingDetector(silence_threshold_s=0.6)
        detector.push_note(62, 10.0)
        detector.push_note(65, 10.25)
        detector.push_note(69, 10.55)
        detector.push_note(67, 11.05)  # Ritardando IOI (0.25 -> 0.30 -> 0.50)
        gauge, ready, _ = detector.evaluate(now=11.65)
        assert gauge >= 0.68 and ready is True
    finally:
        out.close()


def test_3_mode_b_cello_nlms_pitch_and_articulation() -> None:
    sr = 44100
    dur_s = 0.70
    t = np.arange(int(sr * dur_s), dtype=np.float64) / sr

    # Target cello note A3 (MIDI 57, 220.0 Hz) with phase-integrated 5.4 Hz vibrato
    target_midi = 57
    f0_cello = 220.0
    phase = 2.0 * np.pi * f0_cello * np.cumsum(1.0 + 0.004 * np.sin(2.0 * np.pi * 5.4 * t)) / sr
    cello_core = (
        0.35 * np.sin(phase)
        + 0.16 * np.sin(2.0 * phase)
        + 0.08 * np.sin(3.0 * phase)
    )

    # Strong piano bleed reference (unrelated chord E4 + B4)
    piano_ref = 0.30 * np.sin(2.0 * np.pi * 329.63 * t) + 0.22 * np.sin(2.0 * np.pi * 493.88 * t)

    # Test 3a: Echo attenuation via NLMS on pure piano bleed frame
    nlms = NLMSEchoCanceller()
    pure_bleed_mic = 0.75 * piano_ref
    residual = nlms.process_block(pure_bleed_mic, piano_ref)
    # Check latter half after adaptive convergence
    in_tail_pow = float(np.mean(pure_bleed_mic[len(t) // 2 :] ** 2))
    out_tail_pow = float(np.mean(residual[len(t) // 2 :] ** 2))
    assert out_tail_pow < in_tail_pow * 0.05, "NLMS failed to attenuate piano bleed significantly"
    assert nlms.last_erle_db > 10.0

    # Test 3b: Legato Cello + Bleed -> F0 pitch & "legato" articulation
    perception = CelloAcousticPerception(sample_rate=sr)
    legato_env = np.minimum(1.0, t / 0.08)
    mic_legato = cello_core * legato_env + 0.65 * piano_ref
    snap_leg = perception.analyze_utterance(mic_legato, piano_ref, nlms=NLMSEchoCanceller())
    assert abs(snap_leg.midi_pitch - target_midi) <= 1, f"Expected MIDI ~{target_midi}, got {snap_leg.midi_pitch}"
    assert snap_leg.articulation == "legato", f"Expected legato, got {snap_leg.articulation}"

    # Test 3c: Staccato Cello bursts + Bleed -> F0 pitch & "staccato" articulation
    stacc_env = np.zeros_like(t)
    for k in range(3):
        s = int(k * 0.22 * sr)
        e = min(len(t), s + int(0.075 * sr))
        seg = np.linspace(0.0, 1.0, e - s)
        stacc_env[s:e] = np.exp(-5.0 * seg) * np.minimum(1.0, seg * 16.0)
    mic_stacc = cello_core * stacc_env + 0.65 * piano_ref
    snap_stacc = perception.analyze_utterance(mic_stacc, piano_ref, nlms=NLMSEchoCanceller())
    assert abs(snap_stacc.midi_pitch - target_midi) <= 1, f"Expected MIDI ~{target_midi}, got {snap_stacc.midi_pitch}"
    assert snap_stacc.articulation == "staccato", f"Expected staccato, got {snap_stacc.articulation}"


def test_4_multiple_candidates_pruning_and_anti_ai_slop() -> None:
    with tempfile.TemporaryDirectory() as empty_dir:
        loader = ZeroConfigCorpusLoader(search_dirs=[Path(empty_dir)])
        assert loader.loaded_file_count == 0
        assert loader.source_mode == "builtin_seed_dictionary"
        assert len(loader.patterns) >= 4

        engine = MusicalIntentEngine(use_ollama=False)
        composer = MultipleCandidateComposer(corpus_loader=loader)

        snap = PerceptionSnapshot(
            mode="cello",
            timestamp=time.perf_counter(),
            f0_hz=220.0,
            midi_pitch=57,
            note_name="A3",
            articulation="staccato",
            breath_gauge=0.9,
            turn_ready=True,
        )
        human_notes = [
            {"pitch": 57, "onset_s": 0.0, "duration_s": 0.12, "velocity": 76},
            {"pitch": 60, "onset_s": 0.2, "duration_s": 0.12, "velocity": 78},
            {"pitch": 64, "onset_s": 0.4, "duration_s": 0.14, "velocity": 82},
        ]
        hints = VerbalDirectiveRecognizer().parse_directive("対位法で挑発して、ペダルを浅く、乾いた音で")
        intent = engine.decide_intent(snap, human_notes, hints)

        # Anti-AI Slop validation on intent monologue
        assert validate_chamber_music_monologue(intent.monologue_ja), f"Invalid monologue: {intent.monologue_ja}"
        assert len(intent.monologue_ja) <= 60
        for bad in BANNED_PHRASES:
            assert bad not in intent.monologue_ja

        # Ensure sanitizer strips slops/flattery if LLM were to produce them
        slop_attempt = "素晴らしい演奏ですね！あなたのチェロの悲痛な叫びに共鳴しました🎹✨"
        sanitized = sanitize_monologue_ja(slop_attempt, intent.monologue_ja)
        assert validate_chamber_music_monologue(sanitized)
        assert "!" not in sanitized and "！" not in sanitized

        # Compose 4 candidates (1 adopted + 3 rejected with explicit rejection tags)
        turn = composer.compose_turn(human_notes, intent, human_mode="cello")
        assert len(turn.candidates) >= 3
        adopted = [c for c in turn.candidates if c.adopted]
        rejected = [c for c in turn.candidates if not c.adopted]
        assert len(adopted) == 1
        assert len(rejected) >= 2
        for rej in rejected:
            assert rej.rejection_tag_ja.startswith("[棄却:"), f"Missing rejection tag on {rej.candidate_id}"

        # Verify stage HUD HTML strictly obeys Stage Editorial UI rules
        hud_html = Path("src/ui/stage_hud.html").read_text(encoding="utf-8")
        assert "#060608" in hud_html and "#EAE6DF" in hud_html and "#5A5852" in hud_html and "#C83E2B" in hud_html
        for forbidden in ("AI THINKING", "#00ffff", "#ff00ff", "素晴らしい演奏", "共鳴しました"):
            assert forbidden not in hud_html


def test_5_demo_mode_websocket_and_zero_midi_fallback() -> None:
    with tempfile.TemporaryDirectory() as empty_dir:
        app = create_stage_app(
            demo_enabled=True,
            corpus_dirs=[Path(empty_dir)],
            use_ollama=False,
            open_hardware=False,
        )
        with TestClient(app) as client:
            # 1. Serve Stage HUD
            r_html = client.get("/")
            assert r_html.status_code == 200
            assert "Zone C" in r_html.text

            # 2. Reset Motif Memory Graph to 0 nodes (curtain rise state)
            r_reset = client.post("/api/stage/reset")
            assert r_reset.status_code == 200
            assert r_reset.json()["motif_nodes"] == 0

            # 3. Connect WebSocket & stream demo turns in Mode B and Mode A without any MIDI files
            with client.websocket_connect("/ws/stage") as ws:
                initial_payload = ws.receive_json()
                assert initial_payload["corpus_source"] == "builtin_seed_dictionary"
                assert initial_payload["motif_graph"]["nodes"] == []

                # Trigger Mode B (Cello) turn
                r_turn1 = client.post("/api/stage/turn")
                assert r_turn1.status_code == 200
                st1 = r_turn1.json()["state"]
                assert st1["mode"] == "cello"
                assert len(st1["candidates"]) == 4
                assert sum(1 for c in st1["candidates"] if c["adopted"]) == 1
                assert len(st1["motif_graph"]["nodes"]) == 2

                # Switch to Mode A (Piano) & send MC directive & trigger turn
                assert client.post("/api/stage/mode", json={"mode": "piano"}).status_code == 200
                assert client.post("/api/stage/directive", json={"text": "短調で発展させて"}).status_code == 200
                r_turn2 = client.post("/api/stage/turn")
                assert r_turn2.status_code == 200
                st2 = r_turn2.json()["state"]
                assert st2["mode"] == "piano"
                assert st2["perception"]["echo_suppressed_count"] >= 1
                assert len(st2["motif_graph"]["nodes"]) == 4
                assert len(st2["motif_graph"]["edges"]) >= 2

                # Receive live WebSocket broadcast frame reflecting updated turn
                ws_msg = ws.receive_json()
                assert "candidates" in ws_msg and "output" in ws_msg

                # Emergency Panic check
                r_panic = client.post("/api/stage/panic")
                assert r_panic.status_code == 200
                assert r_panic.json()["active_voices"] == 0
