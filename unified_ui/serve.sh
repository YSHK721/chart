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
# 構成（基本設計書 §4・§11）:
#   [公開 8000] router.py（本スクリプトが foreground 起動）
#     ├─ /live/*   → 127.0.0.1:8001（indicator_ui core・既存 serve.sh 8001 が起動）
#     ├─ /replay/* → 127.0.0.1:8281（replay_ui core・既存 serve.sh 8281 が起動）
#     └─ /sim/*    → 127.0.0.1:8381（sim_ui core・本スクリプトが直接起動）
#
# 重要:
#   - live / replay の 2 つの core は必ず既存 serve.sh 経由で起動する（生 python 起動禁止）。既存
#     serve.sh はデータ watch（毎分 M1 追記・当日 tick 再取得）を併走させ、これが無いと確定足が
#     伸びず指標が止まる（memory: fixed-ports-and-serve-scripts）。既存 serve.sh は無編集で
#     PORT 引数のみ渡す。
#   - sim core は**データ watch を持たない**（Phase 1 は静的配信のみ・計算は子プロセスが行う）。
#     単独起動 serve.sh は作らない裁定（§11.1 裁定 2 = TBD-12 保留）のため、本スクリプトが
#     venv python で直接起動する。起動コマンドの形は replay の serve.sh :56-60 と同流儀。
#   - 内部ポート 8001/8281/8381 は loopback 限定（router のみが叩く・外部非公開）。
#   - core は各々 setsid で別プロセスグループ起動し、停止時にグループごと確実に止める
#     （既存 serve.sh の trap cleanup で watch も停止する）。setsid は「グループ kill を可能にする」
#     ためであり、detached 起動（nohup で Ctrl-C を無効化する）ではない。
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
# sim core は単独起動 serve.sh を持たない（§11.1 裁定 2）。本スクリプトが web 根を渡して起動する。
SIM_WEB_DIR="${REPO_ROOT}/simulator/sim_ui/web"

PUBLIC_PORT=8000
LIVE_PORT=8001
REPLAY_PORT=8281
SIM_PORT=8381

# venv python（sim core を直接起動するために要る）。既存 core の serve.sh と同じ規約で解決する:
#   worktree は git 管理外の実体（venv）を持たないため、dev_paths.local.sh が VENV_PYTHON を
#   export していればそれを、無ければ git 共通ディレクトリ（メイン .git）の親＝メイン
#   チェックアウト根の venv を使う。symlink は張らない（ISSUE-363 の真因の除去）。
MAIN_ROOT="$REPO_ROOT"
if [ ! -x "${REPO_ROOT}/lightweight-charts-python-main/.venv/bin/python" ] \
   && command -v git >/dev/null 2>&1; then
  COMMON_DIR="$(git -C "$REPO_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  [ -n "$COMMON_DIR" ] && MAIN_ROOT="$(dirname "$COMMON_DIR")"
fi
VENV_PY="${VENV_PYTHON:-${MAIN_ROOT}/lightweight-charts-python-main/.venv/bin/python}"

# 既存 serve.sh の存在確認（無ければ core を起動できない＝即中断）。
for f in "$LIVE_SERVE" "$REPLAY_SERVE" "$ROUTER_PY" "${SIM_WEB_DIR}/index.html"; do
  if [ ! -f "$f" ]; then
    echo "エラー: 必須ファイルが見つかりません: $f" >&2
    exit 1
  fi
done

# venv（sim core を直接起動するのに要る）の存在確認。両 core の serve.sh（例:
#   simulator/replay_ui/serve.sh:41-44）と同流儀で**起動前に**言う。言わないと sim core だけが
#   サイレントに死に、wait_up の 60 秒タイムアウトで「起動しませんでした」としか出ず、
#   原因（venv 不在）がメッセージに現れない。
if [ ! -x "$VENV_PY" ]; then
  echo "エラー: venv python が見つかりません: $VENV_PY" >&2
  echo "       worktree で起動する場合は ./tools/setup_worktree.sh で環境設定を用意してください。" >&2
  exit 1
fi

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

