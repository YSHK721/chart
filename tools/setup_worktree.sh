#!/usr/bin/env bash
# setup_worktree.sh — worktree から起動するための環境設定を 1 コマンドで用意する（ISSUE-365）。
#
# 解く問題:
#   データ実体（data/marketdata）と venv は git 管理外のため、worktree を作った瞬間に両方とも
#   存在しない。一方コードはこれらを**ツリー相対**で探すため、worktree から起動すると自分の
#   ツリー内を見て見つからず core が起動しない。
#
#   従来はツリー内へ symlink を張って通していた。しかしその symlink の中身は張り元の絶対パス
#   （/workspaces/app/...）であり、コミットして本チェックアウトへ checkout されると
#   「自分自身を指す symlink」に置換され、実体参照が失われる。2026-08-10 に実際に起き、
#   venv と marketdata が同時に参照不能になってサーバが起動しなくなった（ISSUE-363）。
#
# 本スクリプトの方針:
#   symlink を張らない。**ツリーの外にある実体を絶対パスで指す環境変数**を生成する。
#   出力先 dev_paths.local.sh は .gitignore 済みなので、コミットの候補にならない
#   （「気をつける」ではなく、構造的にコミットされない）。
#
# 使い方:
#   cd <worktree> && ./tools/setup_worktree.sh
#   ./tools/setup_worktree.sh --main-root /workspaces/app   # 実体の場所を明示する場合
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUT="${REPO_ROOT}/dev_paths.local.sh"

MAIN_ROOT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --main-root) MAIN_ROOT="$2"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "不明な引数: $1" >&2; exit 1 ;;
  esac
done

# 実体の在り処を決める。明示指定が無ければ git の共通ディレクトリから本チェックアウトを引く
#   （worktree は .git がファイルで、common-dir が本チェックアウトの .git を指す）。
if [ -z "$MAIN_ROOT" ] && command -v git >/dev/null 2>&1; then
  COMMON_DIR="$(git -C "$REPO_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  [ -n "$COMMON_DIR" ] && MAIN_ROOT="$(dirname "$COMMON_DIR")"
fi
if [ -z "$MAIN_ROOT" ]; then
  echo "エラー: 実体の場所を特定できません。--main-root で指定してください。" >&2
  exit 1
fi

VENV_PY="${MAIN_ROOT}/lightweight-charts-python-main/.venv/bin/python"
DATA_DIR="${MAIN_ROOT}/data/marketdata"

# fail-fast: 実体が無いまま設定を書くと、起動時に分かりにくい形で失敗する。
missing=0
if [ ! -x "$VENV_PY" ]; then
  echo "エラー: venv python が見つかりません: $VENV_PY" >&2
  missing=1
fi
if [ ! -d "$DATA_DIR" ]; then
  echo "エラー: データ基点が見つかりません: $DATA_DIR" >&2
  missing=1
fi
[ "$missing" -eq 0 ] || { echo "  --main-root で実体のあるツリーを指定してください。" >&2; exit 1; }

cat > "$OUT" <<EOF
#!/usr/bin/env bash
# 自動生成（tools/setup_worktree.sh）。**コミットしないこと**（.gitignore 済み）。
#
# このツリーから起動するとき、git 管理外の実体（venv・データ）をどこに見に行くかを指す。
# symlink を張る代わりに絶対パスの環境変数で指すことで、ツリー内に環境依存の実体を
# 持たずに済む（ISSUE-363 の再発防止）。
export VENV_PYTHON="${VENV_PY}"
export MARKETDATA_DATA_DIR="${DATA_DIR}"
EOF

echo "生成しました: $OUT"
echo "  VENV_PYTHON        = $VENV_PY"
echo "  MARKETDATA_DATA_DIR= $DATA_DIR"
echo
echo "以後 ./unified_ui/serve.sh がこの設定を読みます（symlink は不要です）。"
