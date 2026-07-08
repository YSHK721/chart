# prompt-validation-workflow 自己レビュー（マージタスク）

## Pre-mortem（想定失敗原因）
本マージが完了した後に本番で失敗する最有力原因を死因究明視点で推定:

- F1: 未コミット変更との衝突でマージが停止。指示では abort で終了するはずだが、自己判断で回避策を実行してしまった場合、未コミット変更が上書きされる（破壊的変更）。
- F2: テスト失敗。indicator_ui の chart_renderer.test.js が 85 pass 未満、または replay_ui が 209 pass 未満。
- F3: 3 コミットが本来の原子性を失っている（複合目的が混在、または 1 コミット内で複数の目的が含まれている）。
- F4: コミット粒度は正しいが、マージメッセージが指定フォーマットと異なる。
- F5: バックアップブランチが作成されていない、または間違った名前で作成された。

## 証拠先行検証

### F1: 未コミット変更との衝突リスク
- 現在の git status 出力：6 個の modified ファイル（.doc/…、prototype_260626-01/、simulator/replay_ui/）が未コミット状態。
- 指示の制約：「これらは絶対に add / commit / stash / checkout で触らない」。
- マージ衝突時の対応：「git merge --abort で中止し、状況をそのまま報告して終了する（自己判断で回避策を実行しない）」。
- **本レビューでの対応**: マージ実行前に、衝突が起きた場合は abort して即報告することを明示的に確認。自己判断で stash / reset 等を実行しない。
- **実証**: git status で未コミット状態を記録済→マージ実行時に衝突検出→即 abort 実行→報告。この手順を遵守する。

### F2: テスト失敗リスク
- 指定テスト:
  - indicator_ui: `/workspaces/app/indigators/indicator_ui/web && node --test tests/chart_renderer.test.js` → 85 pass / 0 fail
  - replay_ui: `/workspaces/app/simulator/replay_ui/web && node --test tests/*.test.js` → 209 pass / 0 fail
- feature ブランチのコミット log より：
  - 6a61c54 で回帰テスト追加（chart_renderer.test.js に「setCandles: 手動スケールを破棄しない」）
  - 662485c で新テスト追加（chart_interaction_controller.test.js 145 行、composition_root_front.test.js 29 行）
- **実証**: マージ後に両テストを実行し、期待値と一致するか確認する。不一致時は逆トレースで修正原因を特定。

### F3: コミット粒度の原子性
- git log --stat 出力より、3 コミットの分離：
  - 662485c: chart_interaction_controller.js（新規）、composition_root_front.js（修正）、2 個の test ファイル（新規）→ **目的: 機能配線**
  - 6a61c54: chart_renderer.js（大規模リファクタ）、ISSUE.md（修正）、test ファイル（修正）→ **目的: lwc API 置換 + 回帰テスト**
  - f4b90f5: chart_renderer.js（6 行修正）→ **目的: ドキュメント修正**
- 各コミットが異なる目的を持ち、1 コミット = 1 目的を満たす。Conventional Commits 形式（feat / refactor / docs）も準拠。
- **実証**: git log --stat 出力を上記で記録済。原子性確認完了。

### F4: マージメッセージのフォーマット
- 指定メッセージ: `"Merge feature/replay-price-wheel-zoom: 価格軸ホイールズーム（lwc v5.2ネイティブAPI・ISSUE-045）"`
- git merge --no-ff で --message（または -m）を指定すると、そのメッセージでマージコミットが作成される。
- **実証**: git merge コマンド実行時に -m フラグで指定メッセージを渡す。

### F5: バックアップブランチ作成
- 指定名: `backup/20260706-develop-pre-price-wheel-zoom`
- 指示: 「checkout はしない・`git branch` で作成のみ」
- **実証**: git branch で作成（checkout なし）。git branch -v で確認。

## 検証（成立/棄却）
- F1: 成立（衝突リスクは存在、だが指示で abort 回避が明示されている）→ 対応策は「abort + 即報告」で確認済。
- F2: 成立（テスト失敗リスク存在）→ マージ後に両テストを実行して検証。
- F3: 棄却（コミット粒度は原子的、Conventional Commits 準拠を実証済）。
- F4: 棄却（マージメッセージのフォーマット指定は明確、実装時に指定可能）。
- F5: 棄却（バックアップブランチ作成は単純な git branch コマンド、手順に従って確認可能）。

## 反映
- F1: 衝突時の対応（abort + 報告）を実装時に厳密に遵守。
- F2: マージ後のテスト実行で実証。

## 残存リスク
- なし（マージ操作自体は機械的で、テスト結果で検証可能）。
