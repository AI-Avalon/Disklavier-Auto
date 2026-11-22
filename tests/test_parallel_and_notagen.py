"""
Phase 6 Verification Tests for 6-Worker Parallel Pipeline, RTX 3070 VRAM Manager,
and NotaGen / ABC Live Streaming Composition Engine (CI-Safe CPU/Offline Compatible).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from fastapi.testclient import TestClient

from src.stage.co_composition_architect import CoCompositionArchitect
from src.stage.layer1_perception import PerceptionSnapshot
from src.stage.layer2_intent_llm import MusicalIntentEngine
from src.stage.layer3_candidate_composer import MultipleCandidateComposer, ZeroConfigCorpusLoader
from src.stage.layer5_disklavier_output import DisklavierStageOutputLayer
from src.stage.notagen_live_engine import (
    ABCStreamParser,
    LivePolyphonyHarmonyGuard,
    NotaGenLiveEngine,
)
from src.stage.parallel_pipeline import SpeculativePreGenWorker
from src.stage.stage_server import create_stage_app
from src.stage.vram_manager import RTX3070VRAMManager


def _sample_human_notes() -> List[Dict[str, Any]]:
    return [
        {"pitch": 62, "velocity": 68, "onset_s": 0.0, "duration_s": 0.55},
        {"pitch": 65, "velocity": 72, "onset_s": 0.55, "duration_s": 0.55},
        {"pitch": 69, "velocity": 76, "onset_s": 1.10, "duration_s": 0.85},
    ]


def test_1_worker4_parallel_composer_speedup_and_equivalence() -> None:
    corpus = ZeroConfigCorpusLoader(search_dirs=[])
    composer = MultipleCandidateComposer(corpus_loader=corpus)
    intent_engine = MusicalIntentEngine(use_ollama=False)
    architect = CoCompositionArchitect()
    notes = _sample_human_notes()
    snap = PerceptionSnapshot(
        mode="cello",
        timestamp=time.perf_counter(),
        f0_hz=146.83,
        midi_pitch=62,
        note_name="D4",
        articulation="cantabile",
    )

    macro = architect.preview_turn_architecture(human_notes=notes, mode="cello")
    intent = intent_engine.decide_policy(snap, notes, use_llm_design=False, macro_plan=macro)

    try:
        res_par = composer.compose_turn(notes, intent, human_mode="cello", macro_plan=macro, parallel=True)
        res_ser = composer.compose_turn(notes, intent, human_mode="cello", macro_plan=macro, parallel=False)

        assert len(res_par.candidates) == 4
        assert len(res_ser.candidates) == 4
        assert res_par.adopted_candidate is not None
        assert res_par.adopted_candidate.candidate_id == res_ser.adopted_candidate.candidate_id
        assert res_par.parallel_compose_ms > 0.0
        assert res_par.sum_worker_ms > 0.0
        assert res_par.parallel_speedup_ratio > 0.0
        assert set(res_par.worker_timings_ms.keys()) == {"A", "B", "C", "D"}
    finally:
        composer.close()


def test_2_worker1_l5_safety_and_dispatch_jitter() -> None:
    sent: List[Dict[str, Any]] = []
    out = DisklavierStageOutputLayer(port_name=None, midi_sink=sent.append, start_worker=True)
    try:
        # Attempt 36 simultaneous notes + extreme velocities + rapid pedal spam
        events: List[Dict[str, Any]] = [
            {"pitch": 48 + (i % 36), "onset_s": 0.02, "duration_s": 0.40, "velocity": 127 if i % 2 == 0 else 4}
            for i in range(36)
        ]
        events.extend(
            {"pitch": 0, "onset_s": 0.02 + k * 0.002, "control": 64, "value": 80 + (k % 2)}
            for k in range(8)
        )
        out.send_note_on_immediate(60, 127, duration_s=0.25)
        out.schedule_phrase(events, base_sound_ts=time.perf_counter() + 0.04)
        time.sleep(0.22)

        tel = out.get_telemetry()
        assert tel.hard_limit == 30
        assert tel.active_voices <= 30
        assert tel.max_active_observed <= 30
        assert tel.velocity_clamps >= 1
        assert tel.pedal_thinned >= 1
        assert tel.last_dispatch_jitter_ms < 15.0
        assert all(18 <= int(m["velocity"]) <= 112 for m in sent if m.get("type") == "note_on")

        panic_res = out.panic()
        assert panic_res["elapsed_ms"] < 10.0
        assert out.get_telemetry().active_voices == 0
    finally:
        out.close()


def test_3_worker5_speculative_pregen_cache_hit() -> None:
    corpus = ZeroConfigCorpusLoader(search_dirs=[])
    composer = MultipleCandidateComposer(corpus_loader=corpus)
    intent_engine = MusicalIntentEngine(use_ollama=False)
    architect = CoCompositionArchitect()
    worker = SpeculativePreGenWorker(
        architect=architect,
        composer=composer,
        intent_engine=intent_engine,
        corpus_loader=corpus,
        ttl_s=8.0,
    )
    try:
        notes = _sample_human_notes()
        snap = PerceptionSnapshot(
            mode="piano",
            timestamp=time.perf_counter(),
            f0_hz=440.0,
            midi_pitch=69,
            note_name="A4",
        )
        worker.schedule_pregen(snap, notes, {})

        deadline = time.perf_counter() + 4.0
        while not worker.has_ready_draft() and time.perf_counter() < deadline:
            time.sleep(0.03)

        assert worker.has_ready_draft()
        draft = worker.try_consume("piano", notes, {})
        assert draft is not None
        assert draft.compose_ms > 0.0
        assert worker.hits >= 1
        assert worker.last_saved_ms > 0.0
    finally:
        worker.close()
        composer.close()


def test_4_rtx3070_vram_manager_eviction_and_arbitration(monkeypatch: Any) -> None:
    vm = RTX3070VRAMManager(total_vram_gb=8.0, safety_limit_gb=7.4)
    vm.current_owner = "ollama"

    evict_calls: List[str] = []
    release_calls: List[str] = []

    def fake_evict(model_name: Any = None) -> bool:
        evict_calls.append(str(model_name or vm.ollama_model))
        vm.evictions_count += 1
        return True

    monkeypatch.setattr(vm, "evict_ollama", fake_evict)
    acq_ng = vm.acquire_for("notagen", required_gb=2.2)
    assert acq_ng["ok"] is True
    assert vm.current_owner == "notagen"
    assert "ollama" in acq_ng["evicted"]
    assert len(evict_calls) == 1

    acq_ol = vm.acquire_for(
        "ollama",
        required_gb=3.1,
        release_callbacks={"notagen": lambda: release_calls.append("notagen")},
    )
    assert acq_ol["ok"] is True
    assert vm.current_owner == "ollama"
    assert "notagen" in acq_ol["evicted"]
    assert release_calls == ["notagen"]

    st = vm.get_status()
    assert st["safety_limit_gb"] == 7.4
    assert st["evictions_count"] >= 1


def test_5_notagen_abc_parser_harmony_guard_and_live_engine() -> None:
    parser = ABCStreamParser(tonality="D_minor", bpm=76.0)
    rh_notes, ch = parser.parse_voice_bar('[V:1] "Dm" D2 F2 A2 G2 |', hand="rh")
    lh_notes, _ = parser.parse_voice_bar("[V:2] D,,4 A,,4 |", hand="lh")
    assert ch == "Dm"
    assert len(rh_notes) == 4
    assert len(lh_notes) == 2

    # Feed minimal 1-note melody; guard must build rich balanced two-hand polyphony + pedals
    guarded = LivePolyphonyHarmonyGuard.realize_and_guard_bar(
        bar_idx=3,
        total_bars=4,
        rh_notes=rh_notes[:1],
        lh_notes=[],
        chord_symbol="Dmadd9",
        tonality="D_minor",
        bpm=76.0,
        seed_pitches=[62, 65, 69],
        instruction_ja="ウナ・コルダで静かに閉じる",
    )
    ev = guarded["local_events"]
    assert any(n.get("hand") == "lh" for n in ev if int(n.get("pitch", 0)) > 0)
    assert any(n.get("hand") == "rh" for n in ev if int(n.get("pitch", 0)) > 0)
    assert any(int(n.get("control", 0)) == 64 for n in ev)
    assert any(int(n.get("control", 0)) == 67 and int(n.get("value", 0)) >= 80 for n in ev)

    engine = NotaGenLiveEngine(use_ollama=False)
    st = engine.start_generation(
        seed_notes="D4 F4 A4",
        prompt_ja="静かな問いかけから終止する練習曲",
        tonality="D_minor",
        bars=4,
        engine="transformer_abc_fallback",
        play_audio=False,
        blocking=True,
    )
    assert st["status"] == "completed"
    assert st["active_backend"] == "transformer_abc_fallback"
    assert st["bars_completed"] == 4
    assert len(st["events"]) >= 20
    assert st["audit"]["beauty_score"] > 60.0
    assert st["audit"]["preflight_passed"] is True


def test_6_stage_server_live_parallel_and_notagen_endpoints() -> None:
    app = create_stage_app(
        demo_enabled=False,
        monitor_audio=False,
        use_ollama=False,
        open_hardware=False,
        play_audio_device=False,
    )
    with TestClient(app) as client:
        r_conn = client.post("/api/stage/connection_mode", json={"production_mode": False})
        assert r_conn.status_code == 200

        r_listen = client.post("/api/stage/live_listen", json={"enabled": True})
        assert r_listen.status_code == 200

        r_preset = client.post("/api/stage/preset", json={"preset": "cello_low_legato"})
        assert r_preset.status_code == 200
        assert r_preset.json().get("ok") is True

        r_note = client.post(
            "/api/stage/human_note",
            json={"pitch": 62, "velocity": 74, "duration_s": 0.4, "from_browser": True},
        )
        assert r_note.status_code == 200
        assert r_note.json()["state"]["mode"] in ("cello", "piano")

        r_phrase = client.post(
            "/api/stage/human_phrase",
            json={
                "notes": [
                    {"pitch": 62, "duration_s": 0.4, "velocity": 70},
                    {"pitch": 65, "duration_s": 0.4, "velocity": 74},
                    {"pitch": 69, "duration_s": 0.5, "velocity": 78},
                ],
                "mode": "piano",
                "auto_turn": True,
            },
        )
        assert r_phrase.status_code == 200
        st_phrase = r_phrase.json()["state"]
        assert st_phrase["mode"] == "piano"
        assert len(st_phrase["candidates"]) == 4
        assert "telemetry_hud" in st_phrase

        r_cue = client.post("/api/stage/pedal_cue", json={"control": 67, "value": 96})
        assert r_cue.status_code == 200

        r_ng = client.post(
            "/api/notagen/start",
            json={
                "prompt_ja": "4小節即興テスト",
                "engine": "transformer_abc_fallback",
                "tonality": "D_minor",
                "seed_notes": "D4 F4 A4",
                "bars": 4,
                "play_audio": False,
                "blocking": True,
            },
        )
        assert r_ng.status_code == 200
        ng_data = r_ng.json()["notagen"]
        assert ng_data["status"] == "completed"
        assert ng_data["bars_completed"] == 4

        r_ng_stat = client.get("/api/notagen/status")
        assert r_ng_stat.status_code == 200
        assert r_ng_stat.json()["bars_completed"] == 4

        r_state = client.get("/api/stage/state")
        assert r_state.status_code == 200
        hud = r_state.json()
        assert hud["notagen"]["bars_completed"] == 4
        assert hud["telemetry_hud"]["total_workers"] == 6
