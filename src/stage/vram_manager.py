"""
Dynamic RTX 3070 (8GB VRAM) Memory Manager (`src/stage/vram_manager.py`).

Enforces two strict VRAM operating profiles inside the 8.0 GB RTX 3070 budget to
prevent Windows WDDM / CUDA shared-RAM spillover (the 3-5 tok/s memory-wall cliff):

1. `realtime_dialogue` (Co-resident budget < 6.5 GB):
   - Ollama (`qwen3.5:4b` Q4_K_M + `OLLAMA_FLASH_ATTENTION=1` + `OLLAMA_KV_CACHE_TYPE=q8_0`
     + `keep_alive=-1`, ~3.6 GB)
   - `Anticipatory Music Transformer (128M)` FP16 (~0.31 GB)
   - CUDA harmonic & corpus similarity tensors (< 0.15 GB)
   - Mode B `MIDI-GPT` accompaniment runs on CPU (0.0 GB VRAM)

2. `notagen_full` (Dedicated 8.0 GB NotaGen window):
   - Immediately evicts Ollama via `POST /api/generate` (`keep_alive: 0`)
   - Flushes CUDA caches (`gc.collect()` + `torch.cuda.empty_cache()` + `torch.cuda.ipc_collect()`)
   - Dedicates GPU headroom to `NotaGen-Small` (FP16/INT8 + PyTorch SDPA FlashAttention)
"""

from __future__ import annotations

import gc
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Literal, Optional

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore[assignment]


