# 指示ファイル監査（2026-10-31）

対応台帳: `ISSUE.md` の ISSUE-558。提案差分: `docs/prompt-audit-2026-10-31.patch`（未適用）。

## 結論

指示ファイルが、存在しないスキル・ファイル・コマンドを参照している。修正は依頼者の指示で後日行う。

## 用語

- **指示ファイル**: Claude Code のセッションに読み込まれる `.claude/CLAUDE.md`、エージェント定義、スキル。
- **提案差分**: この監査が作った修正案の `git apply` 形式のファイル。作業ツリーには適用していない。
- **事前読み込み**: エージェント定義の `skills:` に書いたスキルを、起動時に本文ごと渡す仕組み。

## 監査の範囲と数え方

| 区分 | 対象 | 読み方 |
|---|---|---|
| 全文 | `.claude/CLAUDE.md`、`.claude/agents/` 12 件 | 全行を読んだ |
| 全文 | code-documentation, code-review, Coding, git, TDD, rapid-prototype, prompt-validation-workflow, upstream-input-validation の `SKILL.md` | 全行を読んだ |
| 検索のみ | clean-architecture, system-basic-design, security, toulmin-argument-toolkit, subagent-creator, skill-designer, spec-items-clarifier の `SKILL.md`、各スキルの references | 語句検索と該当行だけ |
| 検索のみ | `~/.claude/skills/synced/` の 34 スキル、プラグイン `cowork-plugin-management` の 2 スキル | 語句検索と該当行だけ |
| 対象外 | `.claude/worktrees/*/.claude/CLAUDE.md`、自動メモリ、設定ファイル | 読んでいない |

- 確認方法: ファイルの有無は `SKILL.md` の参照を抽出して照合した。履歴は `git log --all` で調べた。
- フックの遮断は `block_destructive_git.py` の正規表現を Python で評価して確認した。フック自体は起動していない。
- 外部仕様は公式文書 `code.claude.com/docs/en/skills` と `code.claude.com/docs/en/sub-agents` で確認した。
- 未実施: エージェントとスキルの実起動、修正前後の挙動比較。

## 発生の実測状況

| 状態 | 項目 |
|---|---|
| **損害を実測** | A-6（upstream-input-validation のテンプレート消失） |
| 記録（発生未実測） | 上記以外のすべて。参照先の不在や矛盾はコードで確認したが、実行時の失敗は再現していない |

A-6 の再現手順（出力の数値は 2026-10-31 時点）:

```bash
grep -c '上流入力なしのケース' .claude/skills/upstream-input-validation/output.md   # 0
git show ec92e4a2:.claude/skills/upstream-input-validation/output.md | grep -c '上流入力なしのケース'   # 1
```

## A. 確度 高（リポジトリまたは公式文書と矛盾）

