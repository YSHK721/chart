#!/usr/bin/env bash
# report_ui をサクッと起動する開発用ランチャ。
#   report.json を実 run で再生成 → no-cache 静的サーバを起動。
#
# 使い方:
#   simulator/report_ui/run.sh            # 既定ポート 8770 で再生成＋起動
#   simulator/report_ui/run.sh 9000       # ポート指定
#   simulator/report_ui/run.sh --serve-only   # 再生成せず起動のみ（report.json 既存を使う）
#   simulator/report_ui/run.sh 9000 --serve-only
#
# 既存データは read-only。出力は web/data/report.json のみ（gitignore 済）。
set -euo pipefail

ROOT="/workspaces/app"
PY="$ROOT/lightweight-charts-python-main/.venv/bin/python"
HERE="$ROOT/simulator/report_ui"

PORT=8770
SERVE_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --serve-only) SERVE_ONLY=1 ;;
    ''|*[!0-9]*) echo "warn: 不明な引数 '$arg' は無視" >&2 ;;
    *) PORT="$arg" ;;
  esac
done

if [[ ! -x "$PY" ]]; then
  echo "error: venv python が見つかりません: $PY" >&2
  exit 1
fi

if [[ "$SERVE_ONLY" -eq 0 ]]; then
  echo "▶ report.json を再生成中（実 run）..."
  PYTHONPATH="$ROOT" "$PY" "$HERE/tools/export_report_payload.py"
else
  if [[ ! -f "$HERE/web/data/report.json" ]]; then
    echo "error: --serve-only ですが report.json がありません。--serve-only を外して再生成してください。" >&2
    exit 1
  fi
  echo "▶ 再生成スキップ（既存 report.json を使用）"
fi

echo "▶ サーバ起動: http://localhost:$PORT/index.html  (Ctrl-C で停止)"
exec env PYTHONPATH="$ROOT" "$PY" "$HERE/serve.py" "$PORT"
