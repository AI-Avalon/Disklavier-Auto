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
echo "  DISKLAVIER-AUTO 名工大 本番ステージ起動 (--mode AUTO --production)"
echo "============================================================================"
if [ ! -f "config.remote.env" ] && [ -z "${DISKLAVIER_REMOTE_URL:-}" ]; then
  echo "[WARN] config.remote.env が未設定のため、Macローカル推論モードで起動します。"
  echo "       RTX連携を使う場合は config.remote.env.example を config.remote.env へコピーしてください。"
fi
echo "  Remote URL     : ${DISKLAVIER_REMOTE_URL:-'(Mac Local Standalone)'}"
echo "  Hybrid Mode    : AUTO (自宅RTX優先 / 遅延・断線時 Mac自動フェイルオーバー)"
echo "  Field Recorder : ON (recordings/session_YYYYMMDD_HHMMSS/ へ黒箱全記録)"
echo "  [本番チェック] : 1) Disklavier本体メニュー Delay In = OFF"
echo "                   2) Steinberg UR22 Ch1(48V ON)=Cello / Ch2=Piano参照"
echo "                   3) 緊急全消音 = Esc キー / F10 = 舞台投影クリーン表示"
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
  --mode AUTO \
  --timeout-ms "${DISKLAVIER_REMOTE_TIMEOUT_MS:-1800}" \
  --production "$@" &
CHILD_PID=$!
wait "$CHILD_PID"
RC=$?
CHILD_PID=""

if [ "$RC" -ne 0 ]; then
  echo ""
  echo "[ERROR] 本番ステージサーバが異常終了しました (exit code=$RC)。"
  echo "        上のログと recordings/ の記録を確認してください。"
  pause_on_error
fi
exit "$RC"
