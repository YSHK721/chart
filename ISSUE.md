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
