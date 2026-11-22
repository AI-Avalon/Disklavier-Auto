"""
Dynamic RTX 3070 (8GB VRAM) Memory Manager (`src/stage/vram_manager.py`).

Prevents OOM crashes when alternating between local Ollama models (`qwen3.5:4b`, ~3.1 GB),
NotaGen / PyTorch sequence models (`NotaGen-Small`, ~2.2 GB fp16), and the RTX 3070
`ClassicalPhraseTransformerNet`.

Key capabilities:
1. Live VRAM probing via PyTorch `cuda.mem_get_info` or non-blocking `nvidia-smi`.
2. Clean Ollama eviction via `POST /api/generate` with `keep_alive: 0`.
3. CUDA cache flushing (`gc.collect()` + `torch.cuda.empty_cache()`).
4. Owner arbitration (`acquire_for`) enforcing the 7.4 GB / 8.0 GB safety envelope.
"""

from __future__ import annotations

import gc
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, Literal, Optional

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore[assignment]


OwnerKind = Literal["idle", "ollama", "notagen", "phrase_transformer"]


def _probe_vram_metrics() -> Dict[str, Any]:
    """Probes live GPU VRAM without failing on CPU-only / CI environments."""
    torch_mod = sys.modules.get("torch")
    if torch_mod is not None:
        try:
            if torch_mod.cuda.is_available():
                free_b, total_b = torch_mod.cuda.mem_get_info(0)
                used_b = max(0, int(total_b) - int(free_b))
                return {
                    "cuda_available": True,
                    "device_name": str(torch_mod.cuda.get_device_name(0)),
                    "used_gb": round(used_b / (1024.0 ** 3), 2),
                    "free_gb": round(free_b / (1024.0 ** 3), 2),
                    "total_gb": round(total_b / (1024.0 ** 3), 2),
                    "source": "torch_cuda",
                }
        except Exception:
            pass

    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.free,memory.total",
                "--format=csv,noheader,nounits",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=1.2,
            check=False,
            creationflags=flags,
        )
        if res.returncode == 0 and res.stdout.strip():
            parts = [p.strip() for p in res.stdout.strip().splitlines()[0].split(",")]
            if len(parts) >= 4:
                used_mib = float(parts[1])
                free_mib = float(parts[2])
                total_mib = float(parts[3])
                return {
                    "cuda_available": True,
                    "device_name": parts[0],
                    "used_gb": round(used_mib / 1024.0, 2),
                    "free_gb": round(free_mib / 1024.0, 2),
                    "total_gb": round(total_mib / 1024.0, 2),
                    "source": "nvidia_smi",
                }
    except Exception:
        pass

    return {
        "cuda_available": False,
        "device_name": "CPU (No CUDA GPU)",
        "used_gb": 0.0,
        "free_gb": 0.0,
        "total_gb": 8.0,
        "source": "cpu_fallback",
    }


