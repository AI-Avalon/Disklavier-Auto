#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export PYTHONUTF8=1

if [ ! -f ".venv/bin/python" ]; then
  echo "[ERROR] .venv not found. Run ./setup_mac.sh first."
  exit 1
fi

# Optional local overrides from .env.mac
if [ -f ".env.mac" ]; then
  set -a
  # shellcheck disable=SC1091
  source ".env.mac"
  set +a
fi

HOST="127.0.0.1"
PORT="${DISKLAVIER_STAGE_PORT:-8000}"
MODE="${DISKLAVIER_HYBRID_MODE:-AUTO}"
REMOTE_URL="${DISKLAVIER_REMOTE_URL:-}"
TOKEN="${DISKLAVIER_API_TOKEN:-}"
TIMEOUT_MS="${DISKLAVIER_REMOTE_TIMEOUT_MS:-1800}"
STAGE_MODE="demo"
RECORD_FLAG="0"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --remote-url) REMOTE_URL="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --timeout-ms) TIMEOUT_MS="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --production) STAGE_MODE="production"; shift ;;
    --demo) STAGE_MODE="demo"; shift ;;
    --record) RECORD_FLAG="1"; shift ;;
    --lan) HOST="0.0.0.0"; shift ;;
    *) echo "[WARN] Unknown option: $1"; shift ;;
  esac
done

source .venv/bin/activate
export PYTHONPATH="$(pwd)"
export DISKLAVIER_HYBRID_MODE="$MODE"
export DISKLAVIER_REMOTE_URL="$REMOTE_URL"
export DISKLAVIER_API_TOKEN="$TOKEN"
export DISKLAVIER_REMOTE_TIMEOUT_MS="$TIMEOUT_MS"
export DISKLAVIER_FIELD_RECORD="$RECORD_FLAG"

if [ "$STAGE_MODE" = "production" ]; then
  export DISKLAVIER_STAGE_PRODUCTION=1
  export DISKLAVIER_STAGE_DEMO=0
  export DISKLAVIER_MONITOR_AUDIO=0
  export DISKLAVIER_FIELD_RECORD=1
  SRV_ARGS="--production"
  URL_SUFFIX="/stage"
else
  export DISKLAVIER_STAGE_PRODUCTION=0
  export DISKLAVIER_STAGE_DEMO=1
  export DISKLAVIER_MONITOR_AUDIO=1
  SRV_ARGS="--demo"
  URL_SUFFIX="/studio?demo=1"
fi

# Resolve free TCP port if preferred port is busy
BIND_PORT="$(python - "$HOST" "$PORT" << 'PY'
import socket, sys
host = sys.argv[1]
pref = int(sys.argv[2])
for p in range(pref, pref + 21):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, p))
            print(p)
            raise SystemExit(0)
        except OSError:
            continue
print(pref)
PY
)"

echo "============================================================================"
echo "  DISKLAVIER-AUTO VENUE M1 MAC STAGE LAUNCHER"
echo "  Stage Mode    : $STAGE_MODE ($SRV_ARGS)"
echo "  Hybrid Mode   : $DISKLAVIER_HYBRID_MODE (timeout=${DISKLAVIER_REMOTE_TIMEOUT_MS}ms)"
echo "  Remote RTX    : ${DISKLAVIER_REMOTE_URL:-'(Mac Local Standalone)'}"
echo "  Field Recorder: DISKLAVIER_FIELD_RECORD=$DISKLAVIER_FIELD_RECORD"
echo "  Local HUD URL : http://127.0.0.1:${BIND_PORT}${URL_SUFFIX}"
echo "============================================================================"

# Quick pre-flight device display
python - << 'PY'
try:
    import mido
    print("[CoreMIDI IN ]", mido.get_input_names() or ["(none)"])
    print("[CoreMIDI OUT]", mido.get_output_names() or ["(none)"])
except Exception as e:
    print("[CoreMIDI] Unavailable:", e)
try:
    import sounddevice as sd
    ins = [f"#{i}:{d['name']}" for i, d in enumerate(sd.query_devices()) if d.get("max_input_channels", 0) > 0]
    print("[CoreAudio IN]", ins[:6] or ["(none)"])
except Exception as e:
    print("[CoreAudio] Unavailable:", e)
PY

if [ -n "$DISKLAVIER_REMOTE_URL" ] && [ "$DISKLAVIER_HYBRID_MODE" != "MAC_LOCAL_ONLY" ]; then
  echo "[PRE-CHECK] Verifying connection to Home RTX 3070..."
  if ! python scripts/check_remote_connection.py --url "$DISKLAVIER_REMOTE_URL" --token "$DISKLAVIER_API_TOKEN" --quick --skip-failover; then
    echo "[WARN] Remote RTX unreachable or slow right now. Hybrid AUTO will start in safe Mac-local mode and auto-recover!"
  fi
fi

# Start local Ollama if installed and not running (for failover backup)
if command -v ollama >/dev/null 2>&1; then
  if ! curl -fsS --max-time 1 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    echo "[INFO] Starting local Ollama serve in background for failover safety..."
    ollama serve >/dev/null 2>&1 &
  fi
fi

cleanup() {
  echo ""
  echo "[CLEANUP] Sending Disklavier PANIC All-Notes-Off..."
  python -c "from src.stage.layer5_disklavier_output import DisklavierStageOutputLayer; out = DisklavierStageOutputLayer(port_name='Disklavier', start_worker=False); out.panic(); out.close()" || true
}
trap cleanup EXIT INT TERM

# Open browser asynchronously once server responds
(
  for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:${BIND_PORT}/api/stage/state" >/dev/null 2>&1; then
      if command -v open >/dev/null 2>&1; then
        open "http://127.0.0.1:${BIND_PORT}${URL_SUFFIX}" || true
      fi
      break
    fi
    sleep 0.3
  done
) &

exec python -m src.stage.stage_server --host "$HOST" --port "$BIND_PORT" $SRV_ARGS
