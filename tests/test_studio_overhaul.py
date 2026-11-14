"""Comprehensive verification suite for the Disklavier-Auto Studio Overhaul."""
from __future__ import annotations

import asyncio
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Tuple

from src.evolution.actor_critic import MusicalGenome
from src.evolution.continuous_virtuoso import ContinuousVirtuosoEngine
from src.evolution.human_reference_teacher import HumanReferenceTeacher
from src.evolution.seed_ingestor import SeedMemoryPool
from src.features.feature_matrix_100 import (
    build_cello_open_string_warmup_wave,
    build_sheet_music_payload,
    export_events_to_midi_file,
    export_events_to_musicxml,
    export_events_to_wav,
    get_feature_catalog,
    get_lan_remote_info,
)
from src.obsidian.obsidian_brain import ObsidianMusicalBrain
from src.safety.disklavier_safety_guard import DisklavierSafetyGuard
from src.safety.preflight_checker import LocalPreflightChecker
from src.virtual_twin.virtual_disklavier import VirtualDisklavierTwin


async def _asgi_request(
    app: Any,
    method: str,
    path: str,
    json_body: Dict[str, Any] | None = None,
    include_token: bool = True,
) -> Tuple[int, bytes, Dict[str, str]]:
    """Invoke a FastAPI ASGI app directly without external HTTP client dependencies."""
    raw_body = json.dumps(json_body).encode("utf-8") if json_body is not None else b""
    headers = []
    if json_body is not None:
        headers.append((b"content-type", b"application/json"))
    if include_token:
        import src.server.mission_control_server as srv
        headers.append((b"x-studio-token", srv.STUDIO_AUTH_TOKEN.encode("ascii")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8765),
    }
    sent = False
    done_event = asyncio.Event()

    async def receive() -> Dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": raw_body, "more_body": False}
        await done_event.wait()
        return {"type": "http.disconnect"}

    status_code = 500
    resp_headers: Dict[str, str] = {}
    body_chunks: list[bytes] = []

    async def send(msg: Dict[str, Any]) -> None:
        nonlocal status_code, resp_headers
        if msg["type"] == "http.response.start":
            status_code = int(msg["status"])
            resp_headers = {
                k.decode("latin1").lower(): v.decode("latin1")
                for k, v in msg.get("headers", [])
            }
        elif msg["type"] == "http.response.body":
            body_chunks.append(msg.get("body", b""))
            if not msg.get("more_body", False):
                done_event.set()

    await app(scope, receive, send)
    done_event.set()
    return status_code, b"".join(body_chunks), resp_headers


def test_local_only_and_skills_present() -> None:
    assert not Path(".github/workflows").exists(), ".github/workflows must NOT exist"
    for skill_name in (
        "live-performance-japanese-ui",
        "human-virtuoso-expressive-midi",
        "apple-silicon-parallel-evolution",
    ):
        skill_file = Path(".agent/skills") / skill_name / "SKILL.md"
        assert skill_file.exists(), f"Missing skill {skill_file}"


def test_preflight_checker_sub_100ms_and_repair() -> None:
    dirty = [
        {"note": 60, "time_offset_s": 0.0, "velocity": 8, "duration_s": 0.012},  # ghost + <30ms
        {"note": 60, "time_offset_s": 0.02, "velocity": 120, "duration_s": 0.05},  # >15Hz rep + >105 vel
        {"note": 0, "time_offset_s": 0.03, "control": 66, "val": 100},  # forbidden sostenuto
    ]
    for i in range(20):
        dirty.append({"note": 48 + i, "time_offset_s": 1.0, "velocity": 70, "duration_s": 0.4})

    audit_before = LocalPreflightChecker.audit_events(dirty)
    assert not audit_before["passed"]
    assert audit_before["elapsed_ms"] < 100.0

    repaired, audit_after = LocalPreflightChecker.audit_and_repair(dirty)
    assert audit_after["passed"], f"Repaired stream still failed: {audit_after}"
    assert audit_after["elapsed_ms"] < 100.0
    assert all(float(e.get("duration_s", 0.1)) >= 0.035 for e in repaired if int(e.get("note", 0)) > 0)
    assert all(e.get("control") not in (66, 67) for e in repaired)


