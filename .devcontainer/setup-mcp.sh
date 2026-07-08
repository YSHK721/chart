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

echo "--- MCP Environment Setup Complete ---"