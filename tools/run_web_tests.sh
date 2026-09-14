#!/usr/bin/env bash
# run_web_tests.sh — フロント（JS）テストを 1 コマンドで全部回す（ISSUE-280）。
#
#   使い方:  tools/run_web_tests.sh
#   終了コード: 全スイート成功で 0 / 1 つでも失敗すれば非ゼロ（失敗したスイート名を最後に列挙）。
#
# なぜ必要か:
#   スイートがスライスごとに散在し、起動方法も混在していたため「壊れて動かなくなったスイート」が
#   見えなかった（unified_ui/web が node_modules の自己参照 symlink で exit 216・出力なしのまま
#   丸一日放置。tail だけ読むと "出力が無い" としか見えない）。
#   1 コマンドに集約し、**終了コードで判定**し、失敗はスイート名付きで最後にまとめて出す。
#
# 対象スイートの一覧は tools/web_suites.txt が唯一源（ここに書き写さない）。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LEDGER="${SCRIPT_DIR}/web_suites.txt"

if [ ! -f "$LEDGER" ]; then
  echo "エラー: スイート台帳が見つかりません: $LEDGER" >&2
  exit 1
fi

failed=()
ran=0

while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    ''|'#'*) continue ;;
  esac
  suite_dir="${REPO_ROOT}/${line}"
  echo "▶ ${line}"
  if [ ! -f "${suite_dir}/package.json" ]; then
    echo "  ✗ package.json がありません（npm test で起動できません）" >&2
    failed+=("$line (package.json 不在)")
    continue
  fi
  if (cd "$suite_dir" && npm test); then
    ran=$((ran + 1))
  else
    code=$?
    echo "  ✗ 失敗（exit ${code}）" >&2
    failed+=("$line (exit ${code})")
  fi
done < "$LEDGER"

echo
if [ "${#failed[@]}" -gt 0 ]; then
  echo "失敗したスイート (${#failed[@]}):" >&2
  for f in "${failed[@]}"; do echo "  - $f" >&2; done
  exit 1
fi

echo "全 ${ran} スイート成功"