# sim core（8381）は serve.sh を持たない＝argv に web 根の絶対パスが載る。これで**どのツリーの
#   sim core か**が一意に決まる（他ツリーの同名プロセスに触れない）。
stop_sim_core_if_up() {
  local root="$1" p
  curl -sf -o /dev/null --max-time 2 "http://127.0.0.1:${SIM_PORT}/" 2>/dev/null || return 0
  for p in $(pids_with "${root}/simulator/sim_ui/web"); do
    echo "  - sim core ${SIM_PORT} (PID ${p}) をプロセスグループごと停止"
    kill -TERM -"$p" 2>/dev/null || kill -TERM "$p" 2>/dev/null || true
  done
  if ! wait_down "http://127.0.0.1:${SIM_PORT}/" 20; then
    echo "エラー: sim core ${SIM_PORT} が停止しませんでした。手動で確認してください:" >&2
    echo "        ps -eo pid,args | grep sim_ui" >&2
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
  stop_sim_core_if_up "$root"
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

# 8381 が誰かに握られたまま起動しないようにする（ISSUE-348 と同型の防御）。
#
# なぜ 8000 の判定だけでは足りないか: 8000 が空いていても 8381 に**別ツリーの** sim core が
#   残っていると、こちらの sim core は bind に失敗して死ぬ。router は起動し、`/sim/*` は
#   別ツリーの sim core へ proxy される。自分のコードが 1 行も入っていない sim を、自分の
#   ものとして検証してしまう（ISSUE-355 と同じ帰結）。
#
# 自ツリー由来（argv にこのツリーの web 根を持つ）なら前回の残骸なので止めて進む。
#   他ツリーなら「誰が握っているか」を示して中断する（停止するかは人の判断＝8000 と同じ規律）。
ensure_sim_port_free() {
  curl -sf -o /dev/null --max-time 2 "http://127.0.0.1:${SIM_PORT}/" 2>/dev/null || return 0
  if [ -n "$(pids_with "${REPO_ROOT}/simulator/sim_ui/web")" ]; then
    echo "▶ ${SIM_PORT} を自ツリーの sim core が握っています。停止してから起動します..."
    stop_sim_core_if_up "$REPO_ROOT"
    return 0
  fi
  echo "エラー: ${SIM_PORT} は**別のツリー**の sim core が占有しています。起動を中止しました。" >&2
  echo "       起動しようとしたツリー: ${REPO_ROOT}" >&2
  echo "       そのまま進むと、このツリーの変更が入っていない sim を検証することになります。" >&2
  echo "       占有プロセス:" >&2
  ps -eo pid,args 2>/dev/null | grep -F "sim_ui" | grep -v grep >&2 || true
  exit 1
}

LIVE_PGID=""
REPLAY_PGID=""
SIM_PGID=""

# core をグループ起動する（setsid=新セッション＝負の PID でグループ kill 可能）。
start_core() {
  local serve_sh="$1" port="$2"
  # setsid で新プロセスグループ。PID=PGID になる。
  setsid bash "$serve_sh" "$port" >/dev/null 2>&1 &
  echo "$!"
}

# sim core をグループ起動する。単独起動 serve.sh を作らない裁定（§11.1 裁定 2）のため、
#   venv python へ Composition Root を直接与える（形は replay の serve.sh :56-60 と同流儀）。
#   argv に web 根の絶対パスが載るので、停止側が「どのツリーの sim core か」を特定できる。
start_sim_core() {
  setsid env PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" "$VENV_PY" -c "
from simulator.sim_ui.main.composition_root_jobs import build_sim_job_app as build_sim_app
from simulator.sim_ui.framework.serve_sim_jobs import serve
serve(build_sim_app(web_dir='${SIM_WEB_DIR}'), port=${SIM_PORT})
" >/dev/null 2>&1 &
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
  [ -n "$SIM_PGID" ] && kill -TERM -"$SIM_PGID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "▶ ライブ core を起動（既存 serve.sh ${LIVE_PORT}・データ watch 併走）..."
LIVE_PGID="$(start_core "$LIVE_SERVE" "$LIVE_PORT")"
echo "▶ リプレイ core を起動（既存 serve.sh ${REPLAY_PORT}）..."
REPLAY_PGID="$(start_core "$REPLAY_SERVE" "$REPLAY_PORT")"
ensure_sim_port_free
echo "▶ シミュレーション core を起動（${SIM_PORT}）..."
SIM_PGID="$(start_sim_core)"

echo "▶ core の起動を待機中..."
wait_up "http://127.0.0.1:${LIVE_PORT}/" "ライブ core (${LIVE_PORT})"
wait_up "http://127.0.0.1:${REPLAY_PORT}/" "リプレイ core (${REPLAY_PORT})"
wait_up "http://127.0.0.1:${SIM_PORT}/" "シミュレーション core (${SIM_PORT})"

echo "統合ルータを起動します: ${PUBLIC_URL}"
# 配信元は**必ず**出す。「どのツリーの UI を見ているか」は検証の前提であり、
#   問い合わせないと分からない状態にしておくと ISSUE-348 の事故がまた起きる。
echo "  配信元: ${REPO_ROOT}"
echo "  /live/*   → 127.0.0.1:${LIVE_PORT}"
echo "  /replay/* → 127.0.0.1:${REPLAY_PORT}"
echo "  /sim/*    → 127.0.0.1:${SIM_PORT}"
echo "  停止: Ctrl-C"
# router を foreground 起動（生 python は router のみ＝データ watch 不要な新規プロキシ）。
#   exec しない: trap cleanup を生かし、router 終了（Ctrl-C）時に core をグループごと停止する。
#   上流はモードごとに `--upstream <mode>=<url>` で渡す（§11.1 裁定 6）。モードを増やすときに
#   router のシグネチャを直さなくてよいのがこの形の要点で、ここも 1 行の追加で済む。
python3 "$ROUTER_PY" "$PUBLIC_PORT" \
  --host 127.0.0.1 \
  --upstream "live=http://127.0.0.1:${LIVE_PORT}" \
  --upstream "replay=http://127.0.0.1:${REPLAY_PORT}" \
  --upstream "sim=http://127.0.0.1:${SIM_PORT}" \
  --web-root "${SCRIPT_DIR}/web"