def test_human_reference_teacher_legato_vivace_and_inspiration() -> None:
    profiles_path = Path("data/human_references/master_performance_profiles.json")
    assert profiles_path.exists()
    mids = list(Path("data/human_references").glob("*.mid"))
    assert len(mids) >= 6

    insp = HumanReferenceTeacher.compute_emergent_inspiration(
        MusicalGenome(expression_x=-0.55, expression_y=0.82).to_dict(),
        expression_x=-0.55,
        expression_y=0.82,
    )
    sources = insp["sources"]
    assert sum(w["pct"] for w in sources) == 100
    assert insp["narrative_ja"] and insp["primary_lineage"]

    # Mechanical input -> humanize repair should add expression
    flat_notes = [
        {"note": 60 + i * 2, "time_offset_s": i * 0.35, "velocity": 64, "duration_s": 0.35, "voice": "melody"}
        for i in range(6)
    ]
    humanized, report = HumanReferenceTeacher.detect_and_humanize_mechanical_midi(flat_notes)
    assert report["was_mechanical"]
    mel = [e for e in humanized if int(e.get("note", 0)) > 0]
    assert all(15 <= int(e["release_velocity"]) <= 30 for e in mel)

    # Vivace expression -> staccato duration >= 0.035 and release_velocity 65..90
    vivace_ev = HumanReferenceTeacher.apply_human_master_expression(
        [{"note": 67, "time_offset_s": 0.1, "velocity": 75, "duration_s": 0.08, "voice": "melody", "articulation": "staccato"}],
        {"expression_x": 0.85, "expression_y": 0.8, "release_vel_staccato": 78, "staccato_gate_ms": 42.0, "half_pedal_target": 40},
        expression_x=0.85,
        expression_y=0.8,
    )
    v_notes = [e for e in vivace_ev if int(e.get("note", 0)) > 0]
    assert v_notes[0]["duration_s"] >= 0.035
    assert 65 <= int(v_notes[0]["release_velocity"]) <= 90


def test_workspace_tabs_timemachine_and_resets() -> None:
    guard = DisklavierSafetyGuard()
    twin = VirtualDisklavierTwin(hardware_connected=False)
    guard.attach_virtual_twin(twin)
    pool = SeedMemoryPool()
    pool.ingest_all()
    brain = ObsidianMusicalBrain()
    engine = ContinuousVirtuosoEngine(guard, pool, None, brain)
    with tempfile.TemporaryDirectory() as td:
        engine.workspace.state_path = Path(td) / "ws.json"
        tab2 = engine.create_song_tab(title="テスト歌タブ", character_preset="night_nocturne")
        t2_id = tab2["tab_id"]
        assert len(tab2["virtuoso"]["workspace"]["tabs"]) >= 2

        engine.run_hyper_speed_training(num_generations=4, speed_multiplier=25.0)
        undo_r = engine.timemachine_undo()
        assert undo_r["ok"]
        redo_r = engine.timemachine_redo()
        assert redo_r["ok"]
        scrub_r = engine.timemachine_scrub(1)
        assert scrub_r["ok"]
        ab_r = engine.play_ab_comparison("both")
        assert ab_r["ok"]

        rescue_r = engine.rescue_last_30s_to_tab()
        assert rescue_r["ok"]

        assert engine.set_expression_pad(-0.6, 0.7)["ok"]
        assert engine.apply_character_preset("vivace_scherzando")["ok"]
        assert engine.trigger_inspiration_spark()["ok"]
        assert engine.mood_change_keep_brain()["ok"]
        assert engine.register_human_like_vote()["ok"]
        assert engine.full_reset_new_song()["ok"]

        assert engine.close_song_tab(t2_id)["ok"]
        assert engine.restore_closed_song_tab()["ok"]
    engine.close()
    twin.close()


