# Issue Log

## ISSUE-001

- 概要：`programmer` サブエージェントが現 Claude Code セッションのエージェントレジストリに未登録のため起動できない
- 重大度：中（動作確認不能）
- ステータス：OPEN
- 検出日：2026-05-05
- 検出経路：メイン会話からの動作確認指示に対し `Agent({ subagent_type: "programmer" })` 呼び出しが「Agent type 'programmer' not found」で失敗
- 再現条件：
  - 対象ファイル：`/workspaces/claude/.claude/agents/programmer.md`（git 未追跡 / セッション開始後に追加）
  - 利用可能エージェント一覧に `programmer` が含まれない
- 原因（推定）：
  - Claude Code はセッション開始時にエージェント定義を読み込み、実行中はホットリロードを行わない
  - `programmer.md` はセッション開始後に作成されたため、現セッションでは認識されない
- 切り分け根拠：
  - 定義ファイルの YAML frontmatter 形式は登録済みの `coding-executor.md` と整合（`name` / `description` / `model` / `color` / `skills` 各フィールドに不足なし）
  - `tools: [Read, Write, Edit, Glob, Grep, Bash]` 配列構文は許容形式
  - `model: sonnet[1m]` も登録済みエージェントと同一値
- 対策案：
  1. Claude Code セッションを再起動し、エージェントレジストリを再読込する
  2. 再起動後に再度 `Agent({ subagent_type: "programmer" })` を呼び出して動作確認する
  3. それでも未登録の場合は、定義ファイルの差分（`spec-driven-developer.md` との比較等）から原因を再調査する
- 備考：
  - 定義ファイル本体は git 未追跡（`spec-driven-developer.md` / `spec-driven-orchestrator copy.md` も同様）。セッション再起動前にコミット要否をユーザーに確認することを推奨

## ISSUE-002

- 概要：moving_averages 追加時に `min_value(periods,2)` 違反が発生し指標を追加できない
- 重大度：中（新指標が使用不能）
- ステータス：RESOLVED
- 検出日：2026-06-10
- 検出経路：UI で移動平均線を追加するとエラー表示（違反 `min_value(periods,2)`）
- 原因：`periods`（FLOAT_LIST=`[20,50]`）に MIN_VALUE 制約を付与したが、`constraint_eval.js:evalMinValue` はスカラ前提で、配列を `[20,50] >= 2`（= `NaN >= 2` → false）と評価し常に違反になる。リスト対応しているのは `RANGE_OPEN` のみ（profit_band の probabilities が使用）。
- 対策：`periods` から MIN_VALUE 制約を除去（共有評価器 JS/Python パリティには手を入れない）。period<2 はバックエンド `add_moving_averages` が描画対象外に除外する（`test_adapter_moving_averages_skips_out_of_range_periods` で担保）ため、UI ヒント `min:2` で誘導し相関制約は付けない。
- 検証：`evaluate(def.params, 既定値)` の違反が 0 件であることを確認。両テストスイート緑。

## ISSUE-003

- 概要：moving_averages の期間を 252 等（静的 pcts 未収載値）に設定すると移動平均線が消える
- 重大度：中（任意期間が使用不能）
- ステータス：RESOLVED
- 検出日：2026-06-10
- 検出経路：UI で期間を 252 に設定すると線が消失（コンソールに `[F3] 系列名不一致のためスキップ name=SMA 252`）
- 原因：F3 系列名照合（`indicator_controller._expectedSeriesNames`）が `seriesNamePattern` の静的 pcts（`5,9,…,200`）だけを期待集合とし、backend が返す `SMA 252` を未知名として破棄していた（任意期間に未対応）。
- 対策：`_expectedSeriesNames`/`_expandPattern` を params 対応に拡張（`bucketsFromParam=ma_types`/`pctsFromParam=periods`・`bucketsUpper`/`pctsInt`）。期待集合を現在の params から動的生成し、任意期間を許容。`_validateSeriesNames`/`_draw` に params を伝搬（3 呼出点）。共有 Python 評価器は不変（F3 はフロント固有）。静的 buckets/pcts は params 未供給時のフォールバックとして温存。
- 検証：period=252 で `_expectedSeriesNames` が `SMA 252` を含み F3 通過。サーバ /compute が `SMA 252`（149 点）を返却。両テストスイート緑（JS 180 / Python 141）。
