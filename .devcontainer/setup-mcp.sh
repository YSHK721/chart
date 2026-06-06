#!/bin/bash
# ---------------------------------------------------------------------
# MCP 環境セットアップスクリプト
# ---------------------------------------------------------------------
set -e
echo "--- Start MCP Environment Setup ---"

# 1. npm prefix を固定して PATH に追加
npm config set prefix '/usr/local'
export PATH="/usr/local/bin:$PATH"

# 2. Claude Code のインストール
if ! command -v claude &> /dev/null; then
    echo "Installing Claude Code..."
    npm install -g @anthropic-ai/claude-code
else
    echo "Claude Code is already installed."
fi

# 3. MCP 設定ファイルの準備
CONFIG_FILE="$HOME/.claude.json"
if [ ! -f "$CONFIG_FILE" ]; then
    echo "{}" > "$CONFIG_FILE"
fi

# 4. Serena MCP サーバーの登録（HTTP transport）
# Docker Compose network 内で serena サービスに直接接続するため
# ホストパス・docker run・cross-device mv がすべて不要になる
PROJECT_DIR=$(pwd)
echo "Configuring Serena MCP server... (project: $PROJECT_DIR)"

: "${SERENA_MCP_PORT:=9121}"
jq --arg port "$SERENA_MCP_PORT" '
.mcpServers.serena = {
    "type": "http",
    "url": ("http://serena:" + $port + "/mcp")
}' "$CONFIG_FILE" > /tmp/claude_tmp.json && mv /tmp/claude_tmp.json "$CONFIG_FILE"

echo "Serena configured."
echo "--- MCP Environment Setup Complete ---"