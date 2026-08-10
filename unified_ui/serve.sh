#!/usr/bin/env bash
# ライブ / リプレイ 一本化ルータ（8000）を起動する。
#   使い方:  ./serve.sh [--takeover]   （公開 8000・停止は Ctrl-C）
#     --takeover : 8000 を別ツリーが握っているとき、そのスタックを停止してから起動する。
#
# --takeover の位置づけ（ISSUE-366 の派生・2026-08-10）:
#   8000 は固定の単一資源なので、別ツリーで検証するには占有の移譲が要る。旧実装は移譲の
#   **判断**（他セッションの作業を落としてよいか）と**手順**（どの PID をどの順で止めるか）の
#   両方を人へ投げ返していた。手順は機械の仕事であり、人の頭に置くと毎回 ps と kill を
#   打つことになる（実際「起動させるだけなのに手間すぎる」と報告された）。
#   本フラグは**判断だけを人に残し、手順をスクリプトへ移す**。フラグ無しの既定は従来どおり
#   エラー終了であり、他セッションのスタックを黙って落とすことはない。
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

TAKEOVER=0
while [ $# -gt 0 ]; do
  case "$1" in
    --takeover) TAKEOVER=1; shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "不明な引数: $1（使い方: ./serve.sh [--takeover]）" >&2; exit 1 ;;
  esac
done

# ツリー固有の環境設定（ISSUE-365）。worktree は git 管理外の実体（venv・データ）を持たないため、
#   起動前に絶対パスの環境変数を用意する必要がある。無いときにここで用意するのは、これが
#   「起動に必ず要るのに、忘れると分かりにくい形で失敗する」準備だからである（人が覚えておく
#   仕事ではない）。本チェックアウトはツリー相対の実体を持つので、この分岐に入らない。
if [ ! -f "${REPO_ROOT}/dev_paths.local.sh" ] \
   && [ ! -x "${REPO_ROOT}/lightweight-charts-python-main/.venv/bin/python" ] \
   && [ -x "${REPO_ROOT}/tools/setup_worktree.sh" ]; then
  echo "▶ 環境設定が無いため tools/setup_worktree.sh を実行..."
  "${REPO_ROOT}/tools/setup_worktree.sh"
fi

# 実行時の import パスは **本スクリプトの位置** から解決する（ISSUE-279）。core 側の serve.sh も
# 各々 source するが、router.py 自身と子プロセスの既定を本スクリプトの位置で確定させる。
#   dev_paths.local.sh（上で用意しうる）もここで読まれる。
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

# ---- 占有スタックの停止に使う道具（--takeover 経路でのみ使う）-----------------
#
# 決してシグナルを送ってはならない PID 集合＝**自分の側**のプロセス。
#
# argv の部分一致は「その文字列を引数に持つプロセス」を拾うため、自分の側まで一致しうる。
#   実測（2026-08-10）で 2 種類が一致した:
#     - 起動元のシェル（`bash -c '... serve.sh ...'`）＝**祖先**
#     - パイプラインのために bash が fork する部分シェル＝**同一プロセスグループの子孫**
#       （argv を親から引き継ぐため、自分の argv に一致文字列があると必ず一致する）
#   誤って自分の側へ INT を送るのは、消そうとしている手間より遥かに悪い。
#
# 停止対象（別ツリーのスタック）は必ず別セッション・別プロセスグループなので、
#   「自分のプロセスグループ ＋ 祖先」を除けば安全側に倒せる。
#
# プロセスグループは**照合時に行ごとに**見る。PID の一覧を先に作って除外する方式では、
#   一覧を作った**後に** fork される部分シェル（照合パイプライン自身）を取り逃す
#   （実測 2026-08-10: 除外一覧方式で自分の部分シェルが残った）。
ancestor_pids() {
  local p=$$
  while [ -n "$p" ] && [ "$p" -gt 1 ] 2>/dev/null; do
    echo "$p"
    p="$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')"
  done
}

# 引数の文字列を argv に含むプロセスの PID を列挙する。ツリーの絶対パスを含む文字列で
#   引くため、**どのツリーのプロセスか**が一意に決まる（他ツリーの同名プロセスに触れない）。
#   検索に使う道具自身（grep/ps/awk）と、自分の側のプロセスは除外する。
pids_with() {
  local mypgid ancestors
  mypgid="$(ps -o pgid= -p $$ 2>/dev/null | tr -d ' ')"
  ancestors=" $(ancestor_pids | tr '\n' ' ') "
  ps -eo pid=,pgid=,args= 2>/dev/null \
    | grep -F -- "$1" \
    | grep -vE '(^| )(u?grep|pgrep|ps|awk)( |$)' \
    | awk -v g="$mypgid" -v anc="$ancestors" '
        g != "" && $2 == g { next }                  # 自分のプロセスグループ（部分シェルを含む）
        index(anc, " " $1 " ") > 0 { next }          # 祖先（別グループのことがある）
        { print $1 }
      ' || true
}