OwnerKind = Literal["idle", "ollama", "notagen", "phrase_transformer", "anticipatory", "dialogue_shared"]
VRAMProfileKind = Literal["realtime_dialogue", "notagen_full"]


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
    Coordinates exclusive/co-resident VRAM ownership within an 8.0 GB RTX 3070 budget.
    """

    REALTIME_DIALOGUE_CAP_GB = 6.5

    def __init__(
        self,
        total_vram_gb: float = 8.0,
        safety_limit_gb: float = 7.4,
        realtime_cap_gb: float = 6.5,
        ollama_host: str = "http://127.0.0.1:11434",
        ollama_model: str = "qwen3.5:4b",
    ) -> None:
        self.total_vram_gb = float(total_vram_gb)
        self.safety_limit_gb = float(safety_limit_gb)
        self.realtime_cap_gb = float(realtime_cap_gb)
        self.ollama_host = ollama_host.rstrip("/")
        self.ollama_model = ollama_model
        self._lock = threading.RLock()
        self.current_owner: OwnerKind = "idle"
        self.active_profile: VRAMProfileKind = "realtime_dialogue"
        self.resident_models: Dict[str, float] = {}
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
                import json
                import urllib.request

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

        with self._lock:
            if evicted:
                self.evictions_count += 1
                self.resident_models.pop("ollama", None)
        return evicted

    def register_resident(self, model_key: str, vram_gb: float) -> None:
        with self._lock:
            self.resident_models[str(model_key)] = round(max(0.0, float(vram_gb)), 3)

    def unregister_resident(self, model_key: str) -> None:
        with self._lock:
            self.resident_models.pop(str(model_key), None)

    def estimated_resident_gb(self) -> float:
        with self._lock:
            return round(sum(self.resident_models.values()), 2)

    def switch_profile(
        self,
        profile: VRAMProfileKind,
        release_callbacks: Optional[Dict[str, Callable[[], None]]] = None,
    ) -> Dict[str, Any]:
        """Switches cleanly between `realtime_dialogue` (<6.5GB co-resident) and `notagen_full` (8GB)."""
        if profile == "notagen_full":
            res = self.acquire_for("notagen", required_gb=2.4, release_callbacks=release_callbacks)
            with self._lock:
                self.active_profile = "notagen_full"
            return {**res, "active_profile": self.active_profile}

        res = self.acquire_for("dialogue_shared", required_gb=4.0, release_callbacks=release_callbacks)
        with self._lock:
            self.active_profile = "realtime_dialogue"
        return {**res, "active_profile": self.active_profile}

    def acquire_for(
        self,
        owner: OwnerKind,
        required_gb: float = 2.0,
        release_callbacks: Optional[Dict[str, Callable[[], None]]] = None,
    ) -> Dict[str, Any]:
        """
        Ensures sufficient VRAM headroom before running `owner`.
        - Co-resident dialogue profile permits `ollama` + `anticipatory` + `phrase_transformer`
          simultaneously as long as projected VRAM stays <= `realtime_cap_gb` (6.5 GB).
        - Switching to `notagen` evicts `ollama` (`keep_alive=0`) and flushes CUDA cache.
        """
        t0 = time.perf_counter()
        with self._lock:
            probe = self.probe_vram(force=True)
            used_gb = float(probe.get("used_gb", 0.0))
            total_gb = float(probe.get("total_gb", self.total_vram_gb)) or self.total_vram_gb
            is_dialogue_co_res = owner in ("ollama", "anticipatory", "phrase_transformer", "dialogue_shared")
            budget_cap = (
                min(self.realtime_cap_gb, total_gb - 1.2)
                if is_dialogue_co_res
                else min(self.safety_limit_gb, total_gb - 0.5)
            )
            projected = used_gb + float(required_gb)

            evicted_targets: List[str] = []
            if owner != self.current_owner:
                if owner == "notagen" and (self.current_owner == "ollama" or projected > budget_cap):
                    if self.evict_ollama():
                        evicted_targets.append("ollama")
                    if release_callbacks and "anticipatory" in release_callbacks and projected > budget_cap:
                        try:
                            release_callbacks["anticipatory"]()
                            evicted_targets.append("anticipatory")
                            self.resident_models.pop("anticipatory", None)
                        except Exception:
                            pass
                    self.active_profile = "notagen_full"
                elif is_dialogue_co_res:
                    if self.current_owner == "notagen":
                        if release_callbacks and "notagen" in release_callbacks:
                            try:
                                release_callbacks["notagen"]()
                                evicted_targets.append("notagen")
                                self.resident_models.pop("notagen", None)
                            except Exception:
                                pass
                    self.active_profile = "realtime_dialogue"

                if release_callbacks and owner != "phrase_transformer" and projected > budget_cap:
                    cb = release_callbacks.get("phrase_transformer")
                    if cb is not None:
                        try:
                            cb()
                            evicted_targets.append("phrase_transformer")
                            self.resident_models.pop("phrase_transformer", None)
                        except Exception:
                            pass
                self.clear_cuda_cache()

            self.current_owner = owner
            self.resident_models[str(owner)] = round(float(required_gb), 2)
            after = self.probe_vram(force=True)
            self.last_transition_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return {
                "ok": True,
                "owner": self.current_owner,
                "active_profile": self.active_profile,
                "required_gb": round(float(required_gb), 2),
                "used_gb": float(after.get("used_gb", 0.0)),
                "estimated_resident_gb": self.estimated_resident_gb(),
                "total_gb": float(after.get("total_gb", self.total_vram_gb)),
                "safety_limit_gb": round(budget_cap, 2),
                "realtime_cap_gb": self.realtime_cap_gb,
                "evicted": evicted_targets,
                "transition_ms": self.last_transition_ms,
                "cuda_available": bool(after.get("cuda_available", False)),
            }

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            probe = self.probe_vram(force=False)
            return {
                "owner": self.current_owner,
                "active_profile": self.active_profile,
                "resident_models": dict(self.resident_models),
                "estimated_resident_gb": self.estimated_resident_gb(),
                "used_gb": float(probe.get("used_gb", 0.0)),
                "free_gb": float(probe.get("free_gb", 0.0)),
                "total_gb": float(probe.get("total_gb", self.total_vram_gb)),
                "safety_limit_gb": self.safety_limit_gb,
                "realtime_cap_gb": self.realtime_cap_gb,
                "cuda_available": bool(probe.get("cuda_available", False)),
                "device_name": str(probe.get("device_name", "CPU")),
                "evictions_count": self.evictions_count,
                "last_transition_ms": self.last_transition_ms,
            }