class RTX3070VRAMManager:
    """
    Coordinates exclusive/shared VRAM ownership within an 8.0 GB RTX 3070 budget.
    """

    def __init__(
        self,
        total_vram_gb: float = 8.0,
        safety_limit_gb: float = 7.4,
        ollama_host: str = "http://127.0.0.1:11434",
        ollama_model: str = "qwen3.5:4b",
    ) -> None:
        self.total_vram_gb = float(total_vram_gb)
        self.safety_limit_gb = float(safety_limit_gb)
        self.ollama_host = ollama_host.rstrip("/")
        self.ollama_model = ollama_model
        self._lock = threading.RLock()
        self.current_owner: OwnerKind = "idle"
        self.evictions_count: int = 0
        self.last_transition_ms: float = 0.0
        self._last_probe: Dict[str, Any] = _probe_vram_metrics()
        self._last_probe_ts: float = time.perf_counter()

    def probe_vram(self, force: bool = False) -> Dict[str, Any]:
        now = time.perf_counter()
        with self._lock:
            if force or (now - self._last_probe_ts) >= 2.0:
                self._last_probe = _probe_vram_metrics()
                self._last_probe_ts = now
            return dict(self._last_probe)

    def clear_cuda_cache(self) -> None:
        gc.collect()
        torch_mod = sys.modules.get("torch")
        if torch_mod is not None:
            try:
                if torch_mod.cuda.is_available():
                    torch_mod.cuda.empty_cache()
                    torch_mod.cuda.ipc_collect()
            except Exception:
                pass

    def evict_ollama(self, model_name: Optional[str] = None) -> bool:
        """Unloads Ollama model from RTX 3070 VRAM by posting `keep_alive: 0`."""
        target_model = model_name or self.ollama_model
        evicted = False
        if requests is not None:
            try:
                r = requests.post(
                    f"{self.ollama_host}/api/generate",
                    json={"model": target_model, "prompt": "", "keep_alive": 0, "stream": False},
                    timeout=3.0,
                )
                evicted = r.status_code == 200
            except Exception:
                evicted = False
        else:
            try:
                import urllib.request
                import json

                req = urllib.request.Request(
                    f"{self.ollama_host}/api/generate",
                    data=json.dumps(
                        {"model": target_model, "prompt": "", "keep_alive": 0, "stream": False}
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    evicted = resp.status == 200
            except Exception:
                evicted = False

        if evicted:
            self.evictions_count += 1
        return evicted

    def acquire_for(
        self,
        owner: OwnerKind,
        required_gb: float = 2.0,
        release_callbacks: Optional[Dict[str, Callable[[], None]]] = None,
    ) -> Dict[str, Any]:
        """
        Ensures sufficient VRAM headroom (<= safety_limit_gb) before running `owner`.
        If switching between Ollama and NotaGen/PyTorch would exceed `safety_limit_gb`,
        automatically evicts the inactive model and empties CUDA cache.
        """
        t0 = time.perf_counter()
        with self._lock:
            probe = self.probe_vram(force=True)
            used_gb = float(probe.get("used_gb", 0.0))
            total_gb = float(probe.get("total_gb", self.total_vram_gb)) or self.total_vram_gb
            budget_cap = min(self.safety_limit_gb, total_gb - 0.5)
            projected = used_gb + float(required_gb)

            evicted_targets = []
            if owner != self.current_owner:
                if owner == "notagen" and (self.current_owner == "ollama" or projected > budget_cap):
                    if self.evict_ollama():
                        evicted_targets.append("ollama")
                elif owner == "ollama" and self.current_owner == "notagen":
                    if release_callbacks and "notagen" in release_callbacks:
                        try:
                            release_callbacks["notagen"]()
                            evicted_targets.append("notagen")
                        except Exception:
                            pass
                if release_callbacks and owner != "phrase_transformer" and projected > budget_cap:
                    cb = release_callbacks.get("phrase_transformer")
                    if cb is not None:
                        try:
                            cb()
                            evicted_targets.append("phrase_transformer")
                        except Exception:
                            pass
                self.clear_cuda_cache()

            self.current_owner = owner
            after = self.probe_vram(force=True)
            self.last_transition_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return {
                "ok": True,
                "owner": self.current_owner,
                "required_gb": round(float(required_gb), 2),
                "used_gb": float(after.get("used_gb", 0.0)),
                "total_gb": float(after.get("total_gb", self.total_vram_gb)),
                "safety_limit_gb": round(budget_cap, 2),
                "evicted": evicted_targets,
                "transition_ms": self.last_transition_ms,
                "cuda_available": bool(after.get("cuda_available", False)),
            }

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            probe = self.probe_vram(force=False)
            return {
                "owner": self.current_owner,
                "used_gb": float(probe.get("used_gb", 0.0)),
                "free_gb": float(probe.get("free_gb", 0.0)),
                "total_gb": float(probe.get("total_gb", self.total_vram_gb)),
                "safety_limit_gb": self.safety_limit_gb,
                "cuda_available": bool(probe.get("cuda_available", False)),
                "device_name": str(probe.get("device_name", "CPU")),
                "evictions_count": self.evictions_count,
                "last_transition_ms": self.last_transition_ms,
            }
