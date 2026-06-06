#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# sync-claude-md.sh
#   正本リポジトリの md ファイル群を他の複数リポジトリへ反映する
# ============================================================
#
# ■ 正本（canonical source）
#   このリポジトリ（YSHK721/claude）を「正本」とし、配下の md を
#   他リポジトリの同一パスへ配布する。CLAUDE.md に限らず任意の md に対応。
#
# ============================================================
# ■ 一連の作業手順（初回 / 反映先が増えたとき）
# ============================================================
#
#   STEP 0) GitHub CLI 認証（未認証の場合のみ・初回1回）
#     gh auth login
#     gh auth setup-git          # git の HTTPS 連携を有効化（push 認証に必要）
#
#   STEP 1) 反映先リポジトリの一覧取得
#     gh repo list <OWNER> --limit 200 --json name,url,isArchived \
#       --jq '.[] | select(.isArchived==false) | "\(.name)\t\(.url).git"'
#     # 例: <OWNER> = YSHK721 。正本リポジトリ（claude）は除いて REPOS に記入。
#
#   STEP 2) 反映先（REPOS）と反映対象（FILES / SYNC_DIRS）を編集
#
#   STEP 3) DRY-RUN で差分確認（対象リポは一切変更しない）
#     bash .claude/scripts/sync-claude-md.sh
#
#   STEP 4) 問題なければ実反映（commit & push）
#     DRY_RUN=0 bash .claude/scripts/sync-claude-md.sh
#
# ============================================================
# ■ 反映対象の指定方法（2 通り併用可）
# ============================================================
#   FILES     : 個別ファイルを列挙。正本リポルートからの相対パス。
#               既定は src=dest。配置先を変える場合のみ "src:dest" 形式。
#   SYNC_DIRS : 指定ディレクトリ配下の *.md を再帰的に丸ごと反映（src=dest）。
#
# ============================================================
# ■ 重要な注意（全上書きの破壊性）
# ============================================================
#   各対象ファイルは正本で「全上書き」される。反映先に独自記述があると消える。
#   - DRY-RUN の差分（削除行 = '-'）で独自内容の有無を必ず確認すること。
#   - 消えても各リポの git 履歴に旧版が残り復元可能:
#       git -C <repo> show <旧コミットSHA>:<path>
#   - 独自内容を残したい対象は本スクリプトに含めず手動マージすること。
#
# ============================================================
# ■ オプション（環境変数）
# ============================================================
#   DRY_RUN=1  既定。差分表示のみ。対象リポを変更しない（push しない）。
#   DRY_RUN=0  実反映（commit & push）。
# ============================================================

# --- 反映先リポジトリ（STEP 2）。正本リポジトリ（claude）は含めない ---
REPOS=(
  #"https://github.com/YSHK721/GetAccelerometerData.git"
  #"https://github.com/YSHK721/PokerSolverGTO.git"
  #"https://github.com/YSHK721/OpenMythos.git"
  "https://github.com/YSHK721/TEMPLATE.git"
  #"https://github.com/YSHK721/template-clean-architecture-python.git"
  #"https://github.com/ya721/create_mcp.git"
  #"https://github.com/ya721/shipment-aggregation-tool.git"
  #"https://github.com/ya721/wpt-00.git"
  #"https://github.com/ya721/product_search.git"
  #"https://github.com/ya721/TEMPLATE.git"
  #"https://github.com/ya721/shipment-aggregation-tool-BK260427-01.git"

)

# --- 反映対象：個別ファイル（リポルート相対。"src:dest" で配置先変更可）---
FILES=(
  ".claude/CLAUDE.md"
  # "docs/policy.md:.claude/policy.md"
)

# --- 反映対象：ディレクトリ配下の *.md を一括（リポルート相対）---
SYNC_DIRS=(
  # ".claude/rules"
  ".claude/"
)

# --- 設定 ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CANON_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"   # 正本リポジトリのルート
MSG="docs: sync shared md files from canonical source

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
DRY_RUN="${DRY_RUN:-1}"

# git が gh のアクティブアカウント認証で private リポも clone/push できるよう設定（冪等）
if command -v gh >/dev/null 2>&1; then
  gh auth setup-git 2>/dev/null || true
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# --- 反映対象リスト（相対パス）を構築 ---
TARGET_SRC=()   # 正本リポルート相対
TARGET_DST=()   # 反映先リポルート相対
add_target() { TARGET_SRC+=("$1"); TARGET_DST+=("$2"); }

