---
name: git
description: |
  リモート操作（push/pull/fetch/PR/CI）を一切使わずローカルリポジトリ内のみで完結する Git 運用を統制する。
  以下のいずれかに該当する場合に使用する。
  - 開発開始前にバックアップ専用ブランチ（backup/YYYYMMDD-before-development）を作成する
  - GitFlow 準拠の命名（feature/fix/refactor/docs/test/chore/hotfix）でローカルブランチを切る
  - Conventional Commits 形式（type(scope): summary）で原子的（単一責任）にコミットする
  - rebase または merge --no-ff によりローカルで develop へ取り込む
  - ローカルでコンフリクトを解決し差分パッチ・worktree 比較で自己レビューを行う
  - 緊急修正（hotfix）を main 起点で作成し main / develop に反映する
  使用しないケース：リモート操作（push/pull/fetch/PR/CI）を伴う運用は本スキル対象外。
when_to_use: ファイル更新後・コミット前・ブランチ切替前・develop 取り込み前・コンフリクト発生時にローカル Git 状態を確認し原子的コミットへ進む判断が必要な場合
context: fork
disable-model-invocation: true
allowed-tools:
  - Bash(git status:*)
  - Bash(git branch:*)
  - Bash(git switch:*)
  - Bash(git log:*)
  - Bash(git diff:*)
  - Bash(git show:*)
  - Bash(git add:*)
  - Bash(git commit:*)
  - Bash(git rebase:*)
  - Bash(git merge:*)
  - Bash(git tag:*)
  - Bash(git worktree:*)
  - Bash(git archive:*)
  - Bash(git blame:*)
  - Bash(git difftool:*)
effort: medium
---

# 目的

ローカル Git 運用を原子的・安全に統制する

## §0. 仮説と事実の区別

| 区分 | 取り扱い | 表記 |
|---|---|---|
| Git 公式仕様（事実） | そのまま採用する | 出典なしで断定する |
| プロジェクト規約 | プロジェクト内で適用する | 「プロジェクト規約より」と明示する |
| 推奨（仮説） | 採用は任意。根拠を併記する | 「（推奨／仮説）」と明示する |
| 不明 | TBD として未解決事項に記録する | 「TBD: 確認要」と明示する |

本スキルは Conventional Commits / GitFlow 由来の外部規約と、プロジェクト固有の運用合意（リモート操作禁止・バックアップブランチ不変・develop 基点）を区別して扱う。出所が混在するため §0 を設置する（推奨／仮説：プロジェクト規約 B.3）。

## §1. 定義

「ローカル完結 Git 運用」とは、`push` / `pull` / `fetch` / Pull Request / CI / リモート保護ルールを一切使用せず、すべての Git 操作をローカルリポジトリ内のみで実行する運用形態を指す。リモート同期が発生する操作は本スキル対象外として扱う。

## §2. 原理原則

- 開発開始前に必ず `backup/YYYYMMDD-before-development` ブランチを作成し、不変保護領域として保持する
- 1 コミット = 1 目的（単一責任）。曖昧語のみのメッセージを禁止し、意図と影響範囲を明示する
- `develop` を基点とし、`main` へは緊急時 hotfix のみ反映する
- すべての Git 操作はローカルで実施し、`push` / `pull` / `fetch` / PR / CI を一切使用しない
- 強制操作（`reset --hard` / `rebase --force` / 強制削除）はバックアップ確認後に限定する
- 秘密情報（鍵・トークン・個人情報）はコミットしない
- pre-commit / テスト自動化が無い場合でも、ローカルで静的解析・テストを手動実行する
- エラー検出時は即時報告し、バックアップへ安全にロールバックできる状態を維持する

## §3. 用語定義

| 用語 | 定義 |
|---|---|
| バックアップ専用ブランチ | 開発開始前に作成する不変ブランチ。命名規則 `backup/YYYYMMDD-before-development`。変更・マージ・rebase・squash・削除を禁止する |
| 原子的コミット | 単一の目的（1 機能 / 1 修正 / 1 設定 / 1 テスト）に絞られたコミット |
| Conventional Commits | `type(scope): summary` 形式のコミットメッセージ規約。type 例：`feat`/`fix`/`refactor`/`docs`/`test`/`style`/`chore`/`perf`/`build`/`ci`/`revert` |
| GitFlow 準拠命名 | `feature/<ID>-<説明>` `fix/<ID>-<説明>` `refactor/<対象>-<説明>` `hotfix/<内容>` 等のブランチ命名規則 |
| ローカルレビュー | PR を使わずに `worktree` 並行確認・差分パッチ生成・`git show` 自己レビューで品質を担保する手順 |
| ローカル品質ゲート | lint / format / secret-scan / 静的解析 / テストをローカルで実施するチェック群 |
| 曖昧コミット | 「修正」「更新」「変更」のみ等、type / scope / 影響範囲が不明なコミットメッセージ |

