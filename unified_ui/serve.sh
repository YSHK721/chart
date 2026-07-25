#!/usr/bin/env bash
# ライブ / リプレイ 一本化ルータ（8000）を起動する。
#   使い方:  ./serve.sh            （公開 8000・停止は Ctrl-C）
#
# 構成（基本設計書 §4）:
#   [公開 8000] router.py（本スクリプトが foreground 起動）
#     ├─ /live/*   → 127.0.0.1:8001（indicator_ui core・既存 serve.sh 8001 が起動）
#     └─ /replay/* → 127.0.0.1:8281（replay_ui core・既存 serve.sh 8281 が起動）
#
# 重要:
#   - 2 つの core は必ず既存 serve.sh 経由で起動する（生 python 起動禁止）。既存 serve.sh は
#     データ watch（毎分 M1 追記・当日 tick 再取得）を併走させ、これが無いと確定足が伸びず
#     指標が止まる（memory: fixed-ports-and-serve-scripts）。既存 serve.sh は無編集で PORT 引数のみ渡す。
#   - 内部ポート 8001/8281 は loopback 限定（router のみが叩く・外部非公開）。
#   - core は各々 setsid で別プロセスグループ起動し、停止時にグループごと確実に止める
#     （既存 serve.sh の trap cleanup で watch も停止する）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

LIVE_SERVE="${REPO_ROOT}/indigators/indicator_ui/serve.sh"
REPLAY_SERVE="${REPO_ROOT}/simulator/replay_ui/serve.sh"
ROUTER_PY="${SCRIPT_DIR}/router.py"

PUBLIC_PORT=8000
LIVE_PORT=8001
REPLAY_PORT=8281

# 既存 serve.sh の存在確認（無ければ core を起動できない＝即中断）。
for f in "$LIVE_SERVE" "$REPLAY_SERVE" "$ROUTER_PY"; do
  if [ ! -f "$f" ]; then
    echo "エラー: 必須ファイルが見つかりません: $f" >&2
    exit 1
  fi
done

# 既に公開 8000 が起動済みなら二重起動しない。
PUBLIC_URL="http://127.0.0.1:${PUBLIC_PORT}/"
if command -v curl >/dev/null 2>&1 && curl -sf -o /dev/null "$PUBLIC_URL" 2>/dev/null; then
  echo "既に起動済みです: $PUBLIC_URL"
  exit 0
fi

LIVE_PGID=""
REPLAY_PGID=""

# core をグループ起動する（setsid=新セッション＝負の PID でグループ kill 可能）。
start_core() {
  local serve_sh="$1" port="$2"
  # setsid で新プロセスグループ。PID=PGID になる。
  setsid bash "$serve_sh" "$port" >/dev/null 2>&1 &
  echo "$!"
}

# URL が応答するまで待つ（起動失敗＝タイムアウトで中断）。
wait_up() {
  local url="$1" name="$2" tries=60
  while [ "$tries" -gt 0 ]; do
    if curl -sf -o /dev/null "$url" 2>/dev/null; then
      return 0
    fi
    tries=$((tries - 1))
    sleep 1
  done
  echo "エラー: ${name} が起動しませんでした（${url}）" >&2
  return 1
}

cleanup() {
  # core をプロセスグループごと停止（既存 serve.sh の trap cleanup が watch も止める）。
  [ -n "$LIVE_PGID" ] && kill -TERM -"$LIVE_PGID" 2>/dev/null || true
  [ -n "$REPLAY_PGID" ] && kill -TERM -"$REPLAY_PGID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "▶ ライブ core を起動（既存 serve.sh ${LIVE_PORT}・データ watch 併走）..."
LIVE_PGID="$(start_core "$LIVE_SERVE" "$LIVE_PORT")"
echo "▶ リプレイ core を起動（既存 serve.sh ${REPLAY_PORT}）..."
REPLAY_PGID="$(start_core "$REPLAY_SERVE" "$REPLAY_PORT")"

echo "▶ core の起動を待機中..."
wait_up "http://127.0.0.1:${LIVE_PORT}/" "ライブ core (${LIVE_PORT})"
wait_up "http://127.0.0.1:${REPLAY_PORT}/" "リプレイ core (${REPLAY_PORT})"

echo "統合ルータを起動します: ${PUBLIC_URL}"
echo "  /live/*   → 127.0.0.1:${LIVE_PORT}"
echo "  /replay/* → 127.0.0.1:${REPLAY_PORT}"
echo "  停止: Ctrl-C"
# router を foreground 起動（生 python は router のみ＝データ watch 不要な新規プロキシ）。
#   exec しない: trap cleanup を生かし、router 終了（Ctrl-C）時に core をグループごと停止する。
python3 "$ROUTER_PY" "$PUBLIC_PORT" \
  --live-upstream "http://127.0.0.1:${LIVE_PORT}" \
  --replay-upstream "http://127.0.0.1:${REPLAY_PORT}" \
  --web-root "${SCRIPT_DIR}/web"