| # | 場所 | 内容 | 修正案 |
|---|---|---|---|
| A-1 | `agents/programmer-executor.md:23`, `agents/git-executor.md:23` | `skills:` に `git` があるが、`skills/git/SKILL.md:15` は `disable-model-invocation: true`。公式文書ではこの設定のスキルは事前読み込みできない。programmer-executor は §7 で `git` スキルが読めなければ即時中断と定めている | `skills:` から外し、本文で `.claude/skills/git/SKILL.md` を読ませる。スキル側の保護は変えない |
| A-2 | `agents/system-internal-designer-executor.md:21,67` | `system-internal-design` が存在しない | 両行を削除する |
| A-3 | `skills/git/SKILL.md:122,152` | `git restore --staged` を指示しているが、`block_destructive_git.py:40` の正規表現に一致する | `git reset -q HEAD -- <path>` に変える。正規表現に一致しないことは確認済み、実行は未確認。`allowed-tools` に無いので確認が出る場合がある |
| A-4 | `skills/TDD/SKILL.md` の 52, 64, 119, 126, 136, 147, 159, 166, 169 行と付録 A | `references/` の 3 ファイルが無く、git 履歴にも無い | 参照を消し、本文の内容で判定する。原本が手元にあれば復元を優先する |
| A-5 | `skills/Coding/SKILL.md:86-89,132-133` ほか | `output.md` が無いのに読み込みの省略を禁止している。`template.md` と「規約 B.6」も参照している | 引用された節名だけで `Coding/output.md` を新規作成し、86-89 行を削除する |
| A-6 | `skills/upstream-input-validation/SKILL.md:107-109` | `output.md` はテンプレートのはずが実行結果で上書きされている | 「応答で返し、書き換えない」に変え、テンプレートを `ec92e4a2` の版から復元する |
| A-7 | `skills/prompt-validation-workflow/SKILL.md:17` | 同じ上書きが起きている。初版の時点で既に実行結果で、テンプレートは履歴に無い | 同じ文面に変え、テンプレートを新規作成する |
| A-8 | エージェント 5 件の完了条件（architecture:63, code-review:62, coding:56, document:59, system-basic:64）、programmer:121、`git-executor.md:65`、`subagent-creator/template.md:65` | 「スキルが出力する違反リスト」が該当スキルに無い（あるのは `TDD/output.md` のみ）。`git-local` も無い | 「§4 強制ルールに違反がないこと」に変える。雛形も変える |
| A-9 | 13 ファイル（agents 3 件、skills 10 件） | 実在しない `system-design`、`spec-driven-developer`、`git-local`、`spec-driven-development.md` を参照している | 実在する名前に変えるか、メイン会話への差し戻しに変える |
| A-10 | `agents/programmer-executor.md:123,130`, `agents/spec-reviewer-executor.md:161,167`, `skills/subagent-creator/references/official-spec.md:76`, `skills/subagent-creator/SKILL.md:52,62,97` | 「公式仕様：ネスト不可」が公式文書と矛盾する（既定で 3 層まで生成可）。`cd` が保持されない記述は現在も正しい | 理由を「tools に Agent を持たない」に変え、ネスト禁止はプロジェクト方針として残す |
| A-11 | `.claude/CLAUDE.md:125` | 「エラー・不整合時は即時追記（`OPEN`）」が「起票の基準」（:64、2026-09-25）と矛盾する。:125 は 2026-06-06 の記述 | 「基準を満たすものは `OPEN`、満たさないものは記録（発生未実測）」に変える |

## B. 確度 中（モデルの挙動に基づく提案）

モデルの挙動の根拠は監査手順書の記述で、このリポジトリでは検証していない。採否は依頼者が決める。

| # | 場所 | 内容 | 修正案 |
|---|---|---|---|
| B-1 | `skills/prompt-validation-workflow/SKILL.md:5`, `skills/upstream-input-validation/SKILL.md:12` | `claude-opus-4-7` に固定している | `model: opus` に変える。技術スタックの変更なので承認対象 |
| B-2 | `.claude/CLAUDE.md:3,8,10,28,64,80,90,156,159,165,168` | 「絶対」「最優先」が重なっている。:3 と :156 はどちらも最優先を名乗る | 平常の語に変え、見出しの日付を消す。禁止の中身と理由は残す。:152 の参照も直す |
| B-3 | `.claude/CLAUDE.md:120,122,127` | 計画の手順と、各完了後の進捗報告を定めている（初版から） | 3 項目を削除し、番号を振り直す |
| B-4 | `.claude/CLAUDE.md:115,117,195,196,202-206` | 100 字・50 字・200 字の上限 | 字数を消す。「結論から」「要点だけ」「背景・根拠は書かない」は残す |
| B-5 | `.claude/CLAUDE.md:191-193` | :5, :6, :28 と同じ内容の反復 | 削除する |
| B-6 | `skills/prompt-validation-workflow/SKILL.md:89-90,124-136` | ポーカー解析の事例（`CandyPoker` は `ISSUE.md` に 0 件、`our_solver` は `.py` に 0 件） | 削除する。規則本体（117-122 行）は残す |
| B-7 | 9 スキルの §4（TDD:46,72,73,75 など） | 「絶対に禁止」の定型文 | 「禁止する」に変える |

