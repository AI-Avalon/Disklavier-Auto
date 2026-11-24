#!/usr/bin/env bash
set -uo pipefail

cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export PYTHONUTF8=1

pause_on_error() {
  if [ "${DISKLAVIER_NO_PAUSE:-0}" != "1" ]; then
    echo ""
    read -r -p "Enterキーを押すとウィンドウを閉じます... " _ || true
  fi
}

for _env_file in ".env.remote" ".env.mac" "config.remote.env"; do
  if [ -f "$_env_file" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$_env_file"
    set +a
  fi
done

if [ ! -f ".venv/bin/python" ]; then
  echo "[ERROR] .venv が見つかりません。先に ./setup_mac.sh を実行してください。"
  pause_on_error
  exit 1
fi

echo "============================================================================"
echo "  DISKLAVIER-AUTO Mac デモ起動 (RTX接続テスト + Macスピーカー + 論理思考画面)"
echo "============================================================================"
if [ ! -f "config.remote.env" ] && [ -z "${DISKLAVIER_REMOTE_URL:-}" ]; then
  echo "[WARN] config.remote.env が未設定のため、Macローカル推論モードで起動します。"
  echo "       RTX連携を使う場合は config.remote.env.example を config.remote.env へコピーしてください。"
fi
echo "  Remote URL   : ${DISKLAVIER_REMOTE_URL:-'(Mac Local Standalone)'}"
echo "  Hybrid Mode  : ${DISKLAVIER_HYBRID_MODE:-AUTO}"
echo "  Audio Output : Macスピーカー / CoreAudio モニター発音 ON"
echo "  Browser UI   : /studio?demo=1 自動起動 (論理思考トレース表示対応)"
echo "============================================================================"

CHILD_PID=""
forward_signal() {
  if [ -n "${CHILD_PID:-}" ] && kill -0 "$CHILD_PID" 2>/dev/null; then
    kill -TERM "$CHILD_PID" 2>/dev/null || true
    wait "$CHILD_PID" 2>/dev/null || true
  fi
}
trap 'forward_signal; exit 130' INT
trap 'forward_signal; exit 143' TERM

./start_mac_stage.sh \
  --remote-url "${DISKLAVIER_REMOTE_URL:-}" \
  --token "${DISKLAVIER_API_TOKEN:-}" \
  --mode "${DISKLAVIER_HYBRID_MODE:-AUTO}" \
  --timeout-ms "${DISKLAVIER_REMOTE_TIMEOUT_MS:-1800}" \
  --demo "$@" &
CHILD_PID=$!
wait "$CHILD_PID"
RC=$?
CHILD_PID=""

if [ "$RC" -ne 0 ]; then
  echo ""
  echo "[ERROR] デモサーバが異常終了しました (exit code=$RC)。"
  echo "        上のログを確認し、必要なら ./Mac_1_Check_RTX.command で接続を確認してください。"
  pause_on_error
fi
exit "$RC"
