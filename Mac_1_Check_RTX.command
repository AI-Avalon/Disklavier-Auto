#!/usr/bin/env bash
set -uo pipefail

cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export PYTHONUTF8=1

pause_if_interactive() {
  if [ "${DISKLAVIER_NO_PAUSE:-0}" != "1" ]; then
    echo ""
    read -r -p "Enterキーを押すとウィンドウを閉じます... " _ || true
  fi
}

load_remote_env() {
  local env_file
  for env_file in ".env.remote" ".env.mac" "config.remote.env"; do
    if [ -f "$env_file" ]; then
      set -a
      # shellcheck disable=SC1090
      source "$env_file"
      set +a
    fi
  done
}

mask_token() {
  local tok="${1:-}"
  local len=${#tok}
  if [ "$len" -eq 0 ]; then
    echo "(未設定)"
  elif [ "$len" -le 8 ]; then
    echo "****"
  else
    echo "${tok:0:4}...${tok: -4} (${len} chars)"
  fi
}

load_remote_env

if [ ! -f ".venv/bin/python" ]; then
  echo "[ERROR] .venv が見つかりません。先に ./setup_mac.sh を実行してください。"
  pause_if_interactive
  exit 1
fi

REMOTE_URL="${DISKLAVIER_REMOTE_URL:-}"
REMOTE_TOKEN="${DISKLAVIER_API_TOKEN:-}"
TIMEOUT_MS="${DISKLAVIER_REMOTE_TIMEOUT_MS:-1800}"

echo "============================================================================"
echo "  DISKLAVIER-AUTO Mac事前チェック (Tailscale / RTX 3070 / MIDI・Audio)"
echo "============================================================================"
if [ ! -f "config.remote.env" ] && [ -z "$REMOTE_URL" ]; then
  echo "[WARN] config.remote.env が見つかりません。"
  echo "       cp config.remote.env.example config.remote.env で接続先を設定してください。"
fi
echo "  接続先 URL    : ${REMOTE_URL:-'(未設定)'}"
echo "  API Token     : $(mask_token "$REMOTE_TOKEN")"
echo "  Timeout (ms)  : ${TIMEOUT_MS} ms"
echo "============================================================================"

# Parse host & port from REMOTE_URL
PARSED_HP="$(".venv/bin/python" - "$REMOTE_URL" "${DISKLAVIER_REMOTE_HOST:-}" << 'PY'
import sys
from urllib.parse import urlparse

url = (sys.argv[1] or "").strip()
fallback_host = (sys.argv[2] or "").strip()
host = fallback_host
port = 8090
if url:
    p = urlparse(url)
    if p.hostname:
        host = p.hostname
    if p.port:
        port = p.port
    elif p.scheme == "https":
        port = 443
    elif p.scheme == "http":
        port = 80
print(f"{host}|{port}")
PY
)"
REMOTE_HOST="${PARSED_HP%%|*}"
REMOTE_PORT="${PARSED_HP##*|}"

echo ""
echo "[1/4] Tailscale 疎通・経路・TCP到達チェック..."
TS_PING_OK=0
TS_PING_MODE="NG"
TCP_OK=0

if command -v tailscale >/dev/null 2>&1; then
  echo "--- tailscale status (該当ノード) ---"
  if [ -n "$REMOTE_HOST" ]; then
    tailscale status 2>&1 | grep -E "($REMOTE_HOST|$(hostname -s))" || tailscale status 2>&1 | head -n 10
  else
    tailscale status 2>&1 | head -n 10
  fi

  if [ -n "$REMOTE_HOST" ]; then
    echo "--- tailscale ping -c 3 $REMOTE_HOST ---"
    set +e
    TS_PING_OUT="$(tailscale ping -c 3 "$REMOTE_HOST" 2>&1)"
    set -e
    echo "$TS_PING_OUT"
    if echo "$TS_PING_OUT" | grep -q "pong from"; then
      TS_PING_OK=1
      if echo "$TS_PING_OUT" | grep -q "via DERP"; then
        TS_PING_MODE="OK (DERP relay)"
      else
        TS_PING_MODE="OK (direct)"
      fi
    else
      echo "[WARN] tailscale ping が応答しませんでした。"
    fi
  fi
else
  echo "[WARN] tailscale CLI が見つかりません (/Applications/Tailscale.app の起動を確認してください)。"
fi

if [ -n "$REMOTE_HOST" ]; then
  echo "--- TCPポート疎通確認 (${REMOTE_HOST}:${REMOTE_PORT}, timeout=2.5s) ---"
  if ".venv/bin/python" - "$REMOTE_HOST" "$REMOTE_PORT" << 'PY'
