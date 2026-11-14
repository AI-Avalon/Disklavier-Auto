"""Phase 1 security, authentication, path-traversal, and input validation tests."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional, Tuple

import src.server.mission_control_server as srv


async def _asgi_http(
    method: str,
    path: str,
    json_body: Optional[Dict[str, Any]] = None,
    headers: Optional[List[Tuple[bytes, bytes]]] = None,
    client_host: str = "127.0.0.1",
) -> Tuple[int, bytes, Dict[str, str]]:
    raw_body = json.dumps(json_body).encode("utf-8") if json_body is not None else b""
    req_headers = list(headers or [])
    if json_body is not None:
        req_headers.append((b"content-type", b"application/json"))
    raw_path, _, qs = path.partition("?")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": raw_path,
        "raw_path": raw_path.encode("ascii"),
        "query_string": qs.encode("ascii"),
        "headers": req_headers,
        "client": (client_host, 51234),
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

    await srv.app(scope, receive, send)
    done_event.set()
    return status_code, b"".join(body_chunks), resp_headers


async def _asgi_ws_connect(query_string: str = "", headers: Optional[List[Tuple[bytes, bytes]]] = None) -> List[Dict[str, Any]]:
    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "ws",
        "path": "/ws",
        "raw_path": b"/ws",
        "query_string": query_string.encode("ascii"),
        "headers": list(headers or []),
        "client": ("127.0.0.1", 51235),
        "server": ("127.0.0.1", 8765),
    }
    recv_queue: asyncio.Queue = asyncio.Queue()
    await recv_queue.put({"type": "websocket.connect"})
    sent_msgs: List[Dict[str, Any]] = []

    async def receive() -> Dict[str, Any]:
        return await recv_queue.get()

    from fastapi import WebSocketDisconnect

    async def send(msg: Dict[str, Any]) -> None:
        sent_msgs.append(msg)
        if msg["type"] == "websocket.send":
            raise WebSocketDisconnect(code=1000)

    await asyncio.wait_for(srv.app(scope, receive, send), timeout=3.0)
    return sent_msgs


def test_post_endpoints_require_token() -> None:
    srv.virtuoso_engine.stop_stream()

    async def _run() -> None:
        tok_header = [(b"x-studio-token", srv.STUDIO_AUTH_TOKEN.encode("ascii"))]

        # Tokenless POST -> 401
        for ep, payload in [
            ("/api/safety/test_note", {}),
            ("/api/play_chord", {"notes": [{"note": 60, "velocity": 60, "duration_s": 0.4}], "countdown_seconds": 0}),
            ("/api/play_sequence", {"events": [{"time_offset_s": 0.0, "note": 60, "velocity": 60, "duration_s": 0.3}], "countdown_seconds": 0}),
            ("/api/panic", {}),
        ]:
            code, _, _ = await _asgi_http("POST", ep, payload, headers=[])
            assert code == 401, f"Expected 401 without token on {ep}, got {code}"

            # Wrong token -> 401
            code_bad, _, _ = await _asgi_http(
                "POST", ep, payload, headers=[(b"x-studio-token", b"wrong-token")]
            )
            assert code_bad == 401, f"Expected 401 with bad token on {ep}, got {code_bad}"

        # Valid token -> 200
        code_ok, body_ok, _ = await _asgi_http(
            "POST",
            "/api/play_sequence",
            {
                "events": [{"time_offset_s": 0.0, "note": 60, "velocity": 60, "duration_s": 0.3}],
                "countdown_seconds": 0,
            },
            headers=tok_header,
        )
        assert code_ok == 200 and json.loads(body_ok)["ok"] is True

        # Remote QR info embeds token when loopback, hides when non-loopback unauthorized
        code_rem, body_rem, _ = await _asgi_http("GET", "/api/remote/info")
        assert code_rem == 200
        rem_data = json.loads(body_rem)
        assert f"?token={srv.STUDIO_AUTH_TOKEN}" in rem_data["remote_url"]

        code_rem_lan, body_rem_lan, _ = await _asgi_http(
            "GET", "/api/remote/info", client_host="192.168.1.99"
        )
        assert code_rem_lan == 200
        assert srv.STUDIO_AUTH_TOKEN not in json.loads(body_rem_lan)["remote_url"]

        # WebSocket rejects missing/bad token with close 1008 and accepts valid token
        ws_no_tok = await _asgi_ws_connect()
        assert ws_no_tok[0]["type"] == "websocket.close" and ws_no_tok[0]["code"] == 1008

        ws_ok = await _asgi_ws_connect(query_string=f"token={srv.STUDIO_AUTH_TOKEN}")
        assert ws_ok[0]["type"] == "websocket.accept"

    asyncio.run(_run())


def test_path_traversal_blocked() -> None:
    async def _run() -> None:
        tok_header = [(b"x-studio-token", srv.STUDIO_AUTH_TOKEN.encode("ascii"))]

        for bad_rel in ("../../etc/passwd", "../src/server/mission_control_server.py", "/etc/passwd", "note.txt"):
            code, _, _ = await _asgi_http(
                "POST", "/api/obsidian/play_hof", {"midi_rel": bad_rel}, headers=tok_header
            )
            assert code == 400, f"Expected 400 for bad play_hof path {bad_rel!r}, got {code}"

        for bad_chart in ("../../etc/passwd", "../experiments_summary.json", "bad.py", "sub/../../x.png"):
            code, _, _ = await _asgi_http("GET", f"/telemetry/charts/{bad_chart}")
            assert code in (400, 404), f"Expected 400/404 for chart {bad_chart!r}, got {code}"

        # Valid filename with allowed suffix that doesn't exist -> 404
        code_nf, _, _ = await _asgi_http("GET", "/telemetry/charts/nonexistent_chart_999.png")
        assert code_nf == 404

        # Invalid extension -> 400
        code_ext, _, _ = await _asgi_http("GET", "/telemetry/charts/rd_studio.sqlite")
        assert code_ext == 400

    asyncio.run(_run())


def test_sequence_and_chord_input_bounds() -> None:
    async def _run() -> None:
        tok_header = [(b"x-studio-token", srv.STUDIO_AUTH_TOKEN.encode("ascii"))]

        # > 512 events -> 422
        huge_events = [
            {"time_offset_s": 0.0, "note": 60, "velocity": 60, "duration_s": 0.2}
            for _ in range(520)
        ]
        code, _, _ = await _asgi_http(
            "POST",
            "/api/play_sequence",
            {"events": huge_events, "countdown_seconds": 0},
            headers=tok_header,
        )
        assert code == 422, f"Expected 422 for >512 events, got {code}"

        # Out-of-range note, velocity, negative offset, excessive duration -> 422
        bad_payloads = [
            {"events": [{"time_offset_s": 0.0, "note": 15, "velocity": 60, "duration_s": 0.2}], "countdown_seconds": 0},
            {"events": [{"time_offset_s": 0.0, "note": 120, "velocity": 60, "duration_s": 0.2}], "countdown_seconds": 0},
            {"events": [{"time_offset_s": 0.0, "note": 60, "velocity": 0, "duration_s": 0.2}], "countdown_seconds": 0},
            {"events": [{"time_offset_s": 0.0, "note": 60, "velocity": 200, "duration_s": 0.2}], "countdown_seconds": 0},
            {"events": [{"time_offset_s": -1.0, "note": 60, "velocity": 60, "duration_s": 0.2}], "countdown_seconds": 0},
            {"events": [{"time_offset_s": 0.0, "note": 60, "velocity": 60, "duration_s": 25.0}], "countdown_seconds": 0},
        ]
        for bp in bad_payloads:
            c, _, _ = await _asgi_http("POST", "/api/play_sequence", bp, headers=tok_header)
            assert c == 422, f"Expected 422 for invalid sequence payload {bp}, got {c}"

        # Chord > 16 notes -> 422
        too_many_notes = [{"note": 36 + i, "velocity": 60, "duration_s": 0.4} for i in range(18)]
        c_chord, _, _ = await _asgi_http(
            "POST", "/api/play_chord", {"notes": too_many_notes, "countdown_seconds": 0}, headers=tok_header
        )
        assert c_chord == 422

    asyncio.run(_run())


if __name__ == "__main__":
    test_post_endpoints_require_token()
    test_path_traversal_blocked()
    test_sequence_and_chord_input_bounds()
    srv.virtuoso_engine.close()
    srv.virtual_twin.close()
    print("ALL Phase 1 security tests passed successfully!")
