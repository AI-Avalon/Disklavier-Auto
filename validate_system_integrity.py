"""End-to-end integrity & Disklavier safety validation suite for Disklavier-Auto."""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
import sys
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.composer.ai_classical_composer import reply_to_passage_virtuoso
from src.evolution.continuous_virtuoso import ContinuousVirtuosoEngine
from src.evolution.human_reference_teacher import HumanReferenceTeacher
from src.evolution.seed_ingestor import SeedMemoryPool
from src.features.feature_matrix_100 import (
    build_sheet_music_payload,
    get_feature_catalog,
)
from src.midi.imslp_library import (
    IMSLPMasterworkLibrary,
    _build_masterwork_definitions,
)
from src.obsidian.obsidian_brain import ObsidianMusicalBrain
from src.safety.disklavier_safety_guard import DisklavierSafetyGuard
from src.safety.preflight_checker import LocalPreflightChecker


BANNED_UI_JARGON = [
    "モチーフ即時吸収",
    "多面的",
    "ポリフォニック・エントロピー",
    "アクター・クリティック重み",
    "ゲート長",
]


def _extract_events(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("events", "notes", "midi_events", "reply_events", "generated_events"):
            val = payload.get(key)
            if isinstance(val, list):
                return val
    raise AssertionError(f"Could not extract event list from payload of type {type(payload)}")


def _assert_disklavier_physical_safety(label: str, raw_events: Any) -> Dict[str, Any]:
    events = _extract_events(raw_events)
    assert len(events) > 0, f"{label}: generated event list is empty"

    # Normalize pitch/note for LocalPreflightChecker compatibility
    normalized_events: List[Dict[str, Any]] = []
    for ev in events:
        copy = dict(ev)
        if "note" not in copy and "pitch" in copy:
            copy["note"] = copy["pitch"]
        if "time_offset_s" not in copy and "onset_s" in copy:
            copy["time_offset_s"] = copy["onset_s"]
        normalized_events.append(copy)

    report = LocalPreflightChecker.audit_events(normalized_events)
    if not report.get("passed", False):
        raise AssertionError(f"{label}: LocalPreflightChecker failed: {report}")

    note_events = [
        ev for ev in normalized_events
        if int(ev.get("note", -1) or -1) >= 21
        and int(ev.get("note", -1) or -1) <= 108
        and ev.get("voice", "") != "pedal"
        and ev.get("event_type", "note") != "cc"
    ]
    assert len(note_events) > 0, f"{label}: no playable piano note events found"

    # Independent sweep for max simultaneous 2ms onsets and max held voices
    onsets = sorted(
        float(ev.get("time_offset_s", ev.get("time_s", 0.0))) for ev in note_events
    )
    max_simul = 0
    left = 0
    for right, t_r in enumerate(onsets):
        while t_r - onsets[left] > 0.002 + 1e-9:
            left += 1
        max_simul = max(max_simul, right - left + 1)

    intervals: List[tuple[float, int]] = []
    for ev in note_events:
        t0 = float(ev.get("time_offset_s", ev.get("time_s", 0.0)))
        dur = float(ev.get("duration_s", ev.get("duration", 0.0)))
        intervals.append((t0, 1))
        intervals.append((t0 + dur, -1))
    # Releases processed before new onsets at exact boundary
    intervals.sort(key=lambda item: (item[0], item[1]))
    cur_held = 0
    max_held = 0
    for _, delta in intervals:
        cur_held += delta
        max_held = max(max_held, cur_held)

    assert max_simul <= 16, f"{label}: max_simultaneous={max_simul} > 16"
    assert max_held <= 20, f"{label}: max_held={max_held} > 20 (30-note PSU crash guard)"

    for idx, ev in enumerate(note_events):
        vel = int(ev.get("velocity", 0))
        role = str(
            ev.get("role", ev.get("voice", ev.get("source_tag", ev.get("layer", ""))))
        ).lower()
        is_halo = bool(ev.get("sympathetic_halo")) or (
            vel == 1 and ("sympathetic" in role or "halo" in role or "overtone" in role)
        )
        if not is_halo:
            assert 20 <= vel <= 105, f"{label}[{idx}]: unsafe velocity={vel} (role={role!r})"
        dur = float(ev.get("duration_s", ev.get("duration", 0.0)))
        assert dur >= 0.035 - 1e-6, f"{label}[{idx}]: unsafe short duration_s={dur:.4f} < 0.035"

    return {
        "notes": len(note_events),
        "max_simul": max_simul,
        "max_held": max_held,
    }


def check_feature_catalog() -> str:
    catalog = get_feature_catalog()
    features = catalog.get("features", []) if isinstance(catalog, dict) else list(catalog)
    assert len(features) == 100, f"Expected 100 features, got {len(features)}"
    assert int(catalog.get("total_count", len(features))) == 100
    return f"100 features registered (IDs {features[0].get('id', 1)}..{features[-1].get('id', 100)})"


def check_imslp_deep_dna() -> str:
    works = _build_masterwork_definitions()
    assert len(works) >= 11, f"Expected >= 11 IMSLP masterworks, got {len(works)}"
    text_blob = " ".join(
        f"{w.get('composer', '')} {w.get('title', '')} {w.get('filename', '')}".lower()
        for w in works
    )
    for composer in ("tchaikovsky", "rachmaninoff", "liszt", "chopin", "bach"):
        assert composer in text_blob, f"Missing required composer in IMSLP masterworks: {composer}"

    lib = IMSLPMasterworkLibrary()
    dna = lib.extract_deep_musical_dna(persist=True)
    assert isinstance(dna, dict), "extract_deep_musical_dna did not return dict"
    assert dna.get("total_masterworks", 0) >= 11, f"DNA masterwork count < 11: {dna.get('total_masterworks')}"
    assert dna.get("global_laws") or dna.get("works") or dna.get("composer_signatures"), (
        f"Deep DNA missing analytical sections: {list(dna.keys())}"
    )
    return f"{len(works)} masterworks analyzed; DNA keys={list(dna.keys())[:5]}"


def check_meta_director_and_radar() -> tuple[Dict[str, Any], str]:
    plan = HumanReferenceTeacher.generate_meta_director_work_plan(
        tab_index=0, mood_preset="romantic"
    )
    assert isinstance(plan, dict) and plan, "Meta-AI Director work plan is empty"
    title_ja = str(plan.get("title_ja", ""))
    title_eu = str(plan.get("title_eu", "") or plan.get("title_en", ""))
    opus = str(plan.get("opus", ""))
    sections = plan.get("formal_sections", []) or plan.get("sections", [])
    assert title_ja, f"Missing title_ja in work plan: {plan}"
    assert title_eu and opus, f"Missing European title or Opus in work plan: {plan}"
    assert len(sections) >= 5, f"Expected 5 formal sections, got {len(sections)}"

    evs_4hand = HumanReferenceTeacher.generate_4hand_virtuoso_layering(
        root_pitch=46, start_time_s=0.0, duration_s=4.2
    )
    radar = HumanReferenceTeacher.evaluate_6axis_radar_100(evs_4hand)
    assert isinstance(radar, dict) and len(radar) >= 6, f"Expected 6 radar axes, got {radar}"
    for k, v in radar.items():
        if isinstance(v, (int, float)):
            assert 0.0 <= float(v) <= 100.0, f"Radar axis {k}={v} out of [0, 100]"
    return plan, f"'{title_ja}' ({opus}), {len(sections)} sections, {len(radar)} radar axes"


def check_super_virtuoso_generators_safety() -> str:
    stats = {}
    stats["4hand"] = _assert_disklavier_physical_safety(
        "generate_4hand_virtuoso_layering",
        HumanReferenceTeacher.generate_4hand_virtuoso_layering(root_pitch=46, start_time_s=0.0, duration_s=4.2),
    )
    stats["cascade"] = _assert_disklavier_physical_safety(
        "generate_88key_crystal_cascade",
        HumanReferenceTeacher.generate_88key_crystal_cascade(
            root_pitch=34, start_time_s=0.0, duration_s=3.2, ascending=True
        ),
    )
    stats["tremolo"] = _assert_disklavier_physical_safety(
        "generate_octave_alternating_tremolo",
        HumanReferenceTeacher.generate_octave_alternating_tremolo(
            center_pitch=70, start_time_s=0.0, duration_s=2.0, velocity=78
        ),
    )
    return ", ".join(f"{k}:{v['notes']}n(sim<={v['max_simul']})" for k, v in stats.items())


def check_passage_dialogue_safety() -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    user_passage = [
        {"pitch": 62, "onset_s": 0.0, "duration_s": 0.42, "velocity": 74},
        {"pitch": 65, "onset_s": 0.42, "duration_s": 0.38, "velocity": 78},
        {"pitch": 69, "onset_s": 0.80, "duration_s": 0.45, "velocity": 82},
        {"pitch": 74, "onset_s": 1.25, "duration_s": 0.65, "velocity": 86},
    ]
    outputs: Dict[str, List[Dict[str, Any]]] = {}
    for mode in ("contrapuntal_reply", "liszt_rachmaninoff_virtuoso", "evolve_symphonic_masterwork"):
        res = reply_to_passage_virtuoso(user_passage, reply_mode=mode, source="piano_midi")
        evs = _extract_events(res)
        _assert_disklavier_physical_safety(f"passage_reply:{mode}", evs)
        outputs[mode] = evs
    summary = ", ".join(f"{m}:{len(e)}n" for m, e in outputs.items())
    return outputs["contrapuntal_reply"], outputs["liszt_rachmaninoff_virtuoso"], summary


def check_engine_integration_and_stage_acts(work_plan: Dict[str, Any]) -> str:
    guard = DisklavierSafetyGuard(virtual_twin=None)
    seed_pool = SeedMemoryPool(auto_ingest=True)
    brain = ObsidianMusicalBrain()
    engine = ContinuousVirtuosoEngine(
        safety_guard=guard, seed_pool=seed_pool, obsidian_brain=brain
    )

    for mode in ("4hand_layering", "88key_cascade", "silent_overtone_halo", "octave_tremolo"):
        res = engine.apply_virtuoso_expression(mode, play_now=False)
        assert isinstance(res, dict) and res.get("ok") is True, f"apply_virtuoso_expression({mode}) failed: {res}"
        evs = list(engine.workspace.get_active_tab().latest_events)
        _assert_disklavier_physical_safety(f"engine.apply_virtuoso_expression:{mode}", evs)

    user_passage = [
        {"pitch": 57, "onset_s": 0.0, "duration_s": 0.5, "velocity": 72},
        {"pitch": 64, "onset_s": 0.5, "duration_s": 0.5, "velocity": 78},
        {"pitch": 67, "onset_s": 1.0, "duration_s": 0.6, "velocity": 82},
    ]
    for rmode in ("contrapuntal_reply", "liszt_rachmaninoff_virtuoso", "evolve_symphonic_masterwork"):
        res = engine.apply_passage_reply(
            user_passage, reply_mode=rmode, source="cello_audio", play_now=False
        )
        evs = _extract_events(res)
        _assert_disklavier_physical_safety(f"engine.apply_passage_reply:{rmode}", evs)

    for act in (1, 2, 3):
        res = engine.run_stage_act(act, play_now=False)
        assert isinstance(res, dict) and res.get("ok") is True, f"run_stage_act({act}) failed: {res}"
        evs = list(engine.workspace.get_active_tab().latest_events)
        _assert_disklavier_physical_safety(f"engine.run_stage_act:{act}", evs)

    snap = engine.get_telemetry_snapshot()
    assert isinstance(snap, dict), "get_telemetry_snapshot did not return dict"
    assert snap.get("imslp_dna") is not None, "Telemetry snapshot missing imslp_dna"
    assert snap.get("radar_6axis") is not None, "Telemetry snapshot missing radar_6axis"

    explorer = brain.get_in_app_knowledge_explorer()
    for req_key in ("graph", "phylogenetic_tree", "recent_notes_full"):
        assert req_key in explorer, f"Knowledge explorer missing key '{req_key}'"

    # Verify Living Score diffing between prior generation and virtuoso generation
    assert engine.run_stage_act(1, play_now=False).get("ok") is True
    evs_gen1 = [dict(e) for e in engine.workspace.get_active_tab().latest_events]
    assert engine.run_stage_act(3, play_now=False).get("ok") is True
    evs_gen2 = [dict(e) for e in engine.workspace.get_active_tab().latest_events]
    sheet = build_sheet_music_payload(
        evs_gen2,
        title=str(work_plan.get("title_ja", "Self-Evolving Opus")),
        bpm=88.0,
        prev_events=evs_gen1,
        work_metadata=work_plan,
    )
    assert sheet.get("mutated_count", 0) > 0, "Living Score diff failed to mark mutated notes"
    assert "dynamic_markings" in sheet, "Living Score payload missing dynamic_markings"
    mutated_notes = [n for n in sheet.get("notes", []) if n.get("mutated")]
    assert mutated_notes and any(n.get("mutation_reason_ja") for n in mutated_notes), (
        "Mutated notes missing mutation_reason_ja"
    )
    return (
        f"4 virtuoso modes + 3 passage modes + 3 stage acts passed; "
        f"mutated_count={sheet.get('mutated_count')}"
    )


def check_server_routes_and_ui() -> str:
    from src.server.mission_control_server import app

    route_paths = {getattr(r, "path", "") for r in app.routes}
    required_routes = {
        "/api/score/current",
        "/api/features/sheet_music",
        "/api/state",
        "/api/imslp/dna",
        "/api/dialogue/passage_reply",
        "/api/studio/virtuoso_expression",
        "/api/studio/stage_act",
        "/api/obsidian/explorer",
        "/ws",
    }
    missing = sorted(required_routes - route_paths)
    assert not missing, f"Missing FastAPI routes: {missing}"

    # Verify HTTP endpoints respond cleanly using Starlette/FastAPI TestClient if httpx is available,
    # otherwise verify endpoint callables directly.
    used_testclient = False
    try:
        from fastapi.testclient import TestClient

        client = TestClient(app)
        for ep in ("/api/state", "/api/imslp/dna", "/api/score/current", "/api/obsidian/explorer"):
            resp = client.get(ep)
            assert resp.status_code == 200, f"GET {ep} returned {resp.status_code}"
        used_testclient = True
    except RuntimeError:
        # httpx not installed in environment; invoke route endpoint functions directly
        for r in app.routes:
            path = getattr(r, "path", "")
            if path in ("/api/imslp/dna", "/api/score/current", "/api/obsidian/explorer") and "GET" in getattr(r, "methods", set()):
                fn = getattr(r, "endpoint", None)
                if fn is not None:
                    res = asyncio.run(fn()) if inspect.iscoroutinefunction(fn) else fn()
                    assert isinstance(res, dict), f"Endpoint {path} did not return dict"

    ui_path = ROOT / "src" / "ui" / "index.html"
    assert ui_path.exists(), f"UI file missing: {ui_path}"
    html = ui_path.read_text(encoding="utf-8")
    for jargon in BANNED_UI_JARGON:
        assert jargon not in html, f"Banned translationese found in index.html: {jargon}"
    for req_id in (
        "canvas-score",
        "canvas-radar6",
        "explorer-dialog",
        "vtab-stage",
        "vtab-evolve",
        "vtab-cello",
        "vtab-notes",
        "vtab-features",
    ):
        assert req_id in html, f"Required UI element id missing from index.html: {req_id}"

    mode = "TestClient GET 200" if used_testclient else "direct endpoint call"
    return f"{len(required_routes)} routes verified ({mode}), UI clean (0 banned jargon)"


def main() -> int:
    checks = []
    checks.append(("1. 100-Feature Catalog", check_feature_catalog()))
    checks.append(("2. IMSLP Deep Musical DNA (11+ Works)", check_imslp_deep_dna()))
    work_plan, meta_msg = check_meta_director_and_radar()
    checks.append(("3. Meta-AI Director & 6-Axis Radar", meta_msg))
    checks.append(("4. Super-Virtuoso Generators Safety", check_super_virtuoso_generators_safety()))
    _, _, dialogue_msg = check_passage_dialogue_safety()
    checks.append(("5. Bi-Directional Passage Reply Safety", dialogue_msg))
    checks.append(("6. Engine, Stage Acts, Score Diff & Obsidian", check_engine_integration_and_stage_acts(work_plan)))
    checks.append(("7. Server API Routes & Japanese UI Integrity", check_server_routes_and_ui()))

    print("=== Disklavier-Auto System Integrity Validation ===")
    for name, detail in checks:
        print(f"[PASS] {name}: {detail}")
    print("ALL 7 INTEGRITY SUITES PASSED (0 physical safety violations, 0 schema mismatches).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
