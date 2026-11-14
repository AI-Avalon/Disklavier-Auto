#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

HOST="127.0.0.1"
PORT="8765"
if [ "${1:-}" = "--lan" ] || [ "${DISKLAVIER_LAN_MODE:-0}" = "1" ]; then
  HOST="0.0.0.0"
  export DISKLAVIER_LAN_MODE=1
  echo "🎹 Starting Disklavier-Auto Live Studio & LAN Mobile Remote Server (${HOST}:${PORT})..."
else
  export DISKLAVIER_LAN_MODE=0
  echo "🎹 Starting Disklavier-Auto Live Studio (Local Safe Mode ${HOST}:${PORT})..."
  echo "   (スマホリモコンを同じWi-Fiから使う場合は ./run_studio.sh --lan で起動してください)"
fi

PYTHONPATH=. .venv/bin/uvicorn src.server.mission_control_server:app --host "$HOST" --port "$PORT" &
SERVER_PID=$!
sleep 1.5
open -na "Google Chrome" --args --app="http://127.0.0.1:${PORT}" --window-size=1480,940 2>/dev/null || open "http://127.0.0.1:${PORT}"
wait $SERVER_PID
