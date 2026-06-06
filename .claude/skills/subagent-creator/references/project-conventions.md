# プロジェクト規約 詳細リファレンス（推奨／仮説）

subagent-creator スキル付録 B から外出しされた詳細リファレンス。
本ファイルは規範ではなく参照データを含む。運用ルールの本体は `${CLAUDE_SKILL_DIR}/SKILL.md` 付録 B を参照。

> **注記**：本ファイルの内容はプロジェクト固有の運用合意であり、公式仕様ではない。採用は任意。

---

## 目次

| 章 | 内容 | 主な参照タイミング |
|---|------|------------------|
| [§B.1](#b1-既存エージェント命名衝突回避リスト推奨仮説) | 既存エージェント命名衝突回避リスト | step S-3 `name` 一意性検証時 |
| [§B.2](#b2-ログ記録推奨仮説) | spec-driven-development.md §0.6 ログスキーマ準拠 | Phase 起動相当の判断を含む subagent 設計時 |
| [§B.3](#b3-description-冒頭ラベル-proactively-推奨仮説) | description 冒頭ラベル `PROACTIVELY` | step S-5 description テンプレート適用時 |
| [§B.4](#b4-出力フォーマット統一abcd-グレード方式推奨仮説) | A/B/C/D グレード方式と深刻度ラベル方式 | step S-7 [A] 採用時 / step S-8 [B] レビュー時 |

---

## §B.1 既存エージェント命名衝突回避リスト（推奨／仮説）

プロジェクト内に既に存在する以下命名のエージェントは spec-driven-development.md 配下の管理者役を担う。新規 subagent は `*-executor` 接尾辞を避ける（推奨／仮説：命名衝突防止）。

```
Architecture-executor / Coding-executor / Git-executor /
CodeReview-executor / Document-executor / Security-executor /
SystemBasicDesigner-executor / SystemInternalDesigner-executor /
TDD-executor / EscalationProcedure-executor
```

公式組み込み subagent との衝突回避は `${CLAUDE_SKILL_DIR}/references/official-spec.md` §A.4 を併読。

---

## §B.2 ログ記録（推奨／仮説）

サブエージェントが Phase 起動相当の判断（複数領域に跨る判断・対立解消等）を行う場合は、spec-driven-development.md §0.6 ログスキーマに準拠する（推奨／仮説）。

該当する subagent 設計時は本文に「ログ出力先」「ログスキーマ準拠」を明記する。

---

## §B.3 description 冒頭ラベル `PROACTIVELY`（推奨／仮説）

PROACTIVELY 起動を意図する場合は description 冒頭に大文字で `PROACTIVELY` を置く（推奨／仮説：プロジェクト内既存エージェントとの整合）。

公式ドキュメントの表現「use proactively」と機能的に等価だが、プロジェクト内で表記を統一する目的で採用する。

---

## §B.4 出力フォーマット統一（A/B/C/D グレード方式・推奨／仮説）

プロジェクト内の評価系エージェント（Architecture-executor 等）は `A/B/C/D` グレード方式を採用している。新規 subagent が評価レポートを出力する場合はこれに準拠する（推奨／仮説）。

レビュー結果（モード [B]）の深刻度ラベルは以下を採用する：

| 深刻度 | アイコン | 適用条件 |
|--------|---------|---------|
| CRITICAL | 🔴 | 公式仕様違反・自動委譲不発火 |
| WARNING | 🟡 | 解釈余地・最小権限違反 |
| INFO | 🔵 | 改善推奨 |
