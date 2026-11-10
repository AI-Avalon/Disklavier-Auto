#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
echo "🎹 Starting Antigravity 2.0: Live-Visualized Autonomous R&D Studio for Cello & Disklavier..."
PYTHONPATH=. .venv/bin/uvicorn src.server.mission_control_server:app --host 127.0.0.1 --port 8765 &
SERVER_PID=$!
sleep 1.5
open -na "Google Chrome" --args --app="http://127.0.0.1:8765" --window-size=1480,940 2>/dev/null || open "http://127.0.0.1:8765"
wait $SERVER_PID
