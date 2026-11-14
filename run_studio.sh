#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
echo "🎹 Starting Disklavier-Auto Live Studio & Mobile Remote Server (0.0.0.0:8765)..."
PYTHONPATH=. .venv/bin/uvicorn src.server.mission_control_server:app --host 0.0.0.0 --port 8765 &
SERVER_PID=$!
sleep 1.5
open -na "Google Chrome" --args --app="http://127.0.0.1:8765" --window-size=1480,940 2>/dev/null || open "http://127.0.0.1:8765"
wait $SERVER_PID