import socket, sys, time
host = sys.argv[1]
port = int(sys.argv[2])
t0 = time.perf_counter()
try:
    with socket.create_connection((host, port), timeout=2.5):
        ms = round((time.perf_counter() - t0) * 1000.0, 2)
        print(f"  [OK] TCP {host}:{port} 接続成功 ({ms} ms)")
        raise SystemExit(0)
except Exception as exc:
    ms = round((time.perf_counter() - t0) * 1000.0, 2)
    print(f"  [FAIL] TCP {host}:{port} 接続不可 ({ms} ms): {exc}")
    raise SystemExit(1)
PY
  then
    TCP_OK=1
  else
    TCP_OK=0
    if [ "$TS_PING_OK" -eq 1 ]; then
      echo "  [原因切り分け] Tailscale層は到達しているため、自宅Windows側のファイアウォールで"
      echo "                 TCP ${REMOTE_PORT} 受信がブロックされているか、サーバ未起動の可能性が高いです。"
      echo "  [対処(Windows管理者PowerShell)]:"
      echo "    New-NetFirewallRule -DisplayName \"Disklavier Remote GPU ${REMOTE_PORT}\" -Direction Inbound -Protocol TCP -LocalPort ${REMOTE_PORT} -Action Allow"
    fi
  fi
fi

echo ""
echo "[2/4] RTX 3070 接続・認証・実推論・フェイルオーバー診断..."
DIAG_RC=1
if [ -n "$REMOTE_URL" ]; then
  set +e
  ".venv/bin/python" scripts/check_remote_connection.py \
    --url "$REMOTE_URL" \
    --token "$REMOTE_TOKEN" \
    --timeout-ms "$TIMEOUT_MS"
  DIAG_RC=$?
  set -e
else
  echo "[SKIP] DISKLAVIER_REMOTE_URL が未設定のためスキップしました。"
fi

echo ""
echo "[3/4] Mac 上の CoreMIDI / CoreAudio デバイス一覧 & ローカル退避確認..."
".venv/bin/python" - << 'PY'
import platform
print(f"  macOS / Arch     : {platform.system()} {platform.release()} ({platform.machine()})")

try:
    import torch
    print(f"  PyTorch / MPS    : {torch.__version__} (MPS available={torch.backends.mps.is_available()})")
except Exception as exc:
    print(f"  PyTorch / MPS    : unavailable ({exc})")

try:
    import mido
    ins = list(mido.get_input_names())
    outs = list(mido.get_output_names())
    print("  CoreMIDI INPUTS  :", ins or ["(none)"])
    print("  CoreMIDI OUTPUTS :", outs or ["(none)"])
    dk_found = any("disklavier" in p.lower() for p in ins + outs)
    print("  Disklavier 実機  :", "検出済み" if dk_found else "未接続 (デモ / 事前テスト可)")
except Exception as exc:
    print("  CoreMIDI         : error:", exc)

try:
    import sounddevice as sd
    devs = sd.query_devices()
    print("  CoreAudio DEVICES:")
    ur_found = False
    for idx, d in enumerate(devs):
        name = str(d.get("name", ""))
        ch_in = int(d.get("max_input_channels", 0))
        ch_out = int(d.get("max_output_channels", 0))
        if "ur22" in name.lower() or "steinberg" in name.lower():
            ur_found = True
        print(f"    #{idx}: {name} (in={ch_in}, out={ch_out})")
    print("  Steinberg UR22   :", "検出済み" if ur_found else "未接続 (Mac内蔵マイク/スピーカー使用)")
except Exception as exc:
    print("  CoreAudio        : error:", exc)
PY

if command -v ollama >/dev/null 2>&1; then
  if curl -fsS --max-time 1 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    echo "  Mac Local Ollama : 稼働中 (http://127.0.0.1:11434)"
  else
    echo "  Mac Local Ollama : インストール済み (起動時は start_mac_stage.sh が自動起動)"
  fi
else
  echo "  Mac Local Ollama : 未インストール (決定論+MPSフォールバックで動作)"
fi

echo ""
echo "============================================================================"
echo "[4/4] 事前チェック結果サマリー"
echo "  - Tailscale Ping   : ${TS_PING_MODE}"
echo "  - TCP ${REMOTE_PORT} 接続    : $([ "$TCP_OK" -eq 1 ] && echo 'OK' || echo 'NG (ブロックまたは未起動)')"
echo "  - RTX 総合診断     : $([ "$DIAG_RC" -eq 0 ] && echo 'PASS (リモート推論＋フェイルオーバーOK)' || echo 'FAIL (フェイルオーバーは確認可能・リモート接続要対処)')"
echo "============================================================================"

pause_if_interactive
exit "$DIAG_RC"
