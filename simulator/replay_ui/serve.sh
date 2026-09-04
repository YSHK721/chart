#!/usr/bin/env bash
# リプレイ（因果リビール再生）UI をサクッと起動する。
#   使い方:  ./serve.sh [PORT]        （既定ポート 8280・停止は Ctrl-C）
#   例:      ./serve.sh 8290
# どのディレクトリから実行してもよい（パスはスクリプト位置基準で解決する）。
# web_dir・PYTHONPATH・venv・port を自動で結線する（手打ちの python -c は不要）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# 実行時の import パスは **本スクリプトの位置** から解決する（ISSUE-279）。venv の .pth は
# インストール時のチェックアウトを絶対パスで指すため、worktree 起動でも main の実装が読まれる。
. "${REPO_ROOT}/tools/dev_paths.sh"
WEB_DIR="${SCRIPT_DIR}/web"

# venv・data（いずれも非追跡）は git worktree だとメインチェックアウト側にしか無い。REPO_ROOT に
# 無ければ git 共通ディレクトリ（メイン .git）の親＝メインチェックアウト根へフォールバックする
# （メイン実行時は MAIN_ROOT=REPO_ROOT で同じ）。コード（PYTHONPATH/web_dir）は REPO_ROOT を使う。
MAIN_ROOT="$REPO_ROOT"
if { [ ! -x "${REPO_ROOT}/lightweight-charts-python-main/.venv/bin/python" ] \
     || [ ! -d "${REPO_ROOT}/data/marketdata" ]; } && command -v git >/dev/null 2>&1; then
  COMMON_DIR="$(git -C "$REPO_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  [ -n "$COMMON_DIR" ] && MAIN_ROOT="$(dirname "$COMMON_DIR")"
fi
# venv とデータの場所（ISSUE-365）。既定は上の MAIN_ROOT 推定だが、dev_paths.local.sh が
#   VENV_PYTHON / MARKETDATA_DATA_DIR を export していればそれを優先する。環境変数で絶対パスを
#   指せれば、worktree へ symlink を張る理由が消える（ISSUE-363 の真因の除去）。
VENV_PY="${VENV_PYTHON:-${MAIN_ROOT}/lightweight-charts-python-main/.venv/bin/python}"
DATA_DIR_DEFAULT="${MARKETDATA_DATA_DIR:-${MAIN_ROOT}/data/marketdata}"

PORT=8280
for arg in "$@"; do
  case "$arg" in
    ''|*[!0-9]*) echo "warn: 不明な引数 '$arg' は無視" >&2 ;;
    *) PORT="$arg" ;;
  esac
done
URL="http://127.0.0.1:${PORT}/"

# venv（pandas 必須）の存在確認。
if [ ! -x "$VENV_PY" ]; then
  echo "エラー: venv python が見つかりません: $VENV_PY" >&2
  exit 1
fi

# 既に起動済みなら二重起動しない（Address already in use を避ける）。
if command -v curl >/dev/null 2>&1 && curl -sf -o /dev/null "$URL" 2>/dev/null; then
  echo "既に起動済みです: $URL"
  exit 0
fi

echo "リプレイ再生 UI を起動します: $URL"
echo "  停止: Ctrl-C"
cd "$REPO_ROOT"
exec env MARKETDATA_DATA_DIR="${MARKETDATA_DATA_DIR:-${DATA_DIR_DEFAULT}}" \
  "$VENV_PY" -c "
from simulator.replay_ui.main.composition_root import build_replay_app
from simulator.replay_ui.framework.serve_replay import serve
serve(build_replay_app(web_dir='${WEB_DIR}'), port=${PORT})
"
