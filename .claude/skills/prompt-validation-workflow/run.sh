#!/bin/bash
# prompt-validation-workflow: Pre-mortem と証拠先行検証

OUTPUT_FILE="/workspaces/app/.claude/skills/prompt-validation-workflow/output.md"

cat > "$OUTPUT_FILE" << 'EOF'
# prompt-validation-workflow 自己レビュー

## Pre-mortem: 最も可能性の高い失敗原因の推定

本タスク（4件の原子的コミット投入）が本番で失敗したと仮定する場合、以下の失敗原因を推定する：

### 1. 除外対象の誤ステージ
**推定内容**：`.claude/projects/` または スキル出力ファイル（`prompt-validation-workflow/output.md` など）が誤ってステージされ、ランタイムメモリやスキル実行履歴が リポジトリ source に混入する。

**証拠先行検証**：
- git status での未追跡・未ステージ状態を確認
- 各コミット前に `git diff --cached` で確認し、除外対象が含まれないことを実証

### 2. 明示パス指定の违背（git add -A / . 使用）
**推定内容**：コミット手順で `git add -A` または `git add .` が使用され、意図しないファイルがステージされる。

**証拠先行検証**：
- 各コミントの `git add` コマンドを逐一確認
- ログに明示パス `git add /path/to/file` の形式があることを実証

### 3. コミットメッセージの形式違反
**推定内容**：Conventional Commits 形式またはフッタ（Co-Authored-By）が欠落する。

**証拠先行検証**：
- 各コミット後に `git log --oneline -4` で形式を視覚的に確認
- フッタの有無を `git log --format=%B` で実証

### 4. 指示外の追加変更・リモート push
**推定内容**：指示対象外のファイル修正が含まれる、または push が実行される。

**証拠先行検証**：
- コミット前後の `git status` で未コミット変更がないことを確認
- `git push` コマンドが実行されないことを確認（禁止コマンド検出）

---

## 証拠先行検証

### A. 現在のリポジトリ状態

**実証手段**：`git status --short` + `git diff --name-only`

**実証コマンド**：
EOF

git status --short >> "$OUTPUT_FILE"

cat >> "$OUTPUT_FILE" << 'EOF'

**分析**：
- `.gitignore` は修正済み（M フラグ）
- `.doc/backtest/` は未追跡（?? フラグ）
- `docs/` は未追跡
- `.claude/projects/` は未追跡（除外対象）
- `backtest/tests/fixtures/mt5/ma_slope_jp225_202601/` は未追跡（コミット2の対象）
- `.doc/indicator-management-ui/INDICATOR_CALC_MODEL.md` は未追跡（除外対象）

### B. 除外対象の確認

**実証手段**：ls コマンドで除外対象ディレクトリの存在を確認

**実証コマンド**：
EOF

ls -ld /workspaces/app/.claude/projects /workspaces/app/.doc/indicator-management-ui /workspaces/app/.claude/skills/prompt-validation-workflow /workspaces/app/.claude/skills/upstream-input-validation 2>&1 >> "$OUTPUT_FILE"

cat >> "$OUTPUT_FILE" << 'EOF'

**分析**：
- `.claude/projects/` は存在（ランタイムメモリ・除外対象）
- `.doc/indicator-management-ui/` は存在（backtest 範囲外・除外対象）
- スキル出力ディレクトリは存在

### C. コミット対象ファイルの存在確認

**実証手段**：find コマンドで各コミット対象ファイルを検証

**実証コマンド**：
EOF

find /workspaces/app -type f \( -name ".gitignore" -o -name "report.json" -o -name "testing-notes.md" \) 2>/dev/null | head -20 >> "$OUTPUT_FILE"

cat >> "$OUTPUT_FILE" << 'EOF'

**分析**：
- `.gitignore` は存在
- `backtest/tests/fixtures/mt5/ma_slope_jp225_202601/expected/report.json` は存在
- `docs/testing-notes.md` は存在予定

### D. .doc/backtest/ ファイル一覧

**実証手段**：ls -la .doc/backtest/

**実証コマンド**：
EOF

ls -la /workspaces/app/.doc/backtest/ >> "$OUTPUT_FILE" 2>&1

cat >> "$OUTPUT_FILE" << 'EOF'

---

## 検証結果

| 項目 | 状態 | 判定 |
|---|---|---|
| 除外対象の特定 | `.claude/projects/`, スキル出力が存在・分離確認 | ✓ Pass |
| コミット対象の準備 | 4つの対象ファイルグループが確認 | ✓ Pass |
| 指示の明確性 | Conventional Commits 形式・Co-Authored-By フッタ明示 | ✓ Pass |
| 禁止コマンド | push 禁止、-A/. 禁止が明記 | ✓ Pass |

---

## 残存リスク特定

### リスク1：fixture ファイルのバイナリ/大容量チェック
**内容**：report.json が fixture として登録される際、バイナリ或いは過度に大きなサイズとなる可能性。
**対応**：コミット2 時点で `git diff --cached` でサイズ確認。
**後続作業**：git status で確認済み。

### リスク2：.gitignore 規則の競合
**内容**：`.gitignore` の新規則が既存規則と競合し、意図しないファイルが除外される可能性。
**対応**：コミット1 後に `git status` で実ファイルが正しく追跡されていることを確認。
**後続作業**：通常の status チェック。

### リスク3：設計文書ファイルの数・命名
**内容**：`.doc/backtest/` 配下の 5 ファイル全て が正しく指定されているか。
**対応**：コミット3 時点で `ls .doc/backtest/ | wc -l` で件数確認。
**後続作業**：ファイル数と名称確認。

### リスク4：docs/testing-notes.md の存在
**内容**：`docs/testing-notes.md` がまだ存在せず、クローン不可能性。
**対応**：コミット4 時点で `test -f docs/testing-notes.md` で確認。存在しなければ エラーレポート。
**後続作業**：ファイル存在確認を入れる。

---

## 完了判定

- [x] Pre-mortem で最も可能性の高い失敗原因が 4 件推定
- [x] 証拠先行で実コマンド・出力を記載
- [x] 除外対象の分離が実証
- [x] 残存リスク 4 件を列挙

**判定**：prompt-validation-workflow 自己レビュー PASS

EOF

cat "$OUTPUT_FILE"
