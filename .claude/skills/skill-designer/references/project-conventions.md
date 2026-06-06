# プロジェクト規約 詳細リファレンス（推奨／仮説）

skill-designer スキル付録 B から外出しされた詳細リファレンス。
本ファイルは規範ではなく参照データを含む。運用ルールの本体は `${CLAUDE_SKILL_DIR}/SKILL.md` 付録 B を参照。

---

## 目次

| 章 | 内容 | 主な参照タイミング |
|---|------|------------------|
| [§B.1](#b1-既存スキル命名衝突回避リスト推奨仮説) | 既存スキル命名衝突回避リスト | §2.5 観点 1 / §3.1 観点 1 ディレクトリ名一意性検証時 |
| [§B.7](#b7-手順-t3--思考代替リスク防御セット-詳細推奨仮説) | 手順 T3 + 思考代替リスク防御セット 詳細（F1-F5 失敗モード / 適否マトリクス / 段階履歴） | §3.1 観点 15 検証時 / 手順 T3 採用判断時 |

---

## §B.1 既存スキル命名衝突回避リスト（推奨／仮説）

プロジェクト内・公式 examples 内に既に存在する以下のスキルと命名衝突しないよう確認する。

```
skill-creator（公式 examples 由来。本スキルは衝突回避のため skill-designer に改名）/
logical-proofreading / academic-claim-analyzer / spec-items-clarifier /
spec-writer / code-analyzer / log-analyzer / error-diagnosis /
upwork-job-analyzer / upwork-country-customs / upwork-risk-evaluator /
upwork-proposal-writer / upwork-english-translator /
code-usage-analyzer / code-context-analyzer / subagent-creator
```

公式組み込みコマンド・バンドルスキルとの衝突回避は `${CLAUDE_SKILL_DIR}/references/official-spec.md` §A.9 を参照。

---

## §B.7 手順 T3 + 思考代替リスク防御セット 詳細（推奨／仮説）

skill-designer 付録 B.7 の詳細リファレンス。本体（防御セット 3 層・操作ルール）は SKILL.md 付録 B.7 を参照。

### 思考代替リスクの 5 つの失敗モード

| # | 失敗モード | 症状 |
|---|----------|------|
| F1 | 機械的踏襲 | 入力特性を無視して (1)→(2)→(3) を全件実行 |
| F2 | 目的盲目 | 手順は完遂するが目的を達成しない |
| F3 | 矛盾耐性喪失 | 入力が手順前提と矛盾しても踏襲継続 |
| F4 | 冗長模倣 | 手順を逐語的に出力にコピー |
| F5 | 逸脱拒否 | 文脈が逸脱を要求しても手順に固執 |

### 防御セットと失敗モードの対応

| 防御 | 配置 | 内容 | 主な対象失敗モード |
|------|------|------|-----------------|
| D1 原理ラッピング | §5 intro / 各 step | 手順は判定基準と必ず併記。手順単独記述を禁止 | F1 |
| D6 完了判定ゲート | §5 末尾 | 手順完了後に判定基準充足を検証。手順遵守 ≠ ステップ完了 | F2 |
| D10 メタルール | §4 強制ルール | 「思考代替防止」「判定基準優先」2 ルール追加 | F3 / F5 |

F4 冗長模倣は D4（出力分離・運用規律）で対応するが、現状の必須セットには含まない（運用観察により今後追加検討）。

### 手順 T3 採用適否マトリクス

| 適用 | 適用例 | 不適用 | 不適用例 |
|------|--------|--------|---------|
| 順序が結果を変える操作 | system-design / error-diagnosis / tdd / clean-architecture（既採用 4 要素プロトコル） | 並列実行可能な観察 | code-review / coding 系（観察的レビュー） |
| 各サブ操作に独立成果物 | spec-items-clarifier / security | 1 つの判断のみ | 単純判定スキル |
| 操作が決定論的 | git-local | 創造的判断中心 | document |

### 段階的導入履歴

- **段階 1（パイロット）**：clean-architecture/SKILL-draft.md に D1 / D6 / D10 を遡及適用（2026-05-04）
- **段階 2（試験適用）**：観察期間後、別スキルへ展開（後続）
- **段階 3（汎用化）**：template.md に手順 T3 + 防御セットを正式組み込み（2026-05-05）。skill-designer 付録 B.7 + §3.1 観点 15 で制度化

### 関連参照

- 操作ルールと適用条件：`${CLAUDE_SKILL_DIR}/SKILL.md` 付録 B.7
- 機構詳細・実装例：`${CLAUDE_SKILL_DIR}/template.md` 末尾「手順 T3 採用時の思考代替リスク防御」
- レビュー観点：`${CLAUDE_SKILL_DIR}/SKILL.md` §3.1 観点 15