## C. 依頼者の判断が必要（提案差分なし）

| # | 場所 | 決めること |
|---|---|---|
| C-1 | `.claude/CLAUDE.md:33` と :49-53 | :33 は破壊的変更を「承認があっても実施しない」、:53 は「不可逆段階は別ターンで改めて確認する」。不可逆段階を実施できるのか |
| C-2 | `skills/git/SKILL.md:60,86` と `.claude/CLAUDE.md` | スキルは `reset --hard` をバックアップ確認後に許可し、CLAUDE.md は禁止している。スキルをどう揃えるか |
| C-3 | `agents/git-executor.md:4,35` と `skills/git/SKILL.md:83` | エージェントは「リモート連携」を担い、スキルは push/pull/fetch を全面禁止している。どちらを正とするか |
| C-4 | `skills/subagent-creator/SKILL.md:198`, `template.md:156`, `references/project-conventions.md:15` | `spec-driven-development.md` に依存する B.2 節を残すか |
| C-5 | 全スキル共通の `step S-N` 手順 | 判断作業には細かすぎる形か（確度 低）。書き直すには評価の仕組みが要る |
| C-6 | skill-designer, subagent-creator, spec-items-clarifier, toulmin-argument-toolkit | プロジェクト版とユーザー版で内容が違う。どちらを正とするか |

## D. 提案差分に含まれない修正

**プロジェクト内**

- `skills/upstream-input-validation/output.md`: `ec92e4a2` の版へ戻す。ファイル全体の置き換えなのでパッチに含めていない。
- `skills/prompt-validation-workflow/output.md`: テンプレートを新規作成する。節の案: 想定失敗の推定 / 証拠 / 判定 / 反映 / 残存リスク。

**`~/.claude/skills/synced/`（全プロジェクトに影響）**

同期で上書きされる可能性がある（未検証）。

| 場所 | 変更前 | 変更後 |
|---|---|---|
| `code-cost-manager/SKILL.md:158,208` | `/config` で思考無効化 | `/config` で思考無効化（Opus 5.5 / Fable 5.1 では無効化できないので `/effort` を下げる） |
| `subagent-creator/SKILL.md:80,425` | サブエージェントは他のサブエージェントを生成できない | 既定で 3 層まで生成できる。禁止するなら `tools` から `Agent` を除く |
| `subagent-creator/SKILL.md:144,249` | ネスト不可を公式制約とする観点 | プロジェクト版と同様に、方針として扱う |

## 対応の段階

提案差分は A と B を 1 つにまとめている（C と D は含まない）。必要な部分だけ取り込めるよう、段階を分ける。

| 段階 | 内容 | 可逆性 | 通過条件 |
|---|---|---|---|
| 1 | A-6, A-7 と D のプロジェクト内 2 件 | コミットで戻せる | `grep -c '上流入力なしのケース'` が 1 以上。実行後も `output.md` が変更されない |
| 2 | A-1〜A-5, A-8〜A-11 | コミットで戻せる | 実在しない名前の検索が 0 件。git-executor と programmer-executor を実起動して git スキルを読める |
| 3 | B（採否を決めた分だけ） | コミットで戻せる | 同じ作業を修正前後で実行して比較する |
| 4 | C の判断結果と D の `~/.claude` 分 | コミットで戻せない（`~/.claude` は全プロジェクトに影響） | 依頼者が個別に承認する |

適用前の確認:

```bash
git apply --check docs/prompt-audit-2026-10-31.patch
```

パッチは監査時点のファイルに対して作った。適用に失敗したら、対象ファイルが変わっているので本書の修正案から作り直す。行番号も監査時点の値である。
