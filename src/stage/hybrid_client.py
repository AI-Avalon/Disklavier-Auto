"""
Hybrid Venue M1 Mac <-> Home RTX 3070 Client with Zero-Dropout Auto-Failover
(`src/stage/hybrid_client.py`).

Supports 3 instant runtime modes:
  - `AUTO`            : Prefer Remote RTX 3070; seamlessly fall back to local M1 Mac engine
                        whenever latency exceeds `timeout_ms` (default 1800ms) or link drops,
                        and automatically recover once `/api/v1/health` succeeds again.
  - `REMOTE_RTX_ONLY` : Strict Remote RTX inference (never switches to local except when
                        explicitly allowed by emergency guard).
  - `MAC_LOCAL_ONLY`  : Standalone offline venue execution on M1 MacBook Pro.
"""

from __future__ import annotations

from collections import deque
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Deque, Dict, Literal, Optional

import requests

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

HybridMode = Literal["AUTO", "REMOTE_RTX_ONLY", "MAC_LOCAL_ONLY"]

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.local.yaml"


def _load_hybrid_defaults() -> Dict[str, Any]:
    if yaml is not None and DEFAULT_CONFIG_PATH.exists():
        try:
            raw = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("hybrid"), dict):
                return dict(raw["hybrid"])
        except Exception:
            pass
    return {}


