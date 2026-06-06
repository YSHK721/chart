# Claude Code サブエージェント公式仕様サマリー（即時参照用）

subagent-creator スキルから参照される公式仕様の即時参照用サマリー。
一次出典：Claude Code 公式ドキュメント「カスタムサブエージェントの作成」（`https://code.claude.com/docs/llms.txt` 配下のサブエージェント章）。

---

## 目次

| 章 | 内容 |
|---|------|
| [A.1](#a1-frontmatter-フィールド一覧公式準拠) | frontmatter フィールド一覧（16 項目） |
| [A.2](#a2-保存先と優先順位公式準拠) | 保存先と優先順位（Enterprise / CLI / Project / Personal / Plugin） |
| [A.3](#a3-メモリスコープ別ディレクトリ公式準拠) | メモリスコープ別ディレクトリ |
| [A.4](#a4-組み込みサブエージェント衝突回避用公式準拠) | 組み込みサブエージェント一覧（衝突回避用） |
| [A.5](#a5-サブエージェントの構造的制約公式準拠) | サブエージェントの構造的制約 |
| [A.6](#a6-サブエージェントモデル解決順序公式準拠) | サブエージェントモデル解決順序 |

---

## A.1 frontmatter フィールド一覧（公式準拠）

| フィールド | 必須 | 型 | 既定値 |
|-----------|-----|---|--------|
| `name` | ✅ | string（小文字・ハイフン） | — |
| `description` | ✅ | string | — |
| `tools` | — | string list | 親から継承 |
| `disallowedTools` | — | string list | なし |
| `model` | — | string（`sonnet` / `opus` / `haiku` / 完全 ID / `inherit`） | `inherit` |
| `permissionMode` | — | enum（`default` / `acceptEdits` / `auto` / `dontAsk` / `bypassPermissions` / `plan`） | `default` |
| `maxTurns` | — | int | 制限なし |
| `skills` | — | string list | なし |
| `mcpServers` | — | object/list | 親から継承 |
| `hooks` | — | object | なし |
| `memory` | — | enum (`user` / `project` / `local`) | なし |
| `background` | — | boolean | `false` |
| `effort` | — | enum (`low` / `medium` / `high` / `xhigh` / `max`) | セッション継承 |
| `isolation` | — | enum (`worktree`) | なし |
| `color` | — | enum（`red` / `blue` / `green` / `yellow` / `purple` / `orange` / `pink` / `cyan`） | なし |
| `initialPrompt` | — | string | なし |

> 公式：「積極的な委譲を促進するには description フィールドに『use proactively』などのフレーズを含める」。

## A.2 保存先と優先順位（公式準拠）

| 場所 | スコープ | 優先度 |
|-----|---------|--------|
| 管理設定の `.claude/agents/` | 組織全体 | 1（最高）|
| `--agents` CLI フラグ | 現在のセッション | 2 |
| `.claude/agents/<name>.md` | 当該プロジェクト | 3 |
| `~/.claude/agents/<name>.md` | 全プロジェクト | 4 |
| プラグインの `agents/` ディレクトリ | プラグイン有効範囲 | 5（最低）|

## A.3 メモリスコープ別ディレクトリ（公式準拠）

| `memory` | 保存先 |
|---------|--------|
| `user` | `~/.claude/agent-memory/<name>/` |
| `project` | `.claude/agent-memory/<name>/` |
| `local` | `.claude/agent-memory-local/<name>/` |

## A.4 組み込みサブエージェント（衝突回避用・公式準拠）

| 名称 | モデル | ツール | 用途 |
|-----|-------|-------|------|
| `Explore` | Haiku | 読み取り専用 | コードベース検索・分析 |
| `Plan` | inherit | 読み取り専用 | プランモード時のリサーチ |
| `general-purpose` | inherit | 全ツール | 探索＋実行が必要な複雑タスク |
| `statusline-setup` | Sonnet | — | `/statusline` 実行時 |
| `Claude Code Guide` | Haiku | — | Claude Code 機能の質問 |

> 上記の名前はカスタム subagent の `name` として使用しない（衝突するため）。

## A.5 サブエージェントの構造的制約（公式準拠）

- サブエージェントは他のサブエージェントを生成できない（ネスト不可）
- メイン会話の作業ディレクトリで開始する。`cd` は bash 呼び出し間で永続化されない。親には影響しない
- システムプロンプトは frontmatter 本文のみ。Claude Code のデフォルトシステムプロンプトは継承しない
- プラグインから提供される subagent は `hooks` / `mcpServers` / `permissionMode` を無視する
- 親が `bypassPermissions` / `acceptEdits` / `auto` のとき、子の `permissionMode` は親に上書きされる
- `Stop` フックは frontmatter では `Stop` と書く（実行時 `SubagentStop` に自動変換される）
- v2.1.63 で Task ツールは Agent に名称変更された。`Task(...)` 参照はエイリアスとして引き続き機能する

## A.6 サブエージェントモデル解決順序（公式準拠）

1. `CLAUDE_CODE_SUBAGENT_MODEL` 環境変数（設定されている場合）
2. 呼び出しごとの `model` パラメーター
3. サブエージェント定義の `model` フロントマター
4. メイン会話のモデル
