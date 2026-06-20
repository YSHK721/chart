# upstream-input-validation

## 上流入力の整理

本タスクの上流入力を 4 種別で分類：

1. **依頼者指示**：1 件
   - 4 件の原子的コミット投入 + 除外3群の明示
   
2. **他者レビュー指摘**：0 件

3. **前段成果物**：1 件
   - 現在ブランチ `develop` のファイル状態（git status より）
   
4. **既存合意の引き継ぎ**：1 件
   - `.claude/CLAUDE.md` の「破壊的な変更禁止」「承認が必要な操作」ルール

判定：上流入力 3 件存在 → 続行

---

## 前提抽出

### 上流入力 #1：依頼者指示（4 コミット分割）
主張：「残りの未ステージ/未追跡ファイルを4件の原子的コミットに分けて作成」

内在する前提：
- `.gitignore` は修正済みで ステージ対象
- `.doc/backtest/` 配下の 5 ファイルが すべて存在する
- `simulator/tests/fixtures/mt5/ma_slope_jp225_202601/expected/report.json` が 存在する
- `docs/testing-notes.md` が 存在する
- 各ファイルは テキスト形式または JSON 形式（binary でない）
- 除外対象 3 群 は リポジトリ source ではない（ランタイムデータ）

独立検証可能性：✓ (git status / ls / find で確認可能)

---

## 証拠先行検証

### 前提 1-1：.gitignore が修正済みで存在する

Git status 出力：
On branch develop
Your branch is ahead of 'origin/develop' by 48 commits.
  (use "git push" to publish your local commits)

Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
  (use "git restore <file>..." to discard changes in working directory)
	modified:   .gitignore

no changes added to commit (use "git add" and/or "git commit -a")

判定：Modified - 存在する ✓

### 前提 1-2：.doc/backtest/ 配下に 5 ファイルが存在する

ファイル一覧：
BACKTEST_CLEAN_ARCH.md
BACKTEST_DESIGN.md
BACKTEST_METRICS.md
BACKTEST_PROCESS.md
BACKTEST_SPEC.md

判定：5 ファイル確認 ✓

### 前提 1-3：report.json が存在する

判定：EXISTS ✓

### 前提 1-4：docs/testing-notes.md が存在する

判定：EXISTS ✓

### 前提 2：除外対象 3 群はリポジトリ source ではない

判定：除外対象の分離確認 ✓

---

## 判定結果

| # | 上流入力 | 採用 | 理由 |
|---|---|---|---|
| 1 | 依頼者指示（4 コミット分割） | 採用 | 全 5 前提の実証取得。ファイル存在・形式確認済み |
| 2 | 前段成果物（git status） | 採用 | リポジトリ状態の客観的確認 |
| 3 | CLAUDE.md ルール継承 | 採用 | 既存合意準拠 |

---

## 残存リスク

- ファイル内容の semantic 検証は human review に委ねる
- Conventional Commits メッセージ の「why」品質評価は commit author 責務

判定：PASS
