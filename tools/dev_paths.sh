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
