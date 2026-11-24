"""CI-safe tests for Remote RTX GPU Server, Hybrid Failover Client, 6-Stage Logic Trace,
Field Session Recorder/Replayer, Art Direction Skill installation, and cross-platform launchers.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from typing import Any, Dict, List

from fastapi.testclient import TestClient
import numpy as np
import pytest

from src.stage.field_recorder import FieldSessionRecorder, FieldSessionReplayer
from src.stage.hybrid_client import HybridInferenceClient
from src.stage.logic_trace import LogicTraceLogger
from src.stage.remote_gpu_server import create_remote_gpu_app
from src.stage.stage_server import create_stage_app

REPO_ROOT = Path(__file__).resolve().parents[1]

SIX_TRACE_SECTIONS = (
    "L1_PERCEPTION",
    "TOKEN_ECON",
    "L2_LOGIC_CHAIN",
    "L3_CANDIDATES",
    "REALIZER_DIFF",
    "TIMING_BUDGET",
)


def test_remote_gpu_server_endpoints_and_ws() -> None:
    app = create_remote_gpu_app(api_token="test-tok-123", use_ollama=False)
    with TestClient(app) as client:
        # 1. /ping is open
        ping = client.get("/ping")
        assert ping.status_code == 200
        assert ping.json()["ok"] is True

        # 2. /api/v1/health enforces Bearer auth
        assert client.get("/api/v1/health").status_code == 401
        assert client.get("/api/v1/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
        ok_h = client.get("/api/v1/health", headers={"Authorization": "Bearer test-tok-123"})
        assert ok_h.status_code == 200
        hp = ok_h.json()
        assert hp["ok"] is True
        assert hp["vram_total_gb"] > 0
        assert isinstance(hp["loaded_models"], list)
        assert "server_time_iso" in hp

        # 3. /api/v1/infer/deliberate returns 4 candidates + 6-stage logic_trace + pedal curves
        assert client.post("/api/v1/infer/deliberate", json={"mode": "cello"}).status_code == 401
        resp = client.post(
            "/api/v1/infer/deliberate",
            headers={"Authorization": "Bearer test-tok-123"},
            json={
                "mode": "cello",
                "turn_id": 2,
                "human_notes": [
                    {"pitch": 50, "onset_s": 0.0, "duration_s": 0.6, "velocity": 68},
                    {"pitch": 53, "onset_s": 0.6, "duration_s": 0.6, "velocity": 72},
                    {"pitch": 57, "onset_s": 1.2, "duration_s": 0.8, "velocity": 76},
                ],
                "use_ollama": False,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["source"] == "remote_rtx"
        assert len(body["candidates"]) == 4
        assert any(c["adopted"] for c in body["candidates"])
        assert isinstance(body["pedal_cc64_curve"], list)
        assert isinstance(body["pedal_cc67_curve"], list)
        trace = body["logic_trace"]
        for sec in SIX_TRACE_SECTIONS:
            assert sec in trace
        assert len(trace["L3_CANDIDATES"]) == 4

        # 4. WS /ws/v1/stream_compose rejects unauthorized tokens and streams authorized sessions
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/v1/stream_compose?token=bad"):
                pass

        with client.websocket_connect("/ws/v1/stream_compose?token=test-tok-123") as ws:
            ws.send_text(
                json.dumps(
                    {
                        "action": "start",
                        "seed_notes": [50, 53, 57, 62],
                        "prompt_ja": "D短調の問いかけ",
                        "tonality": "D_minor",
                        "bpm": 80,
                        "bars": 2,
                        "engine": "heuristic",
                    }
                )
            )
            msgs: List[Dict[str, Any]] = []
            for _ in range(12):
                msg = ws.receive_json()
                msgs.append(msg)
                if msg.get("type") == "complete":
                    break
            assert any(m.get("type") == "bar" for m in msgs)
            assert msgs[-1].get("type") == "complete"


def test_hybrid_client_failover_and_recovery() -> None:
    ctrl: Dict[str, Any] = {"mode": "ok", "delay_s": 0.0}
    net_events: List[Dict[str, Any]] = []

    class _MockHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # pragma: no cover
            return

        def do_GET(self) -> None:
            if self.headers.get("Authorization") != "Bearer tok-xyz":
                self.send_response(401)
                self.end_headers()
                return
            if ctrl["mode"] == "slow":
                time.sleep(float(ctrl["delay_s"]))
            payload = json.dumps({"ok": True, "gpu_name": "Mock RTX 3070", "vram_used_gb": 2.4}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length > 0:
                _ = self.rfile.read(length)
            if self.headers.get("Authorization") != "Bearer tok-xyz":
                self.send_response(401)
                self.end_headers()
                return
            if ctrl["mode"] == "slow":
                time.sleep(float(ctrl["delay_s"]))
            body = json.dumps(
                {
                    "ok": True,
                    "source": "remote_rtx",
                    "timing": {"gpu_infer_ms": 14.5},
                    "candidates": [{"candidate_id": "A", "adopted": True}],
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _MockHandler)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()

    client = HybridInferenceClient(
        mode="AUTO",
        remote_url=f"http://127.0.0.1:{port}",
        api_token="tok-xyz",
        timeout_ms=250,
        recovery_cooldown_s=0.25,
        network_event_cb=net_events.append,
    )
    local_calls = 0

    def _local_fallback() -> Dict[str, Any]:
        nonlocal local_calls
        local_calls += 1
        return {"ok": True, "local_seq": local_calls}

    try:
        # 1. Healthy remote call
        r1 = client.deliberate_with_failover({"turn_id": 1}, _local_fallback)
        assert r1["backend_source"] == "remote_rtx"
        assert r1["failover_triggered"] is False
        assert local_calls == 0

        # 2. Slow network (> timeout_ms=250ms) triggers seamless non-blocking failover
        ctrl["mode"] = "slow"
        ctrl["delay_s"] = 0.60
        r2 = client.deliberate_with_failover({"turn_id": 2}, _local_fallback)
        assert r2["backend_source"] == "mac_local_failover"
        assert r2["failover_triggered"] is True
        assert local_calls == 1
        assert client.failover_count == 1

        # 3. Restore network + wait cooldown -> automatic recovery back to remote_rtx
        ctrl["mode"] = "ok"
        ctrl["delay_s"] = 0.0
        time.sleep(0.32)
        r3 = client.deliberate_with_failover({"turn_id": 3}, _local_fallback)
        assert r3["backend_source"] == "remote_rtx"
        assert r3["recovered"] is True
        assert client.recovery_count >= 1

        # 4. MAC_LOCAL_ONLY mode skips remote cleanly
        client.configure(mode="MAC_LOCAL_ONLY")
        r4 = client.deliberate_with_failover({"turn_id": 4}, _local_fallback)
        assert r4["backend_source"] == "mac_local"
        assert local_calls == 2
    finally:
        srv.shutdown()
        srv.server_close()

    # 5. Server offline: REMOTE_RTX_ONLY raises when allow_emergency_fallback=False, survives when True
    client.configure(mode="REMOTE_RTX_ONLY")
    with pytest.raises(RuntimeError):
        client.deliberate_with_failover({"turn_id": 5}, _local_fallback, allow_emergency_fallback=False)
    r6 = client.deliberate_with_failover({"turn_id": 6}, _local_fallback, allow_emergency_fallback=True)
    assert r6["backend_source"] == "mac_local_failover"
    assert any(ev.get("failover_triggered") for ev in net_events)
    client.close()


def test_stage_server_logic_trace_and_hybrid_routes(tmp_path: Path) -> None:
    app = create_stage_app(
        demo_enabled=False,
        monitor_audio=False,
        production_mode=False,
        use_ollama=False,
        open_hardware=False,
        play_audio_device=False,
    )
    orch = app.state.orchestrator
    orch.field_recorder = FieldSessionRecorder(recordings_root=tmp_path / "rec")
    orch.logic_trace_logger = LogicTraceLogger(log_path=tmp_path / "logic_trace_live.jsonl")

    try:
        with TestClient(app) as client:
            start_rec = client.post("/api/stage/recorder/start", json={"label": "pytest_stage"})
            assert start_rec.status_code == 200
            assert start_rec.json()["active"] is True

            turn = client.post("/api/stage/turn", json={})
            assert turn.status_code == 200

            tr_resp = client.get("/api/stage/logic_trace")
            assert tr_resp.status_code == 200
            tr_data = tr_resp.json()
            assert tr_data["ok"] is True
            latest = tr_data["latest"]
            assert latest is not None
            for sec in SIX_TRACE_SECTIONS:
                assert sec in latest
            assert len(latest["L3_CANDIDATES"]) == 4

            hyb_get = client.get("/api/stage/hybrid_status")
            assert hyb_get.status_code == 200
            assert hyb_get.json()["hybrid"]["mode"] in ("AUTO", "REMOTE_RTX_ONLY", "MAC_LOCAL_ONLY")

            hyb_set = client.post(
                "/api/stage/hybrid_mode",
                json={"mode": "AUTO", "timeout_ms": 1800},
            )
            assert hyb_set.status_code == 200
            assert hyb_set.json()["hybrid"]["mode"] == "AUTO"
            assert hyb_set.json()["hybrid"]["timeout_ms"] == 1800

            stop_rec = client.post("/api/stage/recorder/stop")
            assert stop_rec.status_code == 200
            manifest = stop_rec.json()
            assert manifest["active"] is False
            assert manifest["trace_count"] >= 1
    finally:
        orch.close()


def test_field_recorder_and_replayer_roundtrip(tmp_path: Path) -> None:
    rec = FieldSessionRecorder(recordings_root=tmp_path, sample_rate=44100)
    st = rec.start(label="meikodai_dryrun")
    sdir = Path(st["session_dir"])

    rec.record_midi("in", {"type": "note_on", "pitch": 62, "velocity": 76, "channel": 0})
    rec.record_midi("out", {"type": "note_on", "pitch": 50, "velocity": 58, "channel": 0})
    rec.record_audio_block(
        np.full(512, 0.05, dtype=np.float32),
        np.full(512, 0.01, dtype=np.float32),
    )
    rec.record_logic_trace({"turn_id": 1, "L1_PERCEPTION": {"note_name": "D4"}})
    rec.record_network_event({"event": "failover", "failover_triggered": True, "rtt_ms": 1850.0})
    manifest = rec.stop()

    assert manifest["active"] is False
    assert manifest["midi_in_count"] == 1
    assert manifest["midi_out_count"] == 1
    assert manifest["audio_frames"] == 512
    assert manifest["trace_count"] == 1
    assert manifest["failover_events_count"] == 1
    assert (sdir / "midi_events.jsonl").exists()
    assert (sdir / "disklavier_io.mid").exists()
    assert (sdir / "ur22_audio.wav").exists()
    assert (sdir / "logic_trace.jsonl").exists()
    assert (sdir / "network_latency.jsonl").exists()
    assert (sdir / "session_manifest.json").exists()

    loaded = FieldSessionReplayer.load_session(sdir)
    assert loaded["wav_info"]["exists"] is True
    assert loaded["wav_info"]["channels"] == 2
    assert loaded["wav_info"]["frames"] == 512
    assert len(loaded["logic_traces"]) == 1

    timeline = FieldSessionReplayer.iter_timeline(sdir)
    streams = {item["stream"] for item in timeline}
    assert {"midi", "logic_trace", "network"}.issubset(streams)


def test_art_direction_skill_design_doc_and_launchers() -> None:
    # 1. Clean skill copies (no nested .git folders)
    for rel in (
        ".agents/skills/anything-frontend-art-direction",
        "skills/anything-frontend-art-direction",
    ):
        sdir = REPO_ROOT / rel
        assert (sdir / "SKILL.md").exists(), f"Missing {sdir / 'SKILL.md'}"
        assert not (sdir / ".git").exists(), f"Nested .git found in {sdir}"

    # 2. DESIGN_DIRECTION.md contains subject, 2-option title font comparison, and checklist records
    dd_path = REPO_ROOT / "docs" / "DESIGN_DIRECTION.md"
    assert dd_path.exists()
    dd_txt = dd_path.read_text(encoding="utf-8")
    assert "Shippori Mincho" in dd_txt
    assert "Cormorant Garamond" in dd_txt
    assert "checklists" in dd_txt.lower()

    # 3. All .bat files pure ASCII and zero chcp 65001
    for bat in REPO_ROOT.glob("*.bat"):
        raw = bat.read_bytes()
        assert raw.decode("ascii"), f"Non-ASCII character in {bat.name}"
        assert "chcp 65001" not in raw.decode("ascii").lower()

    # 4. All .sh files use LF line endings (no CRLF)
    for sh in REPO_ROOT.glob("*.sh"):
        raw = sh.read_bytes()
        assert b"\r\n" not in raw, f"CRLF line ending in {sh.name}"

    # 5. Mac Finder .command launchers and gitignored config.remote.env template
    for cname in (
        "Mac_1_Check_RTX.command",
        "Mac_2_Start_Demo_with_RTX.command",
        "Mac_3_Start_Stage_Production.command",
    ):
        cpath = REPO_ROOT / cname
        assert cpath.exists(), f"Missing macOS launcher: {cname}"
        raw_c = cpath.read_bytes()
        assert b"\r\n" not in raw_c, f"CRLF line ending in {cname}"
        txt_c = raw_c.decode("utf-8")
        assert txt_c.startswith("#!/usr/bin/env bash"), f"Missing bash shebang in {cname}"
        assert 'cd "$(dirname "$0")"' in txt_c, f"Missing repo root cd in {cname}"
        assert "config.remote.env" in txt_c, f"Missing config.remote.env loader in {cname}"
        assert "DISKLAVIER_API_TOKEN=" not in txt_c, f"Hardcoded token assignment in {cname}"

    gitignore_lines = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "config.remote.env" in gitignore_lines
    ex_path = REPO_ROOT / "config.remote.env.example"
    assert ex_path.exists()
    assert "<YOUR_DISKLAVIER_API_TOKEN>" in ex_path.read_text(encoding="utf-8")


def test_wiki_pages_present() -> None:
    wiki_dir = REPO_ROOT / "docs" / "wiki"
    expected_pages = (
        "Home.md",
        "01-Architecture-and-Hybrid-RTX-Mac.md",
        "02-Installation-Windows-RTX-and-M1-Mac.md",
        "03-Live-Stage-Operations-and-Hardware.md",
        "04-Logic-Trace-and-Musical-Engine.md",
        "05-UI-Design-Direction-and-Accessibility.md",
        "06-Performance-Benchmarks-and-QA.md",
    )
    for name in expected_pages:
        p = wiki_dir / name
        assert p.exists(), f"Missing Wiki page: {p}"
        assert len(p.read_text(encoding="utf-8").strip()) >= 200, f"Wiki page too short: {p}"

