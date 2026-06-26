#!/usr/bin/env bash
# indicator UI（B方式・ライブ計算サーバ）をサクッと起動する。
#   使い方:  ./serve.sh [PORT] [--no-update]   （既定ポート 8000・停止は Ctrl-C）
#   例:      ./serve.sh 9000
#            ./serve.sh --no-update          # データ更新をスキップして即起動
# 起動前にチャート用データ（足/ロールアップ/日足）を増分取得し、最新データで表示する。
# どのディレクトリから実行してもよい（パスはスクリプト位置基準で解決する）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VENV_PY="${SCRIPT_DIR}/../../lightweight-charts-python-main/.venv/bin/python"
API_DIR="${SCRIPT_DIR}/api"

PORT=8000
NO_UPDATE=0
for arg in "$@"; do
  case "$arg" in
    --no-update) NO_UPDATE=1 ;;
    ''|*[!0-9]*) echo "warn: 不明な引数 '$arg' は無視" >&2 ;;
    *) PORT="$arg" ;;
  esac
done
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

# 最新データを増分取得（チャート用＝足/ロールアップ/日足のみ。ティック/ingest は重く
# チャート非表示のためスキップ）。取得失敗時も既存データで起動を続行する（--no-update で省略）。
if [ "$NO_UPDATE" -eq 0 ]; then
  echo "▶ 最新データを増分取得中（足/ロールアップ/日足）..."
  if ! PYTHONPATH="$REPO_ROOT" python3 "$REPO_ROOT/tools/acquire_marketdata.py" --skip ticks --skip ingest; then
    echo "warn: データ更新に失敗しました。既存データで起動を続行します（--no-update で更新省略可）。" >&2
  fi
fi

echo "indicator UI（B方式）を起動します: $URL"
echo "  停止: Ctrl-C"
cd "$API_DIR"
exec "$VENV_PY" -m framework.server "$PORT"
