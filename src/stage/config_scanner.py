"""
Environment Scanner & config.local.yaml Generator for Disklavier-Auto Stage System.
Scans MIDI ports (Disklavier / loopMIDI), Audio devices (Steinberg UR22 WASAPI/ASIO),
Ollama local models (VRAM <= 8GB safe selection), and MIDI corpus folders.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.local.yaml"

PREFERRED_OLLAMA_MODELS = [
    "qwen3.5:4b",
    "qwen3:4b",
    "qwen2.5:7b",
    "gemma3:4b",
    "qwen2.5:3b",
    "llama3.2:3b",
]


def scan_midi_ports() -> Dict[str, Any]:
    inputs: List[str] = []
    outputs: List[str] = []
    error: Optional[str] = None
    try:
        import mido

        inputs = list(mido.get_input_names())
        outputs = list(mido.get_output_names())
    except Exception as exc:
        error = str(exc)

    def _pick_preferred(names: List[str]) -> Optional[str]:
        for kw in ("disklavier", "yamaha", "usb-midi", "loopmidi"):
            for name in names:
                if kw in name.lower():
                    return name
        return None

    return {
        "inputs": inputs,
        "outputs": outputs,
        "preferred_in": _pick_preferred(inputs),
        "preferred_out": _pick_preferred(outputs),
        "error": error,
    }


def scan_audio_devices() -> Dict[str, Any]:
    input_devices: List[Dict[str, Any]] = []
    output_devices: List[Dict[str, Any]] = []
    preferred_ur22: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    try:
        import sounddevice as sd

        hostapis = sd.query_hostapis()
        devices = sd.query_devices()

        def _rank(hostapi_idx: int) -> int:
            if 0 <= hostapi_idx < len(hostapis):
                hname = str(hostapis[hostapi_idx].get("name", "")).lower()
                if "wasapi" in hname:
                    return 0
                if "asio" in hname:
                    return 1
                if "core audio" in hname:
                    return 0
            return 2

        ur22_matches = []
        for idx, dev in enumerate(devices):
            h_idx = int(dev.get("hostapi", -1))
            h_name = hostapis[h_idx]["name"] if 0 <= h_idx < len(hostapis) else "Unknown"
            entry = {
                "index": idx,
                "name": dev["name"],
                "hostapi": h_name,
                "max_input_channels": int(dev["max_input_channels"]),
                "max_output_channels": int(dev["max_output_channels"]),
                "default_samplerate": float(dev["default_samplerate"]),
            }
            if entry["max_input_channels"] > 0:
                input_devices.append(entry)
                if "steinberg" in dev["name"].lower() or "ur22" in dev["name"].lower():
                    ur22_matches.append((_rank(h_idx), entry))
            if entry["max_output_channels"] > 0:
                output_devices.append(entry)

        if ur22_matches:
            ur22_matches.sort(key=lambda item: item[0])
            preferred_ur22 = ur22_matches[0][1]
    except Exception as exc:
        error = str(exc)

    return {
        "input_count": len(input_devices),
        "output_count": len(output_devices),
        "preferred_ur22": preferred_ur22,
        "input_devices": input_devices[:16],
        "output_devices": output_devices[:16],
        "error": error,
    }


def scan_ollama_environment(host: str = "http://127.0.0.1:11434") -> Dict[str, Any]:
    installed_models: List[str] = []
    running = False
    selected_model: Optional[str] = None
    error: Optional[str] = None
    try:
        import ollama

        client = ollama.Client(host=host)
        resp = client.list()
        raw_models = getattr(resp, "models", None) or (resp.get("models", []) if isinstance(resp, dict) else [])
        for m in raw_models:
            name = getattr(m, "model", None) or (m.get("model") or m.get("name") if isinstance(m, dict) else None)
            if name:
                installed_models.append(str(name))
        running = True
        for pref in PREFERRED_OLLAMA_MODELS:
            for inst in installed_models:
                if inst.lower() == pref or inst.lower().startswith(pref.split(":")[0] + ":"):
                    if ":27b" not in inst.lower() and ":32b" not in inst.lower() and ":70b" not in inst.lower():
                        selected_model = inst
                        break
            if selected_model:
                break
        if not selected_model and installed_models:
            # Avoid oversized >10GB models on 8GB VRAM unless nothing else exists
            small = [m for m in installed_models if not any(sz in m.lower() for sz in (":27b", ":32b", ":70b", ":72b"))]
            selected_model = small[0] if small else installed_models[0]
    except Exception as exc:
        error = str(exc)

    return {
        "running": running,
        "host": host,
        "installed_models": installed_models,
        "selected_model": selected_model,
        "error": error,
    }


def scan_midi_directories(extra_dirs: Optional[List[Path]] = None) -> Dict[str, Any]:
    candidates = extra_dirs or [
        Path(r"D:\Disklavier-Data"),
        REPO_ROOT / "data" / "imslp_midi",
        REPO_ROOT / "data" / "human_references",
        REPO_ROOT / "data" / "maestro-v3.0.0",
        REPO_ROOT / "data" / "midi",
    ]
    found: Dict[str, Any] = {}
    active_corpus_dir: Optional[str] = None
    total_midis = 0

    for d in candidates:
        count = 0
        if d.exists() and d.is_dir():
            count = sum(1 for _ in d.rglob("*.mid")) + sum(1 for _ in d.rglob("*.midi"))
        found[str(d)] = {"exists": d.exists(), "midi_count": count}
        total_midis += count
        if active_corpus_dir is None and count > 0:
            active_corpus_dir = str(d)

    return {
        "directories": found,
        "total_midi_files": total_midis,
        "active_corpus_dir": active_corpus_dir,
        "fallback_seed_dictionary": active_corpus_dir is None,
    }


def scan_gpu_vram() -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "platform": platform.platform(),
        "cpu_count": os.cpu_count() or 4,
        "cuda_available": False,
        "gpu_name": None,
        "vram_gb": 0.0,
    }
    try:
        import importlib.util

        if importlib.util.find_spec("torch") is not None:
            import torch

            if torch.cuda.is_available():
                info["cuda_available"] = True
                info["gpu_name"] = torch.cuda.get_device_name(0)
                props = torch.cuda.get_device_properties(0)
                info["vram_gb"] = round(props.total_memory / (1024**3), 2)
    except Exception:
        pass
    return info


def build_local_config(output_path: Path = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    midi = scan_midi_ports()
    audio = scan_audio_devices()
    ollama_info = scan_ollama_environment()
    corpus = scan_midi_directories()
    gpu = scan_gpu_vram()

    config: Dict[str, Any] = {
        "system": {
            "os": platform.system(),
            "architecture": platform.machine(),
            "vram_budget_gb": 8.0,
            "gpu": gpu,
        },
        "midi": {
            "preferred_in_port": midi["preferred_in"] or "Disklavier",
            "preferred_out_port": midi["preferred_out"] or "Disklavier",
            "detected_inputs": midi["inputs"],
            "detected_outputs": midi["outputs"],
            "delay_in_mode": "OFF",
        },
        "audio": {
            "device_keyword": "Steinberg UR22",
            "sample_rate": 44100,
            "block_size": 735,
            "preferred_device": audio["preferred_ur22"],
            "input_devices_count": audio["input_count"],
        },
        "llm": {
            "provider": "ollama",
            "host": ollama_info["host"],
            "ollama_running": ollama_info["running"],
            "selected_model": ollama_info["selected_model"] or "qwen3.5:4b",
            "installed_models": ollama_info["installed_models"],
            "temperature": 0.2,
            "num_ctx": 2048,
            "keep_alive": -1,
            "flash_attention": True,
            "kv_cache_type": "q8_0",
        },
        "corpus": {
            "primary_dirs": [
                r"D:\Disklavier-Data",
                "data/imslp_midi",
                "data/human_references",
                "data/maestro-v3.0.0",
                "data/midi",
            ],
            "active_corpus_dir": corpus["active_corpus_dir"],
            "total_midi_files": corpus["total_midi_files"],
            "zero_config_fallback": corpus["fallback_seed_dictionary"],
        },
        "safety": {
            "hard_polyphony_limit": 30,
            "velocity_min": 18,
            "velocity_max": 112,
            "min_retrigger_ms": 60.0,
            "pedal_min_interval_ms": 40.0,
            "pedal_deadband": 3,
            "note_ttl_s": 10.0,
        },
    }

    output_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return config


def main() -> int:
    cfg = build_local_config()
    print("=== Disklavier-Auto Environment Scan ===")
    print(f"MIDI IN ports  : {cfg['midi']['detected_inputs']}")
    print(f"MIDI OUT ports : {cfg['midi']['detected_outputs']}")
    print(f"Audio UR22     : {cfg['audio']['preferred_device'] or 'Not detected (Virtual/Default)'}")
    print(f"Ollama running : {cfg['llm']['ollama_running']} | Selected: {cfg['llm']['selected_model']}")
    print(f"MIDI files     : {cfg['corpus']['total_midi_files']} (Fallback={cfg['corpus']['zero_config_fallback']})")
    print(f"Saved config   : {DEFAULT_CONFIG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