## §4. 強制ルール

| ルール | 内容 |
|---|---|
| 改変禁止 | 指示以外の改変は絶対に禁止する |
| 主観禁止 | 主観・感想・印象の出力は絶対に禁止する |
| リモート操作禁止 | `git push` / `git pull` / `git fetch` / PR 作成 / CI 起動を一切実行しない |
| バックアップ不変 | バックアップ専用ブランチへの変更・マージ・rebase・squash・削除を禁止する |
| 主要ブランチ保護 | `develop` を削除しない。`main` への直接コミットは hotfix を除き禁止する |
| 強制操作の保護 | `reset --hard` / `rebase --force` / `branch -D` はバックアップ確認後に限定する |
| 秘密情報禁止 | 鍵・トークン・個人情報のコミットを禁止する |
| 曖昧コミット禁止 | 曖昧語のみのメッセージを禁止する。意図と影響を箇条書きで明示する |
| 思考代替防止 | 各 step の「手順」は必ず「判定基準」と併記する。手順単独記述を禁止する（D1）|
| 判定基準優先 | 各 step で「手順」と「判定基準」が衝突した場合は判定基準を優先する。手順への機械的従属を禁止する（D10）|

## §5. 実行プロセス

本スキルの中核フロー（S-1 〜 S-8）は順序付き手順を含むため、思考代替リスク防御 D1（原理ラッピング）が適用される。各 step で「手順」を提示する場合は必ず「判定基準」と併記し、判定基準が手順を原理的に拘束する。手順単独の機械的踏襲は §4「思考代替防止」ルールで禁止される。

### step S-1: 状態確認とバックアップ
- **判定基準**：未コミット変更・未追跡ファイル・未解決コンフリクトの有無で初期状態を分類し、バックアップ未作成の場合は最優先で作成すべきと判断する
- **アンチパターン**：状態未確認のままブランチ切替・コミットを実行する／バックアップ作成を後回しにする
- **手順**：
  1. `git status` / `git branch` / `git log --oneline --graph --decorate -n 20` を実行
  2. 必要に応じ `git diff`（ワークツリー vs インデックス）/ `git diff --cached`（インデックス vs HEAD）で差分確認
  3. バックアップ未作成なら `git switch develop` → `git switch -c backup/YYYYMMDD-before-development`
  4. 任意で `git tag backup-YYYYMMDD-start` / `git archive --format=zip --output=backup-YYYYMMDD.zip HEAD` により多重保全

### step S-2: ブランチ作成と命名検証
- **判定基準**：作業 type（機能 / バグ / リファクタ / ドキュメント / テスト / 雑務 / 緊急）と起点ブランチ（develop / main）を分類し、命名規則と起点の整合を判定する
- **アンチパターン**：`main` から `feature/*` を切る／type 省略・自由記述／`develop` 未同期で作業開始
- **手順**：
  1. 起点に切替：通常作業は `git switch develop`、緊急のみ `git switch main`
  2. type に応じて `feature/` `fix/` `refactor/` `docs/` `test/` `chore/` `hotfix/` のいずれかを選び、`<ID>-<簡潔説明>` を付加
  3. `git switch -c <type>/<ID>-<説明>` で作成

### step S-3: 原子的コミット実行
- **判定基準**：1 コミット = 1 目的を満たすか／関連ファイル・テスト・ドキュメントが整合しているか／メッセージが Conventional Commits 準拠かつ曖昧語を含まないか
- **アンチパターン**：複数目的の混在コミット／関連テスト未更新／曖昧語のみのメッセージ／秘密情報の混入
- **手順**：
  1. `git add -p` で粒度を調整、またはファイル単位で `git add <path>`
  2. ローカルで lint / format / secret-scan / 静的解析 / テストを実行し合格を確認
  3. `type(scope): summary` 形式でメッセージ作成。本文に意図・影響を箇条書き、必要に応じ `Refs: #<ID>` を付記
  4. `git commit -m "..."` を実行

### step S-4: ローカルレビュー
- **判定基準**：差分が意図通りか／影響範囲が想定内か／コミット履歴が読みやすく整理されているか
- **アンチパターン**：自己レビュー省略／worktree 未使用での副作用ある検証／差分パッチを残さない
- **手順**：
  1. `git log --oneline` / `git show <sha>` で自己レビュー
  2. 必要に応じ `git worktree add ../review_tree <branch>` で並行確認
  3. 必要に応じ `git diff develop...<branch> > review.patch` で差分保管

