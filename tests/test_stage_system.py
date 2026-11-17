"""
Comprehensive Verification Suite for Disklavier-Auto Stage Process Visualization System.

Verifies:
1. 30-Note Crash Prevention & <10ms Panic under 200 notes/sec burst.
2. Mode A Self-Strike Echo Loop Suppression (100% AI echo rejection, human pass-through).
3. Mode B Cello Acoustic Perception & 2ch NLMS Echo Cancellation (F0 + staccato/legato).
4. MusicalLogicValidator (rejecting musical contradictions & AI poetry clichés),
   4-Axis Scoring, Problem Note Coordinates, and Anti-AI Slop check.
5. Python Direct Audio Synthesizer (RMS > 0.01, 0 NaN/Inf, 0 clipping),
   Staggered Deliberation Timeline, Production vs Demo isolation, and WebSocket streaming.
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
    MusicalLogicValidator,
    sanitize_monologue_ja,
    validate_chamber_music_monologue,
)
from src.stage.layer3_candidate_composer import (
    MultipleCandidateComposer,
    ZeroConfigCorpusLoader,
)
from src.stage.layer5_disklavier_output import DisklavierStageOutputLayer
from src.stage.local_audio_synth import LocalAudioSynthesizer
from src.stage.stage_server import create_stage_app


def test_1_thirty_voice_crash_prevention_and_fast_panic() -> None:
    emitted: List[Dict[str, Any]] = []
    out = DisklavierStageOutputLayer(
        port_name=None,
        midi_sink=emitted.append,
        start_worker=False,
    )
    try:
        t_sim = 100.0
        for i in range(220):
            pitch = 21 + (i * 7) % 88
            vel = 5 if i % 10 == 0 else (127 if i % 10 == 1 else 78)
            out.send_note_on_immediate(pitch=pitch, velocity=vel, duration_s=4.0, now=t_sim)
            assert len(out.active_notes) <= 30, f"Active voices exceeded 30: {len(out.active_notes)}"
            t_sim += 0.005

        tel = out.get_telemetry()
        assert tel.max_active_observed <= 30
        assert tel.voice_steals > 0
        assert tel.velocity_clamps > 0

        note_ons = [e for e in emitted if e["type"] == "note_on"]
        assert all(18 <= e["velocity"] <= 112 for e in note_ons)

        assert out.send_pedal_cc64(50, now=200.000) is True
        assert out.send_pedal_cc64(52, now=200.050) is False
        assert out.send_pedal_cc64(64, now=200.015) is False
        assert out.send_pedal_cc64(64, now=200.060) is True

        emitted.clear()
        panic_res = out.panic()
        assert panic_res["active_voices"] == 0
        assert panic_res["elapsed_ms"] < 10.0
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

        for idx, p in enumerate(ai_pitches):
            echo_recv = ledger.filter_incoming_note(pitch=p, velocity=70, recv_ts=t0 + idx * 0.01 + 0.045)
            assert echo_recv is None

        human_pitches = [62, 65, 69]
        extracted = []
        for idx, hp in enumerate(human_pitches):
            res = ledger.filter_incoming_note(pitch=hp, velocity=82, recv_ts=t0 + 0.30 + idx * 0.15)
            if res is not None:
                extracted.append(res)
        assert [n["pitch"] for n in extracted] == human_pitches
        assert ledger.suppressed_count == len(ai_pitches)

        detector = TurnTakingDetector(silence_threshold_s=0.6)
        detector.push_note(62, 10.0)
        detector.push_note(65, 10.25)
        detector.push_note(69, 10.55)
        detector.push_note(67, 11.05)
        gauge, ready, _ = detector.evaluate(now=11.65)
        assert gauge >= 0.68 and ready is True
    finally:
        out.close()


def test_3_mode_b_cello_nlms_pitch_and_articulation() -> None:
    sr = 44100
    dur_s = 0.70
    t = np.arange(int(sr * dur_s), dtype=np.float64) / sr

    target_midi = 57
    f0_cello = 220.0
    phase = 2.0 * np.pi * f0_cello * np.cumsum(1.0 + 0.004 * np.sin(2.0 * np.pi * 5.4 * t)) / sr
    cello_core = (
        0.35 * np.sin(phase)
        + 0.16 * np.sin(2.0 * phase)
        + 0.08 * np.sin(3.0 * phase)
    )

    piano_ref = 0.30 * np.sin(2.0 * np.pi * 329.63 * t) + 0.22 * np.sin(2.0 * np.pi * 493.88 * t)

    nlms = NLMSEchoCanceller()
    pure_bleed_mic = 0.75 * piano_ref
    residual = nlms.process_block(pure_bleed_mic, piano_ref)
    in_tail_pow = float(np.mean(pure_bleed_mic[len(t) // 2 :] ** 2))
    out_tail_pow = float(np.mean(residual[len(t) // 2 :] ** 2))
    assert out_tail_pow < in_tail_pow * 0.05
    assert nlms.last_erle_db > 10.0

    perception = CelloAcousticPerception(sample_rate=sr)
    legato_env = np.minimum(1.0, t / 0.08)
    mic_legato = cello_core * legato_env + 0.65 * piano_ref
    snap_leg = perception.analyze_utterance(mic_legato, piano_ref, nlms=NLMSEchoCanceller())
    assert abs(snap_leg.midi_pitch - target_midi) <= 1
    assert snap_leg.articulation == "legato"

    stacc_env = np.zeros_like(t)
    for k in range(3):
        s = int(k * 0.22 * sr)
        e = min(len(t), s + int(0.075 * sr))
        seg = np.linspace(0.0, 1.0, e - s)
        stacc_env[s:e] = np.exp(-5.0 * seg) * np.minimum(1.0, seg * 16.0)
    mic_stacc = cello_core * stacc_env + 0.65 * piano_ref
    snap_stacc = perception.analyze_utterance(mic_stacc, piano_ref, nlms=NLMSEchoCanceller())
    assert abs(snap_stacc.midi_pitch - target_midi) <= 1
    assert snap_stacc.articulation == "staccato"


def test_4_multiple_candidates_pruning_and_musical_logic_validator() -> None:
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
            f0_hz=146.83,
            midi_pitch=50,
            note_name="D3",
            articulation="staccato",
            breath_gauge=0.9,
            turn_ready=True,
        )
        human_notes = [
            {"pitch": 50, "onset_s": 0.0, "duration_s": 0.12, "velocity": 76},
            {"pitch": 51, "onset_s": 0.2, "duration_s": 0.12, "velocity": 78},
            {"pitch": 53, "onset_s": 0.4, "duration_s": 0.14, "velocity": 82},
        ]
        hints = VerbalDirectiveRecognizer().parse_directive("対位法で挑発して、ペダルを浅く、乾いた音で")
        policy = engine.decide_policy(snap, human_notes, hints)
        turn = composer.compose_turn(human_notes, policy, human_mode="cello")
        intent = engine.finalize_deliberation_monologue(snap, human_notes, policy, turn, hints)

        adopted = [c for c in turn.candidates if c.adopted]
        rejected = [c for c in turn.candidates if not c.adopted]
        assert len(adopted) == 1
        assert len(rejected) == 3

        # Verify 4-axis percentages and exact problem note coordinates
        for rej in rejected:
            assert rej.rejection_tag_ja.startswith("[棄却:")
            assert len(rej.problem_note_indices) >= 1
            assert set(rej.subscores_pct.keys()) == {
                "harmonic_fit",
                "counterpoint_quality",
                "overtone_clearance",
                "rhythm_density",
            }
            assert "hud_badge_ja" in rej.rejection_detail

        # Verify MusicalLogicValidator passes factual post-L3 memo and blocks contradictions/clichés
        ok, errs = MusicalLogicValidator.check_text(
            intent.monologue_ja,
            perception=snap,
            adopted_candidate=adopted[0],
            rejected_candidates=rejected,
        )
        assert ok, f"Factual memo failed validation ({errs}): {intent.monologue_ja}"
        assert 40 <= len(intent.monologue_ja) <= 75
        for bad in BANNED_PHRASES:
            assert bad not in intent.monologue_ja

        # Ensure known contradiction & AI poetry samples are strictly rejected
        contradictions = [
            "チェロのD3が半音階で上昇し、長三度の空虚五度で緊張を高める。ダンパー操作で余白を残して解決を待つ。",
            "チェロの演奏が素晴らしい！あなたの魂に共鳴しました🎹✨",
            "スタッカートのレガートを無音の強奏でペダル無しの深い残響にするだろう。",
        ]
        for bad_txt in contradictions:
            assert not validate_chamber_music_monologue(bad_txt), f"Failed to block NG text: {bad_txt}"
            sanitized = sanitize_monologue_ja(bad_txt, intent.monologue_ja, perception=snap)
            assert validate_chamber_music_monologue(sanitized, perception=snap)

        # Verify Stage HUD adheres to Editorial Dark palette
        hud_html = Path("src/ui/stage_hud.html").read_text(encoding="utf-8")
        assert "#060608" in hud_html and "#EAE6DF" in hud_html and "#5A5852" in hud_html and "#C83E2B" in hud_html
        for forbidden in ("AI THINKING", "#00ffff", "#ff00ff", "素晴らしい演奏", "共鳴しました"):
            assert forbidden not in hud_html


def test_5_demo_audio_synth_staggered_timeline_and_production_separation() -> None:
    # 1. Verify LocalAudioSynthesizer RMS > 0.01, zero NaN/Inf, zero clipping
    synth = LocalAudioSynthesizer(enabled=True)
    human_notes = [
        {"pitch": 50, "onset_s": 0.0, "duration_s": 0.25, "velocity": 74},
        {"pitch": 53, "onset_s": 0.28, "duration_s": 0.25, "velocity": 78},
    ]
    adopted_evs = [
        {"pitch": 69, "onset_s": 0.0, "duration_s": 0.35, "velocity": 72},
        {"pitch": 74, "onset_s": 0.32, "duration_s": 0.45, "velocity": 76},
    ]
    rej_dummy = [{"events": [{"pitch": 51, "onset_s": 0.0, "duration_s": 0.2, "velocity": 70}]}]
    audio_stats = synth.play_turn_audio(
        human_notes=human_notes,
        human_mode="cello",
        human_articulation="legato",
        rejected_candidates=rej_dummy,
        adopted_events=adopted_evs,
        play_device=False,
    )
    assert audio_stats["rms"] > 0.01
    assert audio_stats["has_nan_or_inf"] is False
    assert audio_stats["clipping_count"] == 0
    synth.close()

    # 2. Verify Demo Mode (monitor_audio=True, staggered timeline, WS streaming, presets)
    with tempfile.TemporaryDirectory() as empty_dir:
        app = create_stage_app(
            demo_enabled=True,
            monitor_audio=True,
            production_mode=False,
            corpus_dirs=[Path(empty_dir)],
            use_ollama=False,
            open_hardware=False,
            play_audio_device=False,
        )
        with TestClient(app) as client:
            assert client.get("/").status_code == 200
            assert client.post("/api/stage/reset").json()["motif_nodes"] == 0

            with client.websocket_connect("/ws/stage") as ws:
                init_st = ws.receive_json()
                assert init_st["monitor_audio"] is True
                assert init_st["production_mode"] is False

                r_preset = client.post("/api/stage/preset", json={"preset": "baroque_counterpoint"})
                assert r_preset.status_code == 200
                st1 = r_preset.json()["state"]
                assert len(st1["deliberation_timeline"]) >= 5
                assert st1["audio_status"]["rms"] > 0.01
                assert len(st1["candidates"]) == 4

                assert client.post("/api/stage/mode", json={"mode": "piano"}).status_code == 200
                r_turn2 = client.post("/api/stage/turn")
                assert r_turn2.status_code == 200
                st2 = r_turn2.json()["state"]
                assert st2["mode"] == "piano"
                assert st2["perception"]["echo_suppressed_count"] >= 1

                r_cal = client.post("/api/stage/calibrate")
                assert r_cal.status_code == 200 and r_cal.json()["calibrated"] is True

                r_panic = client.post("/api/stage/panic")
                assert r_panic.status_code == 200 and r_panic.json()["active_voices"] == 0

    # 3. Verify Production Mode strictly disables virtual demo and python monitor synth
    with tempfile.TemporaryDirectory() as empty_dir:
        prod_app = create_stage_app(
            demo_enabled=True,  # Must be overridden to False in production mode
            monitor_audio=True,
            production_mode=True,
            corpus_dirs=[Path(empty_dir)],
            use_ollama=False,
            open_hardware=False,
            play_audio_device=False,
        )
        with TestClient(prod_app) as prod_client:
            st = prod_client.get("/api/stage/state").json()
            assert st["production_mode"] is True
            assert st["demo_enabled"] is False
            assert st["monitor_audio"] is False
