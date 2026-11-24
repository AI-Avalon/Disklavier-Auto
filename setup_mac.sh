#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "============================================================================"
echo "  DISKLAVIER-AUTO M1/M2/M3 MACBOOK PRO VENUE SETUP"
echo "============================================================================"

# Ensure Homebrew paths (Apple Silicon / Intel) are visible
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

PYTHON_BIN=""
for cand in python3.12 python3.11 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    ver="$("$cand" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    major="${ver%%.*}"
    minor="${ver##*.}"
    if [ "$major" -eq 3 ] && [ "$minor" -ge 11 ]; then
      PYTHON_BIN="$cand"
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "[ERROR] Python 3.11 or 3.12 is required."
  echo "        Install via Homebrew: brew install python@3.12"
  exit 1
fi

echo "[1/4] Using Python: $($PYTHON_BIN --version) ($(command -v "$PYTHON_BIN"))"

if [ ! -d ".venv" ]; then
  echo "[2/4] Creating virtual environment at .venv ..."
  "$PYTHON_BIN" -m venv .venv
else
  echo "[2/4] Reusing existing .venv ..."
fi

source .venv/bin/activate
python -m pip install --upgrade pip wheel

echo "[3/4] Installing core, CoreMIDI (python-rtmidi), CoreAudio (sounddevice), and Apple Silicon deps..."
pip install -r requirements-ci.txt
pip install python-rtmidi sounddevice || echo "[WARN] Optional audio/MIDI package warning (check portaudio: brew install portaudio)"
pip install ollama || true

# Install torch for Apple Silicon MPS local fallback if not present
python -c "import torch; print('[OK] PyTorch:', torch.__version__, 'MPS:', torch.backends.mps.is_available())" 2>/dev/null || \
  pip install torch || echo "[INFO] PyTorch install skipped; lightweight local theory fallback remains active."

echo "[4/4] Scanning macOS CoreMIDI, CoreAudio (Steinberg UR22), and local Ollama fallback..."
PYTHONPATH=. python - << 'PY'
import platform
from pathlib import Path
import yaml

info = {
    "system": {
        "os": platform.system(),
        "architecture": platform.machine(),
        "apple_silicon": platform.machine() == "arm64",
    },
    "midi": {
        "preferred_in_port": "Disklavier",
        "preferred_out_port": "Disklavier",
        "detected_inputs": [],
        "detected_outputs": [],
        "delay_in_mode": "OFF",
    },
    "audio": {
        "device_keyword": "Steinberg UR22",
        "sample_rate": 44100,
        "block_size": 735,
        "ur22_device_index": None,
    },
    "hybrid": {
        "mode": "AUTO",
        "remote_url": "",
        "timeout_ms": 1800,
    },
}

try:
    import mido
    ins = list(mido.get_input_names())
    outs = list(mido.get_output_names())
    info["midi"]["detected_inputs"] = ins
    info["midi"]["detected_outputs"] = outs
    print("  CoreMIDI Inputs :", ins or ["(none)"])
    print("  CoreMIDI Outputs:", outs or ["(none)"])
except Exception as exc:
    print("  CoreMIDI scan skipped:", exc)

try:
    import sounddevice as sd
    devs = sd.query_devices()
    ur_idx = None
    for idx, d in enumerate(devs):
        name = str(d.get("name", ""))
        if int(d.get("max_input_channels", 0)) > 0 and ("ur22" in name.lower() or "steinberg" in name.lower()):
            ur_idx = idx
            break
    info["audio"]["ur22_device_index"] = ur_idx
    print("  CoreAudio UR22  :", f"device #{ur_idx} ({devs[ur_idx]['name']})" if ur_idx is not None else "Not detected (using default/virtual)")
except Exception as exc:
    print("  CoreAudio scan skipped:", exc)

cfg_path = Path("config.local.yaml")
if not cfg_path.exists():
    cfg_path.write_text(yaml.safe_dump(info, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print("  Wrote initial config.local.yaml")
else:
    print("  Kept existing config.local.yaml")
PY

if command -v ollama >/dev/null 2>&1; then
  echo "[OK] Local Ollama binary detected for Mac offline/failover fallback."
else
  echo "[INFO] Local Ollama not installed on Mac (deterministic + MPS fallback stays active if RTX drops)."
fi

echo "============================================================================"
echo "  Setup complete! Next steps:"
echo "  1. Check remote connection:"
echo "     .venv/bin/python scripts/check_remote_connection.py --url http://<TS_IP>:8090 --token <TOKEN>"
echo "  2. Launch venue stage:"
echo "     ./start_mac_stage.sh --remote-url http://<TS_IP>:8090 --token <TOKEN> --mode AUTO --production"
echo "============================================================================"