def test_100_features_exports_warmup_and_docs() -> None:
    cat = get_feature_catalog()
    assert cat["total_count"] == 100
    assert len(cat["categories"]) == 10

    evs, msg = build_cello_open_string_warmup_wave()
    assert len(evs) >= 60 and "C-G-D-A" in msg
    assert LocalPreflightChecker.audit_events(evs)["passed"]

    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        xml = export_events_to_musicxml(evs, "Smoke", p / "smoke.musicxml")
        assert "<score-partwise" in xml
        wav = export_events_to_wav(evs, p / "smoke.wav")
        assert wav.exists() and wav.stat().st_size > 10000
        mid = export_events_to_midi_file(evs, "Smoke", p / "smoke.mid")
        assert mid.exists() and mid.stat().st_size > 150

    sheet = build_sheet_music_payload(evs, "Smoke")
    assert len(sheet["notes"]) >= 20
    info = get_lan_remote_info(8765)
    assert "<svg" in info["qr_svg"] and "</svg>" in info["qr_svg"]

    # UI jargon check
    ui_html = Path("src/ui/index.html").read_text(encoding="utf-8")
    remote_html = Path("src/ui/remote.html").read_text(encoding="utf-8")
    for banned in ("モチーフ即時吸収", "多面的", "ポリフォニック・エントロピー"):
        assert banned not in ui_html, f"Banned term {banned} found in index.html"
        assert banned not in remote_html, f"Banned term {banned} found in remote.html"

    # Docs & Mermaid check
    feat_doc = Path("FEATURES_100.md").read_text(encoding="utf-8")
    assert "| 100 | `J10` |" in feat_doc
    readme = Path("README.md").read_text(encoding="utf-8")
    mermaid_blocks = re.findall(r"```mermaid\n(.*?)```", readme, flags=re.DOTALL)
    assert len(mermaid_blocks) >= 4, f"Expected >=4 mermaid diagrams, got {len(mermaid_blocks)}"


def test_fastapi_endpoints_asgi() -> None:
    import src.server.mission_control_server as srv

    srv.virtuoso_engine.stop_stream()
    srv.virtuoso_engine.workspace.state_path = Path(tempfile.gettempdir()) / "test_overhaul_ws.json"

    async def _run_all() -> None:
        code, body, _ = await _asgi_request(srv.app, "GET", "/")
        assert code == 200 and "Disklavier-Auto" in body.decode("utf-8")

        code, body, _ = await _asgi_request(srv.app, "GET", "/remote")
        assert code == 200 and "リモコン" in body.decode("utf-8")

        code, body, _ = await _asgi_request(srv.app, "GET", "/api/tabs")
        assert code == 200 and json.loads(body)["ok"]

        code, body, _ = await _asgi_request(srv.app, "POST", "/api/tabs/new", {"title": "ASGI新曲"})
        assert code == 200 and json.loads(body)["ok"]

        code, body, _ = await _asgi_request(srv.app, "POST", "/api/virtuoso/expression_pad", {"x": 0.4, "y": 0.75})
        assert code == 200 and json.loads(body)["ok"]

        code, body, _ = await _asgi_request(srv.app, "POST", "/api/cello/warmup_wave", {})
        assert code == 200 and json.loads(body)["ok"]

        code, body, _ = await _asgi_request(srv.app, "GET", "/api/score/current")
        assert code == 200 and len(json.loads(body)["notes"]) > 0

        code, body, _ = await _asgi_request(srv.app, "GET", "/api/export/midi")
        assert code == 200 and len(body) > 100

        code, body, _ = await _asgi_request(srv.app, "GET", "/api/export/musicxml")
        assert code == 200 and b"<score-partwise" in body

        code, body, _ = await _asgi_request(srv.app, "GET", "/api/export/wav")
        assert code == 200 and body[:4] == b"RIFF"

        code, body, _ = await _asgi_request(srv.app, "GET", "/api/features/catalog")
        assert code == 200 and json.loads(body)["total_count"] == 100

        code, body, _ = await _asgi_request(srv.app, "POST", "/api/features/invoke", {"feature_id": "J9"})
        assert code == 200 and json.loads(body)["ok"]

        code, body, _ = await _asgi_request(srv.app, "POST", "/api/features/invoke", {"feature_id": "A10"})
        assert code == 200 and json.loads(body)["ok"]

        code, body, _ = await _asgi_request(srv.app, "GET", "/api/system/doctor")
        assert code == 200 and json.loads(body)["ok"]

        code, body, _ = await _asgi_request(srv.app, "GET", "/api/remote/info")
        assert code == 200 and "<svg" in json.loads(body)["qr_svg"]

    asyncio.run(_run_all())
    srv.virtuoso_engine.close()
    srv.virtual_twin.close()


if __name__ == "__main__":
    test_local_only_and_skills_present()
    test_preflight_checker_sub_100ms_and_repair()
    test_human_reference_teacher_legato_vivace_and_inspiration()
    test_workspace_tabs_timemachine_and_resets()
    test_100_features_exports_warmup_and_docs()
    test_fastapi_endpoints_asgi()
    print("ALL Studio Overhaul tests passed successfully!")
