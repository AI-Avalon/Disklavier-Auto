"""
Remote Home RTX 3070 Connection, Latency, Auth & Failover Diagnostic Tool
(`scripts/check_remote_connection.py`).

Run from the venue M1 MacBook Pro (or locally on Windows) to verify:
  1. HTTP `/ping` RTT (`min / avg / p95 / max ms`)
  2. Bearer token authentication enforcement (`401` rejected, `200` on `/api/v1/health`)
  3. Real E2E `/api/v1/infer/deliberate` round-trip latency & 6-stage logic trace validity
  4. Real `WS /ws/v1/stream_compose` bar-by-bar streaming check (unless `--quick`)
  5. Automatic failover simulation (`HybridInferenceClient` fallback to Mac-local on timeout)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any, Dict, List

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.stage.hybrid_client import HybridInferenceClient


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return round(ordered[idx], 2)


async def _probe_ws_stream(base_url: str, token: str, timeout_s: float) -> Dict[str, Any]:
    import websockets

    ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://") + "/ws/v1/stream_compose"
    if token:
        ws_url += f"?token={token}"
    t0 = time.perf_counter()
    bars_received: List[int] = []
    first_bar_ms = 0.0
    async with websockets.connect(ws_url, open_timeout=timeout_s) as ws:
        await ws.send(
            json.dumps(
                {
                    "action": "start",
                    "prompt_ja": "接続疎通2小節ストリーミング検査",
                    "tonality": "D_minor",
                    "bars": 2,
                    "engine": "notagen_small",
                }
            )
        )
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(3.0, timeout_s * 3))
            msg = json.loads(raw)
            mtype = msg.get("type")
            if mtype == "bar":
                if not bars_received:
                    first_bar_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                bars_received.append(int(msg.get("bar", 0)))
            elif mtype == "complete":
                break
            elif mtype == "error":
                raise RuntimeError(str(msg.get("error", "WS stream error")))
    return {
        "ok": len(bars_received) >= 2,
        "bars_received": len(bars_received),
        "first_bar_ms": first_bar_ms,
        "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
    }


def run_diagnostics(
    url: str,
    token: str,
    pings: int = 5,
    timeout_ms: int = 1800,
    quick: bool = False,
    skip_failover: bool = False,
) -> Dict[str, Any]:
    base_url = url.strip().rstrip("/")
    timeout_s = max(0.3, timeout_ms / 1000.0)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    report: Dict[str, Any] = {
        "target_url": base_url,
        "timeout_ms": timeout_ms,
        "checks": {},
        "passed": False,
    }
    failures: List[str] = []

    # 1. Ping RTT
    rtts: List[float] = []
    for _ in range(max(1, pings)):
        t0 = time.perf_counter()
        try:
            r = requests.get(f"{base_url}/ping", timeout=timeout_s)
            if r.status_code != 200:
                failures.append(f"/ping HTTP {r.status_code}")
                break
            rtts.append(round((time.perf_counter() - t0) * 1000.0, 2))
        except Exception as exc:
            failures.append(f"/ping failed: {exc}")
            break

    if rtts:
        report["checks"]["ping_rtt"] = {
            "ok": True,
            "samples": len(rtts),
            "min_ms": min(rtts),
            "avg_ms": round(statistics.mean(rtts), 2),
            "p95_ms": _percentile(rtts, 95),
            "max_ms": max(rtts),
        }
    else:
        report["checks"]["ping_rtt"] = {"ok": False}

    # 2. Bearer Auth & /api/v1/health
    auth_rejected_unauth = False
    try:
        r_unauth = requests.get(f"{base_url}/api/v1/health", timeout=timeout_s)
        auth_rejected_unauth = r_unauth.status_code == 401
    except Exception:
        auth_rejected_unauth = False

    try:
        t0 = time.perf_counter()
        r_health = requests.get(f"{base_url}/api/v1/health", headers=headers, timeout=timeout_s)
        health_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        if r_health.status_code != 200:
            failures.append(f"/api/v1/health HTTP {r_health.status_code}")
            report["checks"]["health_auth"] = {
                "ok": False,
                "status_code": r_health.status_code,
                "unauth_rejected_401": auth_rejected_unauth,
            }
        else:
            hd = r_health.json()
            report["checks"]["health_auth"] = {
                "ok": True,
                "unauth_rejected_401": auth_rejected_unauth,
                "health_rtt_ms": health_ms,
                "gpu_name": hd.get("gpu_name"),
                "vram_used_gb": hd.get("vram_used_gb"),
                "vram_free_gb": hd.get("vram_free_gb"),
                "vram_total_gb": hd.get("vram_total_gb"),
                "loaded_models": hd.get("loaded_models"),
                "queue_length": hd.get("inference_queue_length"),
                "ollama_available": hd.get("ollama_available"),
            }
            if not auth_rejected_unauth:
                failures.append("Server did not return 401 for unauthenticated /api/v1/health")
    except Exception as exc:
        failures.append(f"/api/v1/health failed: {exc}")
        report["checks"]["health_auth"] = {"ok": False, "error": str(exc)}

    # 3. E2E Deliberation Inference Roundtrip
    delib_payload = {
        "mode": "cello",
        "turn_id": 1,
        "perception": {"mode": "cello", "midi_pitch": 50, "note_name": "D3", "articulation": "legato"},
        "human_notes": [
            {"pitch": 50, "velocity": 70, "onset_s": 0.0, "duration_s": 0.45},
            {"pitch": 53, "velocity": 74, "onset_s": 0.45, "duration_s": 0.45},
            {"pitch": 57, "velocity": 78, "onset_s": 0.90, "duration_s": 0.65},
        ],
        "directive_hints": {},
        "use_ollama": None,
    }
    try:
        t0 = time.perf_counter()
        r_inf = requests.post(
            f"{base_url}/api/v1/infer/deliberate",
            json=delib_payload,
            headers=headers,
            timeout=max(timeout_s, 6.0),
        )
        e2e_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        if r_inf.status_code != 200:
            failures.append(f"/api/v1/infer/deliberate HTTP {r_inf.status_code}")
            report["checks"]["e2e_deliberate"] = {"ok": False, "status_code": r_inf.status_code}
        else:
            d_json = r_inf.json()
            trace = d_json.get("logic_trace") or {}
            trace_ok = all(
                k in trace
                for k in (
                    "L1_PERCEPTION",
                    "TOKEN_ECON",
                    "L2_LOGIC_CHAIN",
                    "L3_CANDIDATES",
                    "REALIZER_DIFF",
                    "TIMING_BUDGET",
                )
            )
            cands = d_json.get("candidates") or []
            gpu_ms = float((d_json.get("timing") or {}).get("gpu_infer_ms", 0.0))
            report["checks"]["e2e_deliberate"] = {
                "ok": bool(d_json.get("ok") and len(cands) == 4 and trace_ok),
                "e2e_ms": e2e_ms,
                "gpu_infer_ms": gpu_ms,
                "net_overhead_ms": round(max(0.0, e2e_ms - gpu_ms), 2),
                "candidates_count": len(cands),
                "llm_source": d_json.get("llm_source"),
                "logic_trace_6stage_ok": trace_ok,
            }
            if not report["checks"]["e2e_deliberate"]["ok"]:
                failures.append("Remote deliberate payload incomplete")
    except Exception as exc:
        failures.append(f"/api/v1/infer/deliberate failed: {exc}")
        report["checks"]["e2e_deliberate"] = {"ok": False, "error": str(exc)}

    # 4. WebSocket Bar Streaming Check
    if not quick:
        try:
            ws_res = asyncio.run(_probe_ws_stream(base_url, token, timeout_s=max(2.5, timeout_s)))
            report["checks"]["ws_stream_compose"] = ws_res
            if not ws_res.get("ok"):
                failures.append("WS /ws/v1/stream_compose did not stream >=2 bars")
        except Exception as exc:
            failures.append(f"WS /ws/v1/stream_compose failed: {exc}")
            report["checks"]["ws_stream_compose"] = {"ok": False, "error": str(exc)}

    # 5. Failover Drill
    if not skip_failover:
        client = HybridInferenceClient(
            mode="AUTO",
            remote_url="http://127.0.0.1:19999",
            api_token="dummy",
            timeout_ms=250,
            recovery_cooldown_s=0.5,
        )
        t_fo0 = time.perf_counter()
        fo_res = client.deliberate_with_failover(
            delib_payload,
            lambda: {"ok": True, "source": "local_emergency"},
        )
        fo_ms = round((time.perf_counter() - t_fo0) * 1000.0, 2)
        client.close()
        fo_ok = bool(
            fo_res.get("ok")
            and fo_res.get("failover_triggered") is True
            and fo_res.get("backend_source") == "mac_local_failover"
        )
        report["checks"]["failover_drill"] = {
            "ok": fo_ok,
            "failover_switch_ms": fo_ms,
            "backend_source": fo_res.get("backend_source"),
        }
        if not fo_ok:
            failures.append("Hybrid failover drill did not fall back cleanly")

    report["failures"] = failures
    report["passed"] = len(failures) == 0
    return report


def _load_local_remote_env() -> None:
    """Load default connection settings from local gitignored env files if not already set."""
    allowed_keys = {
        "DISKLAVIER_REMOTE_URL",
        "DISKLAVIER_API_TOKEN",
        "DISKLAVIER_REMOTE_TIMEOUT_MS",
        "DISKLAVIER_REMOTE_HOST",
        "DISKLAVIER_HYBRID_MODE",
    }
    loaded: Dict[str, str] = {}
    for fname in (".env.remote", ".env.mac", "config.remote.env"):
        env_path = ROOT / fname
        if not env_path.is_file():
            continue
        try:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if len(val) >= 2 and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'")):
                    val = val[1:-1]
                if key in allowed_keys:
                    loaded[key] = val
        except Exception:
            pass
    for key, val in loaded.items():
        os.environ.setdefault(key, val)


def main() -> int:
    _load_local_remote_env()
    parser = argparse.ArgumentParser(description="Check connection to Home RTX 3070 Remote GPU Server")
    parser.add_argument("--url", default=os.environ.get("DISKLAVIER_REMOTE_URL", "http://127.0.0.1:8090"))
    parser.add_argument("--token", default=os.environ.get("DISKLAVIER_API_TOKEN", ""))
    parser.add_argument("--pings", type=int, default=5)
    parser.add_argument("--timeout-ms", type=int, default=int(os.environ.get("DISKLAVIER_REMOTE_TIMEOUT_MS") or 1800))
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--skip-failover", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    rep = run_diagnostics(
        url=args.url,
        token=args.token,
        pings=args.pings,
        timeout_ms=args.timeout_ms,
        quick=args.quick,
        skip_failover=args.skip_failover,
    )
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print("============================================================================")
        print(f" DISKLAVIER REMOTE RTX DIAGNOSTIC | target={rep['target_url']} | {'PASS' if rep['passed'] else 'FAIL'}")
        print("============================================================================")
        for name, detail in rep["checks"].items():
            print(f" - [{ 'PASS' if detail.get('ok') else 'FAIL' }] {name}: {detail}")
        if rep["failures"]:
            print("FAILURES:")
            for f in rep["failures"]:
                print("  *", f)
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