class HybridInferenceClient:
    """Thread-safe remote GPU inference client with sub-second failover & auto recovery."""

    VALID_MODES = ("AUTO", "REMOTE_RTX_ONLY", "MAC_LOCAL_ONLY")

    def __init__(
        self,
        mode: Optional[str] = None,
        remote_url: Optional[str] = None,
        api_token: Optional[str] = None,
        timeout_ms: Optional[int] = None,
        recovery_cooldown_s: float = 4.0,
        network_event_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        cfg = _load_hybrid_defaults()
        raw_url = (
            remote_url
            if remote_url is not None
            else (os.environ.get("DISKLAVIER_REMOTE_URL") or cfg.get("remote_url") or "")
        )
        raw_tok = (
            api_token
            if api_token is not None
            else (os.environ.get("DISKLAVIER_API_TOKEN") or cfg.get("api_token") or "")
        )
        raw_mode = (
            mode
            if mode is not None
            else (
                os.environ.get("DISKLAVIER_HYBRID_MODE")
                or cfg.get("mode")
                or ("AUTO" if str(raw_url).strip() else "MAC_LOCAL_ONLY")
            )
        )
        raw_timeout = (
            timeout_ms
            if timeout_ms is not None
            else int(os.environ.get("DISKLAVIER_REMOTE_TIMEOUT_MS") or cfg.get("timeout_ms") or 1800)
        )

        self._lock = threading.RLock()
        self.mode: HybridMode = self._normalize_mode(raw_mode)
        self.remote_url: str = str(raw_url).strip().rstrip("/")
        self.api_token: str = str(raw_tok).strip()
        self.timeout_ms: int = max(150, min(15000, int(raw_timeout)))
        self.recovery_cooldown_s: float = max(0.2, float(recovery_cooldown_s))
        self._network_event_cb = network_event_cb

        self._session = requests.Session()
        self.failover_active: bool = False
        self.remote_healthy: bool = bool(self.remote_url)
        self.active_backend: str = "remote_rtx" if (self.remote_url and self.mode != "MAC_LOCAL_ONLY") else "mac_local"
        self.failover_count: int = 0
        self.recovery_count: int = 0
        self.consecutive_failures: int = 0
        self.circuit_open_until: float = 0.0
        self.last_rtt_ms: float = 0.0
        self.last_gpu_infer_ms: float = 0.0
        self.last_error: str = ""
        self.last_remote_health: Dict[str, Any] = {}
        self.latency_log: Deque[Dict[str, Any]] = deque(maxlen=64)

        self._running: bool = True
        self._probing_recovery: bool = False

    @classmethod
    def _normalize_mode(cls, raw_mode: str) -> HybridMode:
        m = str(raw_mode or "AUTO").strip().upper()
        if m in cls.VALID_MODES:
            return m  # type: ignore[return-value]
        return "AUTO"

    def set_network_event_callback(self, cb: Optional[Callable[[Dict[str, Any]], None]]) -> None:
        with self._lock:
            self._network_event_cb = cb

    def configure(
        self,
        mode: Optional[str] = None,
        remote_url: Optional[str] = None,
        api_token: Optional[str] = None,
        timeout_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            if mode is not None:
                self.mode = self._normalize_mode(mode)
            if remote_url is not None:
                self.remote_url = str(remote_url).strip().rstrip("/")
            if api_token is not None:
                self.api_token = str(api_token).strip()
            if timeout_ms is not None:
                self.timeout_ms = max(150, min(15000, int(timeout_ms)))

            if self.mode == "MAC_LOCAL_ONLY" or not self.remote_url:
                self.failover_active = False
                self.active_backend = "mac_local"
            else:
                self.circuit_open_until = 0.0
                if not self.failover_active:
                    self.active_backend = "remote_rtx"
            return self.get_status()

    def should_try_remote(self) -> bool:
        with self._lock:
            return self.mode != "MAC_LOCAL_ONLY" and bool(self.remote_url)

    def _headers(self) -> Dict[str, str]:
        hdrs = {"Content-Type": "application/json"}
        if self.api_token:
            hdrs["Authorization"] = f"Bearer {self.api_token}"
        return hdrs

    def _emit_network_event(self, ev: Dict[str, Any]) -> None:
        self.latency_log.append(ev)
        cb = self._network_event_cb
        if cb is not None:
            try:
                cb(ev)
            except Exception:
                pass

    def probe_health(self, timeout_s: float = 1.5) -> Dict[str, Any]:
        with self._lock:
            url = self.remote_url
            headers = self._headers()
        if not url:
            return {"ok": False, "error": "remote_url_not_configured", "rtt_ms": 0.0}

        t0 = time.perf_counter()
        try:
            resp = self._session.get(f"{url}/api/v1/health", headers=headers, timeout=max(0.2, float(timeout_s)))
            rtt_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            if resp.status_code != 200:
                err = f"HTTP {resp.status_code}"
                with self._lock:
                    self.remote_healthy = False
                    self.last_error = err
                self._emit_network_event({"event": "health_fail", "rtt_ms": rtt_ms, "error": err})
                return {"ok": False, "status_code": resp.status_code, "error": err, "rtt_ms": rtt_ms}

            data = resp.json()
            recovered = False
            with self._lock:
                if self.failover_active:
                    self.failover_active = False
                    self.recovery_count += 1
                    recovered = True
                self.remote_healthy = True
                self.consecutive_failures = 0
                self.circuit_open_until = 0.0
                self.last_rtt_ms = rtt_ms
                self.last_error = ""
                self.last_remote_health = dict(data)
                if self.mode != "MAC_LOCAL_ONLY":
                    self.active_backend = "remote_rtx"

            self._emit_network_event(
                {
                    "event": "recovery" if recovered else "health_ok",
                    "rtt_ms": rtt_ms,
                    "vram_used_gb": data.get("vram_used_gb"),
                }
            )
            return {"ok": True, "rtt_ms": rtt_ms, "recovered": recovered, **data}
        except Exception as exc:
            rtt_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            with self._lock:
                self.remote_healthy = False
                self.last_error = str(exc)
            self._emit_network_event({"event": "health_error", "rtt_ms": rtt_ms, "error": str(exc)})
            return {"ok": False, "error": str(exc), "rtt_ms": rtt_ms}

    def _trigger_async_recovery_probe(self) -> None:
        with self._lock:
            if not self._running or self._probing_recovery or not self.remote_url:
                return
            self._probing_recovery = True

        def _probe_worker() -> None:
            try:
                self.probe_health(timeout_s=min(1.5, max(0.3, self.timeout_ms / 1000.0)))
            finally:
                with self._lock:
                    self._probing_recovery = False

        threading.Thread(target=_probe_worker, name="HybridRecoveryProbe", daemon=True).start()

    def deliberate_with_failover(
        self,
        payload: Dict[str, Any],
        local_fallback_fn: Callable[[], Dict[str, Any]],
        allow_emergency_fallback: bool = True,
    ) -> Dict[str, Any]:
        """
        Executes turn inference via Remote RTX 3070 with transparent failover to `local_fallback_fn`.
        Guarantees musical continuity (zero crashes or hangs) when network degrades or drops.
        """
        with self._lock:
            mode = self.mode
            url = self.remote_url
            headers = self._headers()
            timeout_s = max(0.15, self.timeout_ms / 1000.0)
            in_cooldown = self.failover_active and time.perf_counter() < self.circuit_open_until

        if mode == "MAC_LOCAL_ONLY" or not url:
            res = local_fallback_fn()
            res["backend_source"] = "mac_local"
            res["network_rtt_ms"] = 0.0
            res["failover_triggered"] = False
            with self._lock:
                self.active_backend = "mac_local"
            return res

        if mode == "AUTO" and in_cooldown:
            self._trigger_async_recovery_probe()
            res = local_fallback_fn()
            res["backend_source"] = "mac_local_failover"
            res["network_rtt_ms"] = 0.0
            res["failover_triggered"] = False
            with self._lock:
                self.active_backend = "mac_local_failover"
            return res

        t_req0 = time.perf_counter()
        try:
            resp = self._session.post(
                f"{url}/api/v1/infer/deliberate",
                json=payload,
                headers=headers,
                timeout=(min(1.0, timeout_s), timeout_s),
            )
            http_ms = round((time.perf_counter() - t_req0) * 1000.0, 2)
            if resp.status_code != 200:
                raise RuntimeError(f"Remote GPU HTTP {resp.status_code}")

            data = resp.json()
            if not isinstance(data, dict) or not data.get("ok"):
                raise RuntimeError("Remote GPU returned invalid payload")

            gpu_infer_ms = float((data.get("timing") or {}).get("gpu_infer_ms", 0.0) or 0.0)
            net_rtt_ms = round(max(0.5, http_ms - gpu_infer_ms), 2)

            recovered = False
            with self._lock:
                if self.failover_active:
                    self.failover_active = False
                    self.recovery_count += 1
                    recovered = True
                self.remote_healthy = True
                self.consecutive_failures = 0
                self.circuit_open_until = 0.0
                self.active_backend = "remote_rtx"
                self.last_rtt_ms = net_rtt_ms
                self.last_gpu_infer_ms = gpu_infer_ms
                self.last_error = ""

            self._emit_network_event(
                {
                    "event": "recovery_infer" if recovered else "remote_infer_ok",
                    "http_ms": http_ms,
                    "rtt_ms": net_rtt_ms,
                    "gpu_infer_ms": gpu_infer_ms,
                    "recovered": recovered,
                }
            )
            data["backend_source"] = "remote_rtx"
            data["network_rtt_ms"] = net_rtt_ms
            data["http_roundtrip_ms"] = http_ms
            data["failover_triggered"] = False
            data["recovered"] = recovered
            return data

        except Exception as exc:
            fail_ms = round((time.perf_counter() - t_req0) * 1000.0, 2)
            err_msg = str(exc)

            if mode == "REMOTE_RTX_ONLY" and not allow_emergency_fallback:
                with self._lock:
                    self.remote_healthy = False
                    self.last_error = err_msg
                raise RuntimeError(f"REMOTE_RTX_ONLY failure: {err_msg}") from exc

            with self._lock:
                self.remote_healthy = False
                self.failover_active = True
                self.failover_count += 1
                self.consecutive_failures += 1
                self.circuit_open_until = time.perf_counter() + self.recovery_cooldown_s
                self.active_backend = "mac_local_failover"
                self.last_rtt_ms = fail_ms
                self.last_error = err_msg

            self._emit_network_event(
                {
                    "event": "failover",
                    "failover_triggered": True,
                    "elapsed_ms": fail_ms,
                    "error": err_msg,
                }
            )

            res = local_fallback_fn()
            res["backend_source"] = "mac_local_failover"
            res["network_rtt_ms"] = fail_ms
            res["failover_triggered"] = True
            res["failover_reason"] = err_msg
            return res

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "mode": self.mode,
                "remote_url": self.remote_url,
                "has_token": bool(self.api_token),
                "timeout_ms": self.timeout_ms,
                "active_backend": self.active_backend,
                "remote_healthy": self.remote_healthy,
                "failover_active": self.failover_active,
                "failover_count": self.failover_count,
                "recovery_count": self.recovery_count,
                "last_rtt_ms": self.last_rtt_ms,
                "last_gpu_infer_ms": self.last_gpu_infer_ms,
                "last_error": self.last_error,
                "last_remote_health": dict(self.last_remote_health),
                "recent_latency_log": list(self.latency_log)[-12:],
            }

    def close(self) -> None:
        with self._lock:
            self._running = False
        try:
            self._session.close()
        except Exception:
            pass
