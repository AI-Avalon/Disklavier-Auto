"""
Comprehensive Verification Suite for Disklavier-Auto Rebuilt Stage System.

Verifies:
1. 30-Note Crash Prevention, Dual-Pedal (CC#64/CC#67) Thinning, & <10ms Panic.
2. Mode A Self-Strike Echo Suppression & AI CC#67 Turn-Detector Isolation.
3. Mode B Cello Acoustic Perception & 2ch NLMS Echo Cancellation.
4. TokenEconomizer, MusicalityCritic (Anti-Scale / Two-Hand Polyphony / 6-Axis Audit),
   and MusicalLogicValidator (blocking contradictions, AI poetry, and "〇〇風" presets).
5. Dual-Pedal Physical Audio Simulation (CC#64 damper tail + CC#67 upper partial veil),
   /favicon.ico 200 OK (Stage & Mission Control), pure-ASCII .bat integrity,
   Obsidian Stage Turn Logging, and Production vs Demo isolation.
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
from src.stage.musicality_critic import MusicalityCritic
from src.stage.stage_server import create_stage_app
from src.stage.token_economizer import TokenEconomizer


def test_1_thirty_voice_crash_prevention_dual_pedal_and_fast_panic() -> None:
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

        # CC#64 Damper & CC#67 Una Corda thinning (40ms interval, +/-3 deadband)
        assert out.send_pedal_cc64(50, now=200.000) is True
        assert out.send_pedal_cc64(52, now=200.050) is False
        assert out.send_pedal_cc64(64, now=200.015) is False
        assert out.send_pedal_cc64(64, now=200.060) is True

        assert out.send_pedal_cc67(72, now=200.000) is True
        assert out.send_pedal_cc67(73, now=200.050) is False
        assert out.send_pedal_cc67(96, now=200.015) is False
        assert out.send_pedal_cc67(96, now=200.060) is True
        assert out.get_telemetry().pedal_cc67 == 96

        emitted.clear()
        panic_res = out.panic()
        assert panic_res["active_voices"] == 0
        assert panic_res["elapsed_ms"] < 10.0
        ccs = {(e["control"], e["value"]) for e in emitted if e["type"] == "control_change"}
        assert (64, 0) in ccs and (67, 0) in ccs and (123, 0) in ccs and (120, 0) in ccs
    finally:
        out.close()


def test_2_mode_a_self_echo_and_una_corda_isolation() -> None:
    ledger = EchoSuppressionLedger(min_lag_s=0.0, max_lag_s=0.250)
    detector = TurnTakingDetector(silence_threshold_s=0.6)
    out = DisklavierStageOutputLayer(
        port_name=None,
        echo_register_cb=ledger.register_sent_note,
        ai_cc67_cb=detector.notify_ai_cc67,
        start_worker=False,
    )
    try:
        t0 = 1000.0
        ai_pitches = [48, 55, 60, 64, 72]
        for idx, p in enumerate(ai_pitches):
            assert out.send_note_on_immediate(pitch=p, velocity=74, duration_s=0.5, now=t0 + idx * 0.01)

        # AI driving CC#67 must NOT trigger human turn-taking cue immediately
        assert out.send_pedal_cc67(95, now=t0 + 0.05) is True
        detector.push_cc(67, 95, t0 + 0.08)
        _, ready_ai_cc, _ = detector.evaluate(now=t0 + 0.12)
        assert ready_ai_cc is False

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


def test_4_musicality_critic_token_economizer_and_logic_validator() -> None:
    # 1. Verify MusicalityCritic explicitly catches and rejects monotonous scale runs & monophony
    scale_only = [
        {"pitch": 60 + iv, "onset_s": i * 0.25, "duration_s": 0.24, "velocity": 70, "hand": "rh"}
        for i, iv in enumerate([0, 2, 4, 5, 7, 9])
    ]
    scale_audit = MusicalityCritic.audit_candidate(scale_only, [{"pitch": 50}])
    assert scale_audit.passed is False
    assert "monotonic_scale_run" in scale_audit.failures
    assert "monophonic_single_hand" in scale_audit.failures
    assert "単調な順次音階" in scale_audit.rejection_tag_ja

    # 2. Verify Zero-Config fallback, TokenEconomizer, and 6-axis polyphonic composition
    with tempfile.TemporaryDirectory() as empty_dir:
        loader = ZeroConfigCorpusLoader(search_dirs=[Path(empty_dir)])
        assert loader.loaded_file_count == 0
        assert loader.source_mode == "builtin_seed_dictionary"
        assert len(loader.patterns) >= 4

        econ = TokenEconomizer()
        engine = MusicalIntentEngine(use_ollama=False, economizer=econ)
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
        hints = VerbalDirectiveRecognizer().parse_directive("対位法で挑発して、ペダル踏み替えと浅いタッチで")
        policy = engine.decide_policy(snap, human_notes, hints, corpus_refs=loader.patterns[:3])
        assert econ.last_stats["compression_ratio_pct"] > 0.0

        turn = composer.compose_turn(human_notes, policy, human_mode="cello")
        intent = engine.finalize_deliberation_monologue(
            snap, human_notes, policy, turn, hints, corpus_refs=turn.corpus_refs_used
        )

        adopted = [c for c in turn.candidates if c.adopted]
        rejected = [c for c in turn.candidates if not c.adopted]
        assert len(adopted) == 1
        assert len(rejected) == 3

        win = adopted[0]
        assert win.audit_passed is True
        win_notes = [e for e in win.events if int(e.get("pitch", 0)) > 0]
        assert any(e.get("hand") == "lh" or int(e["pitch"]) < 60 for e in win_notes)
        assert any(e.get("hand") == "rh" or int(e["pitch"]) >= 60 for e in win_notes)
        assert len(win.pedal_cc64_curve) >= 3 and len(win.pedal_cc67_curve) >= 2

        expected_axes = {
            "anti_scale",
            "polyphony_harmony",
            "voice_leading",
            "spectral_clearance",
            "rhythmic_diversity",
            "dual_pedal",
        }
        for rej in rejected:
            assert rej.rejection_tag_ja.startswith("[× 棄却:")
            assert len(rej.problem_note_indices) >= 1
            assert set(rej.subscores_pct.keys()) == expected_axes
            assert "hud_badge_ja" in rej.rejection_detail

        ok, errs = MusicalLogicValidator.check_text(
            intent.monologue_ja,
            perception=snap,
            adopted_candidate=win,
            rejected_candidates=rejected,
        )
        assert ok, f"Factual memo failed validation ({errs}): {intent.monologue_ja}"
        assert 45 <= len(intent.monologue_ja) <= 75
        for bad in BANNED_PHRASES:
            assert bad not in intent.monologue_ja

        # Ensure contradictions, AI poetry, and "〇〇風" labels are strictly rejected
        contradictions = [
            "チェロのD3が半音階で上昇し、長三度の空虚五度で緊張を高める。ダンパー操作で余白を残して解決を待つ。",
            "チェロの演奏が素晴らしい！あなたの魂に共鳴しました🎹✨",
            "スタッカートのレガートを無音の強奏でペダル無しの深い残響にするだろう。",
            "チェロD3のレガートを検出。案Dを捨て、ドビュッシー風かつバロック風の案Aをペダルで置く。",
        ]
        for bad_txt in contradictions:
            assert not validate_chamber_music_monologue(bad_txt), f"Failed to block NG text: {bad_txt}"
            sanitized = sanitize_monologue_ja(bad_txt, intent.monologue_ja, perception=snap)
            assert validate_chamber_music_monologue(sanitized, perception=snap)

        # Verify Stage HUD editorial palette & zero "〇〇風" buttons
        hud_html = Path("src/ui/stage_hud.html").read_text(encoding="utf-8")
        assert "#060608" in hud_html and "#EAE6DF" in hud_html and "#5A5852" in hud_html and "#C83E2B" in hud_html
        assert "UNA CORDA" in hud_html and "DAMPER" in hud_html
        for forbidden in ("AI THINKING", "#00ffff", "#ff00ff", "素晴らしい演奏", "共鳴しました", "印象派風", "バロック風"):
            assert forbidden not in hud_html
        assert "cdn.jsdelivr.net" not in hud_html

        # Verify Studio & Web-Demo HTML contain zero forbidden AI slop or external CDN scripts
        for ui_file in ("src/ui/studio.html", "web-demo/index.html"):
            if Path(ui_file).exists():
                ui_txt = Path(ui_file).read_text(encoding="utf-8")
                for forbidden in ("AI THINKING", "#00ffff", "#ff00ff", "素晴らしい演奏", "共鳴しました", "印象派風", "バロック風", "cdn.jsdelivr.net"):
                    assert forbidden not in ui_txt


def test_5_dual_pedal_physics_favicon_ascii_bats_and_production_separation() -> None:
    # 1. Verify Dual-Pedal Physical Acoustic Simulation in LocalAudioSynthesizer
    synth = LocalAudioSynthesizer(enabled=True)
    dry_evs = [
        {"pitch": 60, "onset_s": 0.0, "duration_s": 0.25, "velocity": 80, "hand": "rh"},
        {"pitch": 0, "onset_s": 0.0, "control": 64, "value": 0},
        {"pitch": 0, "onset_s": 0.0, "control": 67, "value": 0},
    ]
    damper_evs = [
        {"pitch": 60, "onset_s": 0.0, "duration_s": 0.25, "velocity": 80, "hand": "rh"},
        {"pitch": 0, "onset_s": 0.0, "control": 64, "value": 115},
        {"pitch": 0, "onset_s": 0.9, "control": 64, "value": 115},
        {"pitch": 0, "onset_s": 0.0, "control": 67, "value": 0},
    ]
    una_evs = [
        {"pitch": 60, "onset_s": 0.0, "duration_s": 0.25, "velocity": 80, "hand": "rh"},
        {"pitch": 0, "onset_s": 0.0, "control": 64, "value": 0},
        {"pitch": 0, "onset_s": 0.0, "control": 67, "value": 122},
    ]
    w_dry = synth.synthesize_phrase_events(dry_evs)
    w_damper = synth.synthesize_phrase_events(damper_evs)
    w_una = synth.synthesize_phrase_events(una_evs)

    # Tail energy after note-off (0.45s..0.95s) must be significantly larger with Damper CC#64=115
    sr = 44100
    tail_dry = float(np.mean(w_dry[int(0.45 * sr) : int(0.95 * sr)] ** 2))
    tail_damper = float(np.mean(w_damper[int(0.45 * sr) : int(0.95 * sr)] ** 2))
    assert tail_damper > tail_dry * 3.0, f"Damper sustain too weak: {tail_damper} vs {tail_dry}"

    # Una Corda CC#67=122 must attenuate upper partials (> 900 Hz) relative to dry strike
    spec_dry = np.abs(np.fft.rfft(w_dry[: int(0.25 * sr)]))
    spec_una = np.abs(np.fft.rfft(w_una[: int(0.25 * sr)]))
    freqs = np.fft.rfftfreq(int(0.25 * sr), d=1.0 / sr)
    hi_mask = freqs > 900.0
    assert float(np.sum(spec_una[hi_mask])) < float(np.sum(spec_dry[hi_mask])) * 0.70

    # Full turn audio check
    human_notes = [
        {"pitch": 50, "onset_s": 0.0, "duration_s": 0.25, "velocity": 74},
        {"pitch": 53, "onset_s": 0.28, "duration_s": 0.25, "velocity": 78},
    ]
    adopted_evs = [
        {"pitch": 38, "onset_s": 0.0, "duration_s": 0.85, "velocity": 62, "hand": "lh"},
        {"pitch": 69, "onset_s": 0.0, "duration_s": 0.35, "velocity": 72, "hand": "rh"},
        {"pitch": 74, "onset_s": 0.32, "duration_s": 0.45, "velocity": 76, "hand": "rh"},
        {"pitch": 0, "onset_s": 0.0, "control": 64, "value": 0},
        {"pitch": 0, "onset_s": 0.08, "control": 64, "value": 92},
        {"pitch": 0, "onset_s": 0.40, "control": 64, "value": 0},
        {"pitch": 0, "onset_s": 0.48, "control": 64, "value": 84},
        {"pitch": 0, "onset_s": 0.0, "control": 67, "value": 24},
        {"pitch": 0, "onset_s": 0.30, "control": 67, "value": 86},
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
    assert audio_stats["pedal_cc64_active"] is True
    assert audio_stats["pedal_cc67_active"] is True
    assert audio_stats["repedal_count"] >= 1
    assert audio_stats["una_corda_veil_db"] < 0.0
    synth.close()

    # 2. Verify all .bat launchers contain pure ASCII only & ZERO `chcp 65001` (prevents Windows char doubling)
    from src.stage.cli_banner import BANNERS, display_width

    for bat_name in ("setup_environment.bat", "start_stage.bat", "run_demo_showcase.bat", "run_self_check.bat"):
        bat_text = Path(bat_name).read_text(encoding="utf-8")
        non_ascii = [ch for ch in bat_text if ord(ch) > 127]
        assert not non_ascii, f"{bat_name} contains non-ASCII characters: {non_ascii[:5]}"
        assert "chcp 65001" not in bat_text.lower(), f"{bat_name} must not call chcp 65001"
    for bkey, bval in BANNERS.items():
        for ln in bval.splitlines():
            assert display_width(ln) <= 76, f"Banner line too wide ({display_width(ln)} cols) in {bkey}: {ln}"

    # 3. Verify Stage Server + /favicon.ico 200 OK + Scenario Execution + Obsidian Note Archival
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
            assert client.get("/studio").status_code == 200
            assert client.get("/stage").status_code == 200
            assert client.get("/studio-static/style.css").status_code == 200
            assert client.get("/studio-static/app.js").status_code == 200
            assert client.get("/studio-static/vexflow-bravura.js").status_code == 200
            assert client.get("/studio-static/score_engraver.js").status_code == 200
            assert client.get("/studio-static/../../pyproject.toml").status_code in (400, 404)
            assert client.get("/api/pieces/../../pyproject.toml").status_code in (400, 404)
            assert client.get("/audio/../../pyproject.toml").status_code in (400, 404)
            fav = client.get("/favicon.ico")
            assert fav.status_code == 200 and len(fav.content) > 100
            assert client.post("/api/stage/reset").json()["motif_nodes"] == 0

            with client.websocket_connect("/ws/stage") as ws:
                init_st = ws.receive_json()
                assert init_st["monitor_audio"] is True
                assert init_st["production_mode"] is False

                r_preset = client.post("/api/stage/preset", json={"preset": "cello_leap_spiccato"})
                assert r_preset.status_code == 200
                st1 = r_preset.json()["state"]
                assert len(st1["deliberation_timeline"]) >= 5
                assert st1["audio_status"]["rms"] > 0.01
                assert len(st1["candidates"]) == 4
                assert st1.get("obsidian_last_note", "").endswith(".md")

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

    # 4. Verify Production Mode strictly disables virtual demo and python monitor synth
    with tempfile.TemporaryDirectory() as empty_dir:
        prod_app = create_stage_app(
            demo_enabled=True,
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


def _can_import(mod_name: str) -> bool:
    try:
        __import__(mod_name)
        return True
    except Exception:
        return False


def test_6_four_act_architect_phrase_model_retake_and_masterwork_export() -> None:
    from src.stage.co_composition_architect import CoCompositionArchitect
    from src.stage.corpus_phrase_model import CorpusPhraseModelManager

    has_torch = _can_import("torch")
    has_lameenc = _can_import("lameenc")

    # 1. Verify trained RTX 3070 model loads cleanly when torch is installed, or falls back cleanly in CI
    pm = CorpusPhraseModelManager()
    assert pm.model_path.exists(), f"Trained checkpoint missing: {pm.model_path}"
    if has_torch:
        assert pm.loaded is True
    bp = pm.predict_blueprint(
        act_number=1,
        phrase_function="question_half_cadence",
        seed_intervals=[3, 4, -2],
        prev_end_pitch=69,
        anchor_pitch=69,
        tonic_pc=2,
        tension=0.5,
        momentum=0.5,
        articulation="legato",
        mode="cello",
    )
    assert len(bp.melody_degree_offsets) == 8
    assert bp.lh_idiom in (
        "arpeggio_root_5th_10th",
        "pedal_point_inner_voice",
        "syncopated_offbeat_harmony",
        "una_corda_choral_recall",
    )

    # 2. Verify 4-Act progression, Public Rehearsal Retake, Masterwork Synthesis & /demos/ route
    with tempfile.TemporaryDirectory() as tmp_dir:
        app = create_stage_app(
            demo_enabled=False,
            monitor_audio=True,
            production_mode=False,
            corpus_dirs=[Path(tmp_dir)],
            use_ollama=False,
            open_hardware=False,
            play_audio_device=False,
        )
        with TestClient(app) as client:
            orch = app.state.orchestrator
            acts_seen: List[int] = []
            for idx in range(8):
                r = client.post("/api/stage/turn")
                assert r.status_code == 200
                st = r.json()["state"]
                acts_seen.append(int(st["intent"]["act_number"]))
                win = next(c for c in st["candidates"] if c["adopted"])
                assert len(win.get("masterwork_pct", {})) == 4
                assert win.get("masterwork_total", 0.0) >= 60.0

                if idx == 1:
                    r_ret = client.post(
                        "/api/stage/retake",
                        json={"text": "今のフレーズはまだ解決しない問いかけのまま、左手の内声を増強して書き直して"},
                    )
                    assert r_ret.status_code == 200
                    st_ret = r_ret.json()["state"]
                    assert st_ret["turn_id"] == 2
                    assert st_ret["intent"]["is_retake"] is True
                    assert st_ret["intent"]["phrase_function"] == "question_half_cadence"

            assert set(acts_seen) == {1, 2, 3, 4}

            # Synthesize complete masterwork into temp directory and test export artifacts
            out_dir = Path(tmp_dir) / "exports"
            summary = orch.synthesize_complete_work(
                out_dir=out_dir,
                basename="test_masterwork_cello",
                play_audio=False,
            )
            assert summary["ok"] is True
            assert summary["preflight_passed"] is True
            assert summary["total_duration_s"] >= 35.0
            assert summary["audio_metrics"]["rms"] > 0.01
            assert summary["audio_metrics"]["clipping_count"] == 0
            assert (out_dir / "test_masterwork_cello.wav").stat().st_size > 10000
            if has_lameenc:
                assert (out_dir / "test_masterwork_cello.mp3").stat().st_size > 10000
            assert (out_dir / "test_masterwork_cello.mid").stat().st_size > 500
            assert (out_dir / "test_masterwork_cello.musicxml").stat().st_size > 1000

            # Also verify API route /api/stage/synthesize_work & /demos/ serving + path traversal protection
            r_syn = client.post("/api/stage/synthesize_work", json={"play_audio": False})
            assert r_syn.status_code == 200
            syn_json = r_syn.json()
            assert syn_json["ok"] is True
            asset_url = syn_json["assets"]["mp3_url"] if has_lameenc else syn_json["assets"]["wav_url"]
            assert asset_url.startswith("/demos/")
            r_audio = client.get(asset_url)
            assert r_audio.status_code == 200 and len(r_audio.content) > 10000
            # Clean up exported test artifact in data/telemetry/exports
            exp_dir = Path("data/telemetry/exports")
            for key in ("mp3", "wav", "mid", "musicxml"):
                fname = syn_json["assets"].get(key)
                if fname and (exp_dir / fname).exists():
                    (exp_dir / fname).unlink()
            assert client.get("/demos/../../pyproject.toml").status_code in (400, 404)
            assert isinstance(CoCompositionArchitect(), CoCompositionArchitect)


def test_7_harmonic_beauty_realizer_and_auditor_guarantees() -> None:
    from src.stage.harmonic_beauty_realizer import (
        BeautyHarmonyAuditor,
        HarmonicBeautyRealizer,
        extract_corpus_musical_laws,
    )

    laws = extract_corpus_musical_laws()
    assert laws["corpus_files_analyzed"] >= 1
    for ton, mode in (("D_minor", "cello"), ("F_major", "piano"), ("G_minor", "cello"), ("Eb_major", "piano")):
        res = HarmonicBeautyRealizer.realize_4act_piece(
            {"tonal_center": ton, "bpm": 76.0, "prog_variant": 0}, mode=mode, corpus_laws=laws
        )
        rep = BeautyHarmonyAuditor.audit_piece(
            notes=res["notes"],
            chord_timeline=res["chord_timeline"],
            cc64_curve=res["cc64_curve"],
            cc67_curve=res["cc67_curve"],
            tonality=ton,
            suspension_windows=res["suspension_windows"],
            piano_events_for_preflight=res["piano_preflight_events"],
        )
        assert rep.passed is True, f"{ton}/{mode} failed: {rep.issues}"
        assert rep.beauty_score >= 88.0
        assert rep.consonance_score >= 90.0
        assert rep.avg_polyphony >= 2.5
        assert rep.mono_ratio_pct < 10.0
        assert rep.dissonant_pair_pct <= 2.5
        assert rep.repedal_sync_pct >= 85.0
        assert rep.preflight_passed is True
