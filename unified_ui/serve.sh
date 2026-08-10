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
# 実行時の import パスは **本スクリプトの位置** から解決する（ISSUE-279）。core 側の serve.sh も
# 各々 source するが、router.py 自身と子プロセスの既定を本スクリプトの位置で確定させる。
. "${REPO_ROOT}/tools/dev_paths.sh"

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
#
# ISSUE-348: 旧実装は `curl -sf "$PUBLIC_URL"` で「**何かが**応答するか」しか見ておらず、
#   「**どのツリーが**応答しているか」を見ていなかった。そのため別チェックアウト（main 側・
#   他の worktree）の残存スタックがポートを握っていると、本スクリプトは何も起動せずに
#   「既に起動済みです」と出して正常終了し、開発者は自分のコードが 1 行も入っていない UI を
#   自分のコードとして検証してしまう。実際に 2 度事故が起きている（ISSUE-355 の
#   「setColorThemeProvider is not a function」はこの機構の帰結）。
#
# よって占有者へ配信元を問い合わせ、**自分のツリーと一致するときだけ** no-op する。
#   不一致なら黙って終了せず、占有しているツリーの実パスを示してエラー終了する
#   （どこを見ているのかが即座に分かる）。占有者の停止は行わない — 他セッションが作業中の
#   スタックを落とす破壊的操作になるため、停止は人の判断に委ねる。
PUBLIC_URL="http://127.0.0.1:${PUBLIC_PORT}/"
SERVING_ROOT_URL="http://127.0.0.1:${PUBLIC_PORT}/__serving_root"
if command -v curl >/dev/null 2>&1 && curl -sf -o /dev/null "$PUBLIC_URL" 2>/dev/null; then
  # 占有者が居る。配信元を問い合わせて自分と同一か確かめる。
  serving_root="$(curl -sf --max-time 5 "$SERVING_ROOT_URL" 2>/dev/null | head -n 1 || true)"
  if [ -z "$serving_root" ]; then
    # 応答はするが配信元を答えない＝本エンドポイントを持たない旧ルータが動いている。
    #   「たぶん自分だろう」と仮定して no-op すると、まさに ISSUE-348 の事故になる。
    echo "エラー: ${PUBLIC_PORT} は応答しますが、配信元を確認できません（${SERVING_ROOT_URL} が無応答）。" >&2
    echo "       本エンドポイントを持たない旧ルータが占有している可能性があります。" >&2
    echo "       占有プロセスを確認してください: ps -eo pid,args | grep router.py" >&2
    exit 1
  fi
  if [ "$serving_root" = "$REPO_ROOT" ]; then
    echo "既に起動済みです: $PUBLIC_URL （配信元: ${serving_root}）"
    exit 0
  fi
  echo "エラー: ${PUBLIC_PORT} は**別のツリー**が配信しています。起動を中止しました。" >&2
  echo "       占有中の配信元: ${serving_root}" >&2
  echo "       起動しようとしたツリー: ${REPO_ROOT}" >&2
  echo "       そのまま開くと、このツリーの変更が入っていない UI を見ることになります。" >&2
  echo "       停止して切り替える場合は、占有側のプロセスを止めてから再実行してください:" >&2
  echo "         ps -eo pid,args | grep router.py" >&2
  exit 1
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