for entry in "${FILES[@]:-}"; do
  [ -z "$entry" ] && continue
  if [[ "$entry" == *:* ]]; then
    add_target "${entry%%:*}" "${entry##*:}"
  else
    add_target "$entry" "$entry"
  fi
done

for dir in "${SYNC_DIRS[@]:-}"; do
  [ -z "$dir" ] && continue
  if [ ! -d "$CANON_ROOT/$dir" ]; then
    echo "WARN: SYNC_DIRS に存在しないディレクトリ: $dir（スキップ）"; continue
  fi
  while IFS= read -r f; do
    rel="${f#"$CANON_ROOT"/}"
    add_target "$rel" "$rel"
  done < <(find "$CANON_ROOT/$dir" -type f -name '*.md' | sort)
done

[ "${#REPOS[@]}" -gt 0 ]       || { echo "ERROR: REPOS が空です。"; exit 1; }
[ "${#TARGET_SRC[@]}" -gt 0 ]  || { echo "ERROR: 反映対象（FILES / SYNC_DIRS）が空です。"; exit 1; }

# 正本側に対象ファイルが実在するか検証
for i in "${!TARGET_SRC[@]}"; do
  [ -f "$CANON_ROOT/${TARGET_SRC[$i]}" ] || { echo "ERROR: 正本に対象がありません: ${TARGET_SRC[$i]}"; exit 1; }
done

[ "$DRY_RUN" = "1" ] && echo ">>> DRY-RUN モード（差分表示のみ・対象リポを変更しません）" || echo ">>> 実反映モード（commit & push します）"
echo ">>> 正本ルート: $CANON_ROOT"
echo ">>> 反映対象: ${#TARGET_SRC[@]} ファイル / 反映先: ${#REPOS[@]} リポジトリ"
echo ""

for REPO in "${REPOS[@]}"; do
  echo "=== $REPO ==="
  if [ -d "$REPO/.git" ]; then
    DIR="$REPO"
    git -C "$DIR" pull --ff-only || echo "  WARN: pull 失敗（手動確認）"
  else
    # owner/repo を抽出し、衝突しない一意なクローン先を作る（例: ya721__TEMPLATE）
    slug="${REPO%.git}"; slug="${slug#https://github.com/}"; slug="${slug#git@github.com:}"
    DIR="$WORK/${slug//\//__}"
    git clone --depth 1 "$REPO" "$DIR" >/dev/null 2>&1 || { echo "  ERROR: clone 失敗（private 権限/URL 要確認）。スキップ。"; continue; }
  fi

  CHANGED_DST=()   # このリポで変更が生じる反映先相対パス
  for i in "${!TARGET_SRC[@]}"; do
    src="$CANON_ROOT/${TARGET_SRC[$i]}"
    dst_rel="${TARGET_DST[$i]}"
    tgt="$DIR/$dst_rel"
    # 反映先リポが .gitignore で除外している対象は尊重してスキップ（停止しない）
    if git -C "$DIR" check-ignore -q "$dst_rel" 2>/dev/null; then
      echo "  ! $dst_rel (.gitignore で除外 → スキップ)"
      continue
    fi
    if [ ! -f "$tgt" ]; then
      echo "  + $dst_rel (新規)"
      CHANGED_DST+=("$i")
    elif ! cmp -s "$src" "$tgt"; then
      echo "  ~ $dst_rel (更新)"
      git -C "$DIR" --no-pager diff --no-index --stat -- "$tgt" "$src" 2>/dev/null | sed 's/^/      /' || true
      CHANGED_DST+=("$i")
    else
      echo "  = $dst_rel (変更なし)"
    fi
  done

  if [ "${#CHANGED_DST[@]}" -eq 0 ]; then
    echo "  → 変更なし（スキップ）"
    continue
  fi

  if [ "$DRY_RUN" = "1" ]; then
    echo "  [DRY-RUN] ${#CHANGED_DST[@]} ファイルが対象。commit/push はスキップ"
    continue
  fi

  for i in "${CHANGED_DST[@]}"; do
    src="$CANON_ROOT/${TARGET_SRC[$i]}"
    dst_rel="${TARGET_DST[$i]}"
    mkdir -p "$DIR/$(dirname "$dst_rel")"
    cp "$src" "$DIR/$dst_rel"
    git -C "$DIR" add "$dst_rel"
  done
  git -C "$DIR" commit -m "$MSG" >/dev/null
  git -C "$DIR" push
  echo "  → ${#CHANGED_DST[@]} ファイル反映・push 完了"
done

echo ""
echo "完了。"