### step S-5: develop への取り込み
- **判定基準**：履歴方針（履歴を残す `--no-ff` / すっきり履歴 `--ff-only`）と最新 develop への同期完了を判定する
- **アンチパターン**：未同期のまま merge／取り込み中の `reset --hard`／コンフリクト未解決での `--continue`
- **手順**：
  1. `git switch <branch>` → `git rebase develop`（コンフリクト時は step S-6 へ）
  2. テスト再実行で合格確認
  3. 履歴を残す方針：`git switch develop` → `git merge --no-ff <branch>`
  4. すっきり履歴方針：`rebase -i` で整理 → `git switch develop` → `git merge --ff-only <branch>`
  5. `git branch -d <branch>` で作業ブランチ削除

### step S-6: コンフリクト解決
- **判定基準**：衝突箇所の意図的整合・テスト合格・差分再点検が完了しているか
- **アンチパターン**：衝突マーカー残置／片側のみ採用で意図無視／解決後のテスト省略
- **手順**：
  1. `git status` で衝突ファイル特定
  2. 衝突ファイルを編集して解消
  3. `git add <resolved-files>` でステージング
  4. rebase 中は `git rebase --continue`、merge 中は `git commit -m "fix: resolve merge conflicts in <module> due to <reason>"`
  5. テスト再実行と差分再点検で動作確認

### step S-7: マージ後クリーンアップと記録
- **判定基準**：`develop` のビルド・テスト合格／必要なローカルタグ付与／バックアップブランチ保全を確認する
- **アンチパターン**：`develop` の削除／バックアップブランチへの操作／タグ付与忘れによるスナップショット欠落
- **手順**：
  1. `git switch develop` で取り込み先に切替
  2. ビルド・テスト合格を確認
  3. 必要に応じ `git tag local-snapshot-YYYYMMDD-<shortSHA>` を付与
  4. `git log --all --oneline | grep backup/` でバックアップブランチが不変であることを確認

### step S-8: 緊急時（Hotfix）対応
- **判定基準**：本番影響度・回帰リスクから緊急修正の起点を `main` にすべきか／`main` と `develop` 双方への反映が必要か
- **アンチパターン**：`develop` から hotfix を切る／`main` のみに反映し `develop` への取り込みを忘れる
- **手順**：
  1. `git switch main` → `git switch -c hotfix/<内容>`
  2. 修正・テスト → `git add -p` → `git commit -m "fix: <要旨>"`
  3. `git switch main` → `git merge --no-ff hotfix/<内容>` → `git tag local-hotfix-vX.Y.Z`
  4. `git switch develop` → `git merge --no-ff hotfix/<内容>`

### ステップ完了判定（思考代替リスク防御 D6）

各 step の手順完了後、次 step に進む前に以下を検証する：

- [ ] 判定基準への回答が実行結果から導出できるか
- [ ] 入力前提（リモート操作禁止・バックアップ存在・develop 基点）と矛盾していないか
- [ ] 出力（ブランチ状態・コミット結果・差分）が次 step の入力として整合するか

充足しない場合：手順を機械的に再実行せず、判定基準に立ち返り別経路を検討する。最大 3 回再試行し、3 回失敗時は §6 異常時出力で中断報告する。

## §6. 結果出力

実行完了時、以下のローカル管理レポートを Markdown 形式で出力する。

```markdown
## ローカル Git 管理状況レポート（YYYY-MM-DD）

### リポジトリ状態
- 現在のブランチ: <branch>
- 未コミット変更: <あり/なし と概要>
- ローカルブランチ数: <count>
- バックアップブランチ: <存在/不在 と名前>

### 実施内容
- 作成ブランチ: <type/ID-説明>
- 取り込み方式: <merge --no-ff / merge --ff-only / rebase>
- コミット件数: <count>
- 解決コンフリクト件数: <count>
- 付与タグ: <タグ名 または なし>

### ブランチ戦略準拠度
- 命名規則準拠: <準拠/違反 と根拠>
- 単一責任原則準拠: <準拠/違反 と根拠>
- Conventional Commits 準拠: <準拠/違反 と根拠>

### 推奨アクション
1. [高] <発見された問題 / 根拠 / 推奨対応>
2. [中] <改善提案>
3. [低] <最適化提案>
```

異常時は中断箇所の step 番号・判定基準充足状況・直前のコマンド実行結果・バックアップ復元手順を併記して報告する。
