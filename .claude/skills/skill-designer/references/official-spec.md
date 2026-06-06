# Claude Code スキル公式仕様サマリー（即時参照用）

skill-designer スキルから参照される公式仕様の即時参照用サマリー。
一次出典：Claude Code 公式ドキュメント「スキルで Claude を拡張する」（`https://code.claude.com/docs/llms.txt` 配下のスキル章）。

---

## 目次

| 章 | 内容 |
|---|------|
| [A.1](#a1-frontmatter-フィールド一覧公式準拠) | frontmatter フィールド一覧（15 項目） |
| [A.2](#a2-文字列置換公式準拠) | 文字列置換（環境変数・引数） |
| [A.3](#a3-配置先と優先順位公式準拠) | 配置先と優先順位（Enterprise / Personal / Project / Plugin） |
| [A.4](#a4-呼び出し制御マトリクス公式準拠) | 呼び出し制御マトリクス（user-invocable / disable-model-invocation） |
| [A.5](#a5-スキルライフサイクル公式準拠) | スキルライフサイクル（注入・コンパクション） |
| [A.6](#a6-動的コンテキスト注入公式準拠) | 動的コンテキスト注入 |
| [A.7](#a7-拡張思考公式準拠) | 拡張思考（ultrathink） |
| [A.8](#a8-ライブ変更検出と追加ディレクトリ公式準拠) | ライブ変更検出と追加ディレクトリ |
| [A.9](#a9-組み込みコマンドバンドルスキル衝突回避用) | 組み込みコマンド・バンドルスキル（衝突回避用） |

---

## A.1 frontmatter フィールド一覧（公式準拠）

| フィールド | 必須 | 型 | 既定値 |
|-----------|-----|---|--------|
| `name` | いいえ | string（小文字・数字・ハイフン、≤64 文字） | ディレクトリ名 |
| `description` | 推奨 | string | マークダウン最初の段落 |
| `when_to_use` | いいえ | string | なし |
| `argument-hint` | いいえ | string | なし |
| `arguments` | いいえ | string list | なし |
| `disable-model-invocation` | いいえ | boolean | `false` |
| `user-invocable` | いいえ | boolean | `true` |
| `allowed-tools` | いいえ | string list | なし |
| `model` | いいえ | string | セッション継承 |
| `effort` | いいえ | enum (`low` / `medium` / `high` / `xhigh` / `max`) | セッション継承 |
| `context` | いいえ | enum (`fork`) | インライン |
| `agent` | いいえ | string | `general-purpose` |
| `hooks` | いいえ | object | なし |
| `paths` | いいえ | string list | なし |
| `shell` | いいえ | enum (`bash` / `powershell`) | `bash` |

## A.2 文字列置換（公式準拠）

| 変数 | 説明 |
|-----|------|
| `$ARGUMENTS` | スキル呼び出し時の全引数 |
| `$ARGUMENTS[N]` / `$N` | 0 ベースのインデックスで個別引数（シェルスタイルのクォートで複数単語可） |
| `$name` | `arguments` フロントマターで宣言された名前付き引数 |
| `${CLAUDE_SESSION_ID}` | 現在のセッション ID |
| `${CLAUDE_EFFORT}` | 現在の努力レベル |
| `${CLAUDE_SKILL_DIR}` | SKILL.md を含むディレクトリ（バンドルファイル参照に使用する） |

## A.3 配置先と優先順位（公式準拠）

| 場所 | パス | 適用範囲 | 優先度 |
|-----|------|---------|--------|
| Enterprise | 管理設定参照 | 組織全体 | 1（最高） |
| Personal | `~/.claude/skills/<name>/SKILL.md` | 全プロジェクト | 2 |
| Project | `.claude/skills/<name>/SKILL.md` | 当該プロジェクト | 3 |
| Plugin | `<plugin>/skills/<name>/SKILL.md` | プラグイン有効範囲 | 名前空間化（`plugin-name:skill-name`） |

> 同名スキルは Enterprise > Personal > Project の順で上書きされる。プラグインは名前空間化のため衝突しない。

## A.4 呼び出し制御マトリクス（公式準拠）

| frontmatter | ユーザー呼び出し | Claude 呼び出し | コンテキスト読み込み |
|------------|---------------|---------------|------------------|
| (デフォルト) | はい | はい | description 常時、フル呼び出し時に本体読み込み |
| `disable-model-invocation: true` | はい | いいえ | description 含まれず、ユーザー呼び出し時のみフル読み込み |
| `user-invocable: false` | いいえ | はい | description 常時、フル呼び出し時に本体読み込み |

## A.5 スキルライフサイクル（公式準拠）

- スキル呼び出し時に SKILL.md 本体は会話に 1 メッセージとして注入され、セッション残りで持続する
- Claude Code は後続ターンでスキルファイルを再読込みしない
- 自動コンパクション時：呼び出されたスキルを 25,000 トークン予算で再アタッチする。各スキル最大 5,000 トークンまで保持する
- 多数のスキルを呼び出した場合、古いスキルはコンパクション後に完全にドロップされる可能性がある
- 効果が薄れた場合は再呼び出しでフルコンテンツを復元する

## A.6 動的コンテキスト注入（公式準拠）

公式構文（インライン形式・複数行フェンス形式）と
`disableSkillShellExecution: true` の挙動については
`${CLAUDE_SKILL_DIR}/references/dynamic-context-syntax.md` を参照。

## A.7 拡張思考（公式準拠）

スキルコンテンツのどこかに「ultrathink」を含めると拡張思考が有効化される。

## A.8 ライブ変更検出と追加ディレクトリ（公式準拠）

- `~/.claude/skills/`、プロジェクト `.claude/skills/`、`--add-dir` ディレクトリ内の `.claude/skills/` の変更は再起動なしで反映される
- セッション開始時に存在しなかった最上位スキルディレクトリを新規作成した場合は再起動が必要
- ネストされた `.claude/skills/`（例：`packages/frontend/.claude/skills/`）は当該パスのファイル操作時に自動検出される

## A.9 組み込みコマンド・バンドルスキル（衝突回避用）

| 種別 | 名前 |
|-----|------|
| 組み込みコマンド | `/help` `/compact` `/init` `/review` `/security-review` 等 |
| バンドルスキル | `/simplify` `/batch` `/debug` `/loop` `/claude-api` |

> 上記の名前はカスタムスキルの `name` として使用しない（衝突するため）。
> 同名の場合、Skill ツール経由で呼び出されるバンドルスキルが優先される。
