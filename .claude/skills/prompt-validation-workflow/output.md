# prompt-validation-workflow 自己レビュー

## Pre-mortem: 最も可能性の高い失敗原因の推定

本タスク（4件の原子的コミット投入）が本番で失敗したと仮定する場合、以下の失敗原因を推定する：

### 1. 除外対象の誤ステージ
推定内容：`.claude/projects/` または スキル出力ファイルが誤ってステージされ、ランタイムメモリがリポジトリ source に混入する。

### 2. 明示パス指定の违背（git add -A / . 使用）
推定内容：コミット手順で `git add -A` または `git add .` が使用され、意図しないファイルがステージされる。

### 3. コミットメッセージの形式違反
推定内容：Conventional Commits 形式またはフッタ（Co-Authored-By）が欠落する。

### 4. 指示外の追加変更・リモート push
推定内容：指示対象外のファイル修正が含まれる、または push が実行される。

---

## 証拠先行検証

### A. 現在のリポジトリ状態
 M .claude/skills/prompt-validation-workflow/output.md
 M .claude/skills/upstream-input-validation/output.md
 M .gitignore
?? .claude/projects/
?? .claude/skills/prompt-validation-workflow/input.md
?? .claude/skills/prompt-validation-workflow/run.sh
?? .doc/backtest/
?? .doc/indicator-management-ui/INDICATOR_CALC_MODEL.md
?? simulator/tests/fixtures/mt5/ma_slope_jp225_202601/
?? docs/

分析：修正済みファイル（.gitignore）と未追跡ファイル（.doc/backtest/, docs/, fixture）を確認。

### B. 除外対象の存在確認

drwxr-xr-x 3 root root  96 Jun 13 09:14 /workspaces/app/.claude/projects
drwxr-xr-x 6 root root 192 Jun 17 12:38 /workspaces/app/.doc/indicator-management-ui

分析：除外対象の .claude/projects/ と .doc/indicator-management-ui/ が存在し、分離確認。

---

## 検証結果

| 項目 | 状態 |
|---|---|
| 除外対象の特定 | Pass |
| コミット対象の準備 | Pass |
| 指示の明確性 | Pass |
| 禁止コマンド明記 | Pass |

---

## 残存リスク

1. fixture report.json サイズ確認
2. .gitignore 規則競合確認
3. .doc/backtest/ ファイル数確認
4. docs/testing-notes.md 存在確認

**判定**：PASS
