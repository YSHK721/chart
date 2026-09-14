#!/usr/bin/env bash
# dev_paths.sh — 起動スクリプトの位置を基準に PYTHONPATH を組み立てる（ISSUE-279）。
#
# 使い方（各 serve.sh から source する）:
#   REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
#   . "${REPO_ROOT}/tools/dev_paths.sh"      # ← PYTHONPATH を export する
#
# なぜ必要か:
#   venv の .pth（tools/install_dev_paths.py が書く）は**インストールした時のチェックアウト**を
#   絶対パスで指す。venv は worktree でも共有（symlink）されるため、worktree から起動しても
#   main の実装が読まれ、「殻だけ新・実装は旧」という組合せになる。実測では worktree の
#   /tf_period_profile が `handle_tf_period_profile() got an unexpected keyword argument 'va'`
#   で 500 になった（2026-08-08）。
#   PYTHONPATH は site-packages（.pth）より**先に**解決されるため、ここで自分の REPO_ROOT 由来の
#   パスを前置すれば、起動元のツリーが必ず勝つ。
#
# 規約: 既存の PYTHONPATH は捨てず後ろへ継ぐ（呼び出し側の意図を壊さない）。
#   パスの一覧は tools/dev_paths.txt が唯一源（本スクリプトは値を書き写さない）。

if [ -z "${REPO_ROOT:-}" ]; then
  echo "dev_paths.sh: REPO_ROOT が未設定です（source 前に設定してください）" >&2
  return 1 2>/dev/null || exit 1
fi

_dev_paths_ledger="${REPO_ROOT}/tools/dev_paths.txt"
if [ ! -f "$_dev_paths_ledger" ]; then
  echo "dev_paths.sh: パス台帳が見つかりません: $_dev_paths_ledger" >&2
  return 1 2>/dev/null || exit 1
fi

_dev_paths_joined=""
while IFS= read -r _dev_paths_line || [ -n "$_dev_paths_line" ]; do
  case "$_dev_paths_line" in
    ''|'#'*) continue ;;
  esac
  if [ "$_dev_paths_line" = "." ]; then
    _dev_paths_abs="$REPO_ROOT"
  else
    _dev_paths_abs="${REPO_ROOT}/${_dev_paths_line}"
  fi
  if [ -z "$_dev_paths_joined" ]; then
    _dev_paths_joined="$_dev_paths_abs"
  else
    _dev_paths_joined="${_dev_paths_joined}:${_dev_paths_abs}"
  fi
done < "$_dev_paths_ledger"

export PYTHONPATH="${_dev_paths_joined}${PYTHONPATH:+:${PYTHONPATH}}"

unset _dev_paths_ledger _dev_paths_line _dev_paths_abs _dev_paths_joined

# --- ツリー固有の環境設定（ISSUE-365）------------------------------------------
#
# 病因: データ実体（data/marketdata）と venv は git 管理外のため、worktree を作った瞬間に
#   両方とも存在しない。一方コードはこれらを**ツリー相対**で探す（indicator_ui/serve.sh:16、
#   marketdata/paths.py の既定値）。そのため worktree から起動するには各ツリーに実体か
#   symlink が要り、実際に symlink を張ることになる。
#
#   その symlink をコミットしてしまうと、中身は張り元の絶対パス（/workspaces/app/...）なので、
#   本チェックアウトへ checkout された瞬間に「自分自身を指す symlink」へ置換され、実体参照が
#   失われる。2026-08-10 に実際に起き、venv と marketdata の両方が参照不能になってサーバが
#   起動しなくなった（ISSUE-363）。
#
# 是正の考え方: 「コミットしないよう気をつける」では再発する（.gitignore は既追跡ファイルに
#   効かず、`git add -A` は無差別に拾う）。**symlink を張る理由そのものを消す**。
#   ツリー相対でなく環境変数の絶対パスで指せるようにすれば、各ツリーに実体を置く必要が無くなる。
#
# 運用: 各ツリーが自分の dev_paths.local.sh を持つ（.gitignore 済み＝コミットの候補にならない）。
#   無くても従来どおり動く（ツリー相対の既定へフォールバック）ため、既存の運用は壊れない。
_dev_paths_local="${REPO_ROOT}/dev_paths.local.sh"
if [ -f "$_dev_paths_local" ]; then
  . "$_dev_paths_local"
fi
unset _dev_paths_local
