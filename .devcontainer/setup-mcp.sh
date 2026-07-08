#!/bin/bash
# ---------------------------------------------------------------------
# Claude Code セットアップスクリプト
# ---------------------------------------------------------------------
# serena MCP / compose 撤去後は MCP 設定を持たず、Claude Code CLI の
# 導入のみを担う。devcontainer.json の postCreateCommand から実行される。
set -e
echo "--- Start Claude Code Setup ---"

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

echo "--- Claude Code Setup Complete ---"