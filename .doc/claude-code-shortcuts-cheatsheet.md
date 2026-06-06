# Claude Code 入力プロンプト ショートカット チートシート

> **対象**: Claude Code CLI のインタラクティブモード（`claude` 起動後の入力プロンプト）
> **一次情報**: [Claude Code Docs — Interactive mode](https://code.claude.com/docs/en/interactive-mode)（公式）
> **検証日**: 2026-05-03
> **凡例**:
> - ✅ 公式ドキュメント記載と一致
> - ⚠️ 公式と画面表示で記述が異なる／補足が必要
> - 🟡 公式ドキュメントに直接記載なし（二次情報のみで確認）

---

## 1. プレフィックス（行頭で入力）

| 表記 | 機能 | 検証 |
|---|---|---|
| `!` | シェルモード。コマンドと出力を会話コンテキストに追加して実行。 | ✅ |
| `/` | コマンド／スキル呼び出し。`/` 単独で全コマンド一覧。 | ✅ |
| `@` | ファイルパス補完。`@src/` のようにディレクトリ指定も可。 | ✅ |
| `&` | （画面表示）バックグラウンド実行のプレフィックス | 🟡 公式 Interactive mode ページには `&` プレフィックスの直接記載なし。バックグラウンド化の正規手段は `Ctrl+B`（実行中タスクをバックグラウンドへ移動）と公式に明記されている。`&` 表記の挙動はバージョン依存の可能性あり。 |

---

## 2. 入力編集

| ショートカット | 機能 | 検証 |
|---|---|---|
| `Shift + ⏎` | 改行（送信せずに次行）。iTerm2 / WezTerm / Ghostty / Kitty / Warp / Apple Terminal はそのまま動作。VS Code / Cursor / Windsurf / Alacritty / Zed は `/terminal-setup` が必要。 | ✅ |
| `Ctrl + G` | 入力を `$EDITOR` で開いて編集。`Ctrl + X Ctrl + E` も同等（readline 標準）。 | ✅ |
| `Ctrl + S` | （画面表示）プロンプトを一時退避（stash） | 🟡 公式 Interactive mode ページでは `Ctrl+S` は reverse search のスコープ切替として記載。「stash prompt」機能は二次情報（debbie.codes 等）で確認できるが、公式の同ページには明記されていない。バージョン差の可能性あり。 |
| `Ctrl + Shift + _` | （画面表示）入力を undo | 🟡 公式ページに直接記載なし。`Ctrl + _` / `Ctrl + Shift + _` は readline ライブラリ標準の undo バインドとして広く知られているが、Claude Code 公式ドキュメントの該当ページには明示記載なし。 |
| `Esc Esc`（ダブルタップ） | （画面表示）入力をクリア／（公式）Rewind or summarize（コードや会話を以前の状態に戻す、または選択メッセージから要約生成） | ⚠️ 画面表示「clear input」と公式記載「Rewind or summarize」が一致しない。空入力時と入力中で挙動が分岐する可能性が高いが、公式ドキュメントは Rewind / summarize のみ記載。 |

---

## 3. モード・モデル切替

| ショートカット | 機能 | 検証 |
|---|---|---|
| `Shift + Tab` | 権限モードを循環（`default` → `acceptEdits` → `plan` → 有効な追加モード `auto` / `bypassPermissions`）。画面表示「auto-accept edits」は `acceptEdits` モードを指す。 | ✅ |
| `Alt + P`（Win/Linux）／`Option + P`（macOS） | 入力中のプロンプトを保持したままモデル切替。 | ✅ |
| `Alt + O`（Win/Linux）／`Option + O`（macOS） | Fast mode のオン／オフ切替。 | ✅ |

> **macOS 注意**: `Option + ◯` 系は端末側で「Option を Meta として送信」を有効にする必要がある（iTerm2: Profiles → Keys → 左/右 Option を `Esc+`／Apple Terminal: Profiles → Keyboard → "Use Option as Meta Key"）。

---

## 4. 表示・出力制御

| ショートカット | 機能 | 検証 |
|---|---|---|
| `Ctrl + O` | （画面表示）verbose output／（公式）transcript viewer のトグル。詳細なツール使用と実行内容を表示。MCP 呼び出しの省略表示（"Called slack 3 times"）も展開。 | ⚠️ 画面表示「verbose output」と公式記載「Toggle transcript viewer」は事実上同一機能を指していると判断できる（詳細出力＝transcript 展開）。 |
| `Ctrl + T` | タスクリスト表示のトグル。最大 5 件をステータス領域に表示。全件確認・クリアは Claude に依頼する（"show me all tasks" 等）。 | ✅ |
| `Ctrl + V` | クリップボード画像の貼り付け（`[Image #N]` チップとして挿入）。iTerm2 では `Cmd + V`、Windows では `Alt + V` も使用可。 | ✅ |

---

## 5. プロセス制御

| ショートカット | 機能 | 検証 |
|---|---|---|
| `Ctrl + Z` | プロセスを suspend（一時停止）。シェルに戻り `fg` で復帰可能。 | 🟡 公式 Interactive mode ページには直接記載なし。POSIX シェルの標準 SIGTSTP 動作に依存（Claude Code を起動しているシェル側の機能）。 |
| `Ctrl + B` | 実行中タスクをバックグラウンドへ移動（tmux 利用時は 2 回押す）。 | ✅ |
| `Ctrl + C` | 入力／生成のキャンセル。 | ✅ |
| `Ctrl + D` | セッション終了（EOF）。 | ✅ |

---

## 6. その他コマンド系

| 表記 | 機能 | 検証 |
|---|---|---|
| `/btw` | 会話履歴を汚さない side question。Claude が処理中でも実行可。コンテキスト全体を読めるが、ツール呼び出し不可、単発応答のみ。 | ✅ |

---

## 7. 公式ドキュメントとの差異まとめ（要点のみ）

実証的に確認した、画面表示と公式ドキュメントの **記述上の不一致** は以下 4 点。

1. **`&` for background**
   公式ページには `&` プレフィックスの記載がない。バックグラウンド化の正規手段は `Ctrl+B` と明記されている。画面表示の `&` は最近のバージョン追加の可能性があるが、公式ドキュメント上では未確認。

2. **`Ctrl + S` for stash prompt**
   Interactive mode ページでは `Ctrl + S` は reverse search のスコープ切替として記載。stash prompt 機能は二次情報のみで確認。公式の Interactive mode ページに反映されていない可能性が高い。

3. **`Ctrl + Shift + _` to undo**
   公式ページには undo ショートカットが明記されていない。readline 標準の undo バインドであり、入力編集レイヤーの挙動として動作している可能性が高いが、公式ドキュメント上では確認不可。

4. **`Esc Esc` の説明**
   画面表示「clear input」と公式記載「Rewind or summarize」が異なる。公式は会話・コード状態の rewind 機能として説明しており、「clear input」とは趣旨が異なる。空入力時と入力中の挙動が分岐していると推定されるが、公式ドキュメントでは明示されていない。

---

## 8. 補足（公式記載の頻出ショートカット、画面非表示）

ユーザー画面に表示されていないが、公式ドキュメントで記載されている主要なものを参考までに記載。

| ショートカット | 機能 |
|---|---|
| `?` | 環境別の利用可能ショートカット一覧を表示 |
| `Ctrl + R` | コマンド履歴の reverse search |
| `Ctrl + L` | 画面再描画（履歴・入力は保持） |
| `Ctrl + X Ctrl + K`（3 秒以内に 2 回） | 全バックグラウンドエージェントの強制終了 |
| `Alt + T` / `Option + T` | Extended thinking のトグル |
| `\` + `Enter` | 改行（端末非依存） |
| `Ctrl + J` | 改行（端末非依存、別経路） |

---

## 出典

- [Claude Code Docs — Interactive mode](https://code.claude.com/docs/en/interactive-mode)（一次資料、Anthropic 公式）
- [Claude Code Docs — Customize keyboard shortcuts](https://code.claude.com/docs/en/keybindings)（公式・キーバインドカスタマイズ）
- 二次情報（stash prompt 機能の確認のみ）: debbie.codes の Claude Code commands 解説記事
