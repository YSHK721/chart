# PokerSolverGTO

GTO プリフロップ・レンジデータ取得 CLI ツール。本リポジトリは Dev Container と Serena MCP サーバを利用した開発環境を提供する。

---

## Serena MCP の初期設定

[Serena](https://github.com/oraios/serena) は、LLM クライアント (Claude Code 等) からセマンティックなコード操作を可能にする MCP (Model Context Protocol) サーバである。本リポジトリでは Docker Compose で Serena コンテナを起動し、Pyright LSP を介して Python コードへのシンボル単位アクセスを提供する。

### 前提条件

- Docker Engine / Docker Compose v2 が利用可能なホスト
- 利用可能ポート: `9121` (MCP HTTP)、`24282` (Serena ダッシュボード)
- VS Code + Dev Containers 拡張 (推奨)

### アーキテクチャ

```
┌─────────────────────────────────────────────────────────┐
│ ホスト                                                                                                           │
│   PokerSolverGTO/  ← リポジトリルート                                                                           │
│         │                                                                                                       │
│         │ bind mount (.:)                                                                                       │
│         ▼                                                                                                       │
│   ┌──────────────────────┐         ┌──────────────────────┐      │
│   │ app コンテナ                               │         │ serena コンテナ                            │        │
│   │  /workspaces/                              │         │  /workspaces/                              │        │
│   │   PokerSolverGTO                           │         │   PokerSolverGTO                           │        │
│   │  (Python 開発)                             │         │  ・MCP HTTP :9121                          │        │
│   │                                            │         │  ・Dashboard :24282                        │        │
│   │  Claude Code が接続                        │───▶ │  ・Pyright LSP                             │                                
     └──────────────────────┘         └──────────────────────┘      │
└─────────────────────────────────────────────────────────┘
```

両コンテナは同一の bind mount パス (`/workspaces/PokerSolverGTO`) を共有し、Devcontainer 標準と整合している。

### 関連ファイル

| パス | 役割 |
|---|---|
| `docker-compose.yml` | `app` / `serena` の 2 サービスを定義 |
| `.devcontainer/devcontainer.json` | Dev Container (`app`) 起動設定 |
| `.serena/project.yml` | Serena プロジェクト設定 (LSP 言語、ツール除外等) |
| `.serena/project.local.yml` | ローカル上書き (Git 管理外) |
| `.serena/memories/` | Serena が永続化するメモリ |

### 設定の要点

#### 1. `docker-compose.yml` (serena サービス)

```yaml
serena:
  image: ghcr.io/oraios/serena:latest
  ports:
    - "9121:9121"    # MCP HTTP エンドポイント
    - "24282:24282"  # Serena ダッシュボード
  volumes:
    - .:/workspaces/PokerSolverGTO
  command: [
    "serena", "start-mcp-server",
    "--transport", "streamable-http",
    "--port", "9121",
    "--host", "0.0.0.0",
    "--project", "/workspaces/PokerSolverGTO"
  ]
```

| 引数 | 役割 |
|---|---|
| `--transport streamable-http` | コンテナ外の MCP クライアントから HTTP で接続可能にする |
| `--host 0.0.0.0` | 全インターフェースでリッスン (他コンテナ・IDE からアクセス可) |
| `--port 9121` | MCP HTTP リッスンポート |
| `--project <path>` | 起動時にアクティベートするプロジェクトのパス |

#### 2. `.serena/project.yml` の必須項目

```yaml
project_name: "PokerSolverGTO"

languages:
  - python      # 末尾に半角空白を含めない (LSP 起動失敗の原因になる)

encoding: "utf-8"
ignore_all_files_in_gitignore: true
```

**重要**: `languages` の値は `Language` enum と完全一致する必要がある。末尾空白や大文字小文字違いがあると Serena は黙って空言語リストで起動し、シンボル系・編集系ツールが全て失敗する。

### 起動手順

#### 初回起動

```bash
# ホスト側、リポジトリルートで実行
docker compose up -d
```

これで `app` と `serena` の両コンテナが起動する。

#### Dev Container を利用する場合

VS Code で「Dev Containers: Reopen in Container」を実行すると `app` コンテナに接続される。`serena` コンテナは `docker-compose.yml` 経由で並行起動する。

### 動作確認

#### Serena コンテナの状態

```bash
docker compose ps
docker compose logs serena | tail -20
```

#### LSP プロセスの起動確認

```bash
docker compose exec serena ps aux | grep -E "pyright|pylsp"
```

Pyright が起動していれば 2〜3 行のプロセスが表示される。

#### MCP エンドポイント疎通

```bash
curl -s -I http://localhost:9121/mcp
curl -s http://localhost:24282/dashboard/index.html | head -3
```

#### Claude Code から接続

Claude Code 等 MCP クライアントの設定に以下を追加 (例)。

```json
{
  "mcpServers": {
    "serena": {
      "type": "http",
      "url": "http://localhost:9121/mcp"
    }
  }
}
```

接続後、`mcp__serena__get_current_config` 等のツールが利用可能になる。

### トラブルシューティング

#### 症状: シンボル系ツールが `No language servers available in the manager` で失敗する

原因: LSP (Pyright) が起動していない。多くは `.serena/project.yml` の `languages` 設定ミス。

確認:

```bash
docker compose exec serena ps aux | grep pyright   # 何も出なければ LSP 未起動
docker compose exec serena cat -A .serena/project.yml | grep -A1 "^languages"
```

`  - python$` ではなく `  - python $` のように末尾空白があると enum 一致しない。

修正後、**コンテナ再起動が必要**:

```bash
docker compose restart serena
```

#### 症状: `docker-compose.yml` の変更 (マウント・command) が反映されない

`docker compose restart` は既存コンテナを再起動するだけで compose ファイルは再読込しない。**完全再作成** が必要:

```bash
docker compose up -d --force-recreate --no-deps serena
```

または:

```bash
docker compose down serena && docker compose up -d serena
```

#### 症状: `activate_project` 後も `Active languages: []` のまま

Serena MCP サーバはプロセス起動時にのみ project.yml の `languages` を読み込み、`activate_project` の再呼び出しでは LSP を再初期化しない。`languages` を変更したら必ずコンテナを再起動する。

### 参考

- Serena 公式: <https://github.com/oraios/serena>
- 言語サーバ一覧: <https://oraios.github.io/serena/01-about/020_programming-languages.html>
- 全 28 ツールの動作確認記録: [`.doc/serena-mcp-tool-verification-2026-04-27.md`](.doc/serena-mcp-tool-verification-2026-04-27.md)
