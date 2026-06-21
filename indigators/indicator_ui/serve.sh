#!/usr/bin/env bash
# indicator UI（B方式・ライブ計算サーバ）をサクッと起動する。
#   使い方:  ./serve.sh [PORT]     （既定ポート 8000・停止は Ctrl-C）
#   例:      ./serve.sh 9000
# どのディレクトリから実行してもよい（パスはスクリプト位置基準で解決する）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="${SCRIPT_DIR}/../../lightweight-charts-python-main/.venv/bin/python"
API_DIR="${SCRIPT_DIR}/api"
PORT="${1:-8000}"
URL="http://127.0.0.1:${PORT}/"

# venv（pandas 必須）の存在確認。
if [ ! -x "$VENV_PY" ]; then
  echo "エラー: venv python が見つかりません: $VENV_PY" >&2
  echo "  pandas を含む venv が必要です（README の B方式 を参照）。" >&2
  exit 1
fi

# 既に起動済みなら二重起動しない。
if command -v curl >/dev/null 2>&1 && curl -sf -o /dev/null "$URL" 2>/dev/null; then
  echo "既に起動済みです: $URL"
  exit 0
fi

echo "indicator UI（B方式）を起動します: $URL"
echo "  停止: Ctrl-C"
cd "$API_DIR"
exec "$VENV_PY" -m framework.server "$PORT"