# URL が応答しなくなるまで待つ（0=落ちた / 1=時間内に落ちない）。
wait_down() {
  local url="$1" tries="${2:-30}"
  while [ "$tries" -gt 0 ]; do
    if ! curl -sf -o /dev/null --max-time 2 "$url" 2>/dev/null; then
      return 0
    fi
    tries=$((tries - 1))
    sleep 1
  done
  return 1
}

# core（8001/8281）が落ちていなければ、**指定ツリーの** core serve.sh をグループ停止する。
#   通常は router を止めた時点で親 serve.sh の trap cleanup が止めるので、ここは保険。
#   落とし切らないと、こちらの core が bind に失敗し、router が**別ツリーの core** を
#   proxy することになる（ISSUE-348 と同じ「他人のコードを自分のものとして見る」事故）。
stop_core_if_up() {
  local core_sh="$1" port="$2" p
  curl -sf -o /dev/null --max-time 2 "http://127.0.0.1:${port}/" 2>/dev/null || return 0
  for p in $(pids_with "${core_sh} ${port}"); do
    echo "  - core ${port} (PID ${p}) をプロセスグループごと停止"
    kill -TERM -"$p" 2>/dev/null || kill -TERM "$p" 2>/dev/null || true
  done
  if ! wait_down "http://127.0.0.1:${port}/" 20; then
    echo "エラー: core ${port} が停止しませんでした。手動で確認してください:" >&2
    echo "        ps -eo pid,args | grep 'serve.sh ${port}'" >&2
    exit 1
  fi
}

# 指定ツリーの 8000 スタックを停止する（Ctrl-C と同じ経路をたどる）。
#   router は foreground プロセスなので、INT を送ると親 serve.sh が EXIT trap へ進み、
#   cleanup が core をプロセスグループごと止める。過去のセッションが setsid nohup で
#   起動したスタックは Ctrl-C が届かないため、この経路が唯一の正しい止め方になる。
stop_stack() {
  local root="$1" p
  local router_pids
  router_pids="$(pids_with "--web-root ${root}/unified_ui/web")"
  if [ -n "$router_pids" ]; then
    for p in $router_pids; do
      echo "  - router (PID ${p}) へ INT（Ctrl-C 相当）"
      kill -INT "$p" 2>/dev/null || true
    done
  else
    echo "  ! router プロセスを特定できませんでした（配信元: ${root}）" >&2
  fi
  if ! wait_down "$PUBLIC_URL" 30; then
    echo "エラー: ${PUBLIC_PORT} が解放されませんでした。占有プロセスを確認してください:" >&2
    echo "        ps -eo pid,args | grep router.py" >&2
    exit 1
  fi
  stop_core_if_up "${root}/indigators/indicator_ui/serve.sh" "$LIVE_PORT"
  stop_core_if_up "${root}/simulator/replay_ui/serve.sh" "$REPLAY_PORT"
  echo "  - 停止しました（配信元だった: ${root}）"
}

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
#   不一致なら黙って終了せず、占有しているツリーの実パスを示す。
#
#   停止するかどうかは**人の判断**に委ねる（他セッションが作業中のスタックを落としうるため）。
#   ただし判断が `--takeover` で表明されているなら、停止の**手順**はスクリプトが行う
#   （ISSUE-366 派生。人に PID 探しをさせない）。フラグが無ければ従来どおりエラー終了する。
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
  if [ "$TAKEOVER" -eq 1 ]; then
    echo "▶ ${PUBLIC_PORT} は別ツリーが配信中: ${serving_root}"
    echo "▶ 引き継ぎます（--takeover）..."
    stop_stack "$serving_root"
  else
    echo "エラー: ${PUBLIC_PORT} は**別のツリー**が配信しています。起動を中止しました。" >&2
    echo "       占有中の配信元: ${serving_root}" >&2
    echo "       起動しようとしたツリー: ${REPO_ROOT}" >&2
    echo "       そのまま開くと、このツリーの変更が入っていない UI を見ることになります。" >&2
    echo "       引き継いで起動する場合: ./serve.sh --takeover" >&2
    exit 1
  fi
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
# 配信元は**必ず**出す。「どのツリーの UI を見ているか」は検証の前提であり、
#   問い合わせないと分からない状態にしておくと ISSUE-348 の事故がまた起きる。
echo "  配信元: ${REPO_ROOT}"
echo "  /live/*   → 127.0.0.1:${LIVE_PORT}"
echo "  /replay/* → 127.0.0.1:${REPLAY_PORT}"
echo "  停止: Ctrl-C"
# router を foreground 起動（生 python は router のみ＝データ watch 不要な新規プロキシ）。
#   exec しない: trap cleanup を生かし、router 終了（Ctrl-C）時に core をグループごと停止する。
python3 "$ROUTER_PY" "$PUBLIC_PORT" \
  --live-upstream "http://127.0.0.1:${LIVE_PORT}" \
  --replay-upstream "http://127.0.0.1:${REPLAY_PORT}" \
  --web-root "${SCRIPT_DIR}/web"
