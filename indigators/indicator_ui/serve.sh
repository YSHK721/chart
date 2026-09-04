#!/usr/bin/env bash
# indicator UI（B方式・ライブ計算サーバ）をサクッと起動する。
#   使い方:  ./serve.sh [PORT] [--no-update]   （既定ポート 8000・停止は Ctrl-C）
#   例:      ./serve.sh 9000
#            ./serve.sh --no-update          # データ更新をスキップして即起動
# 起動前にチャート用データ（足/ロールアップ/日足）を増分取得し、さらに毎分 1 分足を追記する
# バックエンド watch を併走させて、チャートの足がライブで伸び続けるようにする。
# どのディレクトリから実行してもよい（パスはスクリプト位置基準で解決する）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# 実行時の import パスは **本スクリプトの位置** から解決する（ISSUE-279）。venv の .pth は
# インストール時のチェックアウトを絶対パスで指すため、worktree 起動でも main の実装が読まれる。
. "${REPO_ROOT}/tools/dev_paths.sh"
# venv の場所（ISSUE-365）。既定はツリー相対だが、dev_paths.local.sh が VENV_PYTHON を
#   export していればそれを使う。worktree は git 追跡ファイルしか展開されないため venv の実体を
#   持たず、従来はツリー内へ symlink を張る必要があった。その symlink をコミットして本
#   チェックアウトを壊したのが ISSUE-363 で、環境変数で絶対パスを指せば張る理由自体が消える。
VENV_PY="${VENV_PYTHON:-${SCRIPT_DIR}/../../lightweight-charts-python-main/.venv/bin/python}"
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
M1_TOOL="$REPO_ROOT/indigators/indicator_ui/tools/export_jp225_m1.py"
TICK_WATCH_TOOL="$REPO_ROOT/tools/live_tick_watch.py"
WATCH_LOG="$REPO_ROOT/data/marketdata/live_watch.log"
TICK_WATCH_LOG="$REPO_ROOT/data/marketdata/live_tick_watch.log"
MT5_WATCH_TOOL="$REPO_ROOT/tools/mt5_tick_watch.py"
MT5_WATCH_LOG="$REPO_ROOT/data/marketdata/mt5_tick_watch.log"
WATCH_PID=""
TICK_WATCH_PID=""
MT5_WATCH_PID=""
if [ "$NO_UPDATE" -eq 0 ]; then
  echo "▶ 最新データを増分取得中（足/ロールアップ/日足）..."
  if ! "$VENV_PY" "$REPO_ROOT/tools/acquire_marketdata.py" --skip ticks --skip ingest; then
    echo "warn: データ更新に失敗しました。既存データで起動を続行します（--no-update で更新省略可）。" >&2
  fi
  # ライブ更新の実体: 1 分足を毎分取得し jp225_m1.csv + rollups へ追記し続けるバックエンド
  # watch（これが無いとデータが伸びず、フロント LiveUpdater がポーリングしても足が増えない）。
  echo "▶ ライブ更新を開始（毎分 1 分足を追記・ログ: $WATCH_LOG）"
  "$VENV_PY" "$M1_TOOL" --watch --interval 60 >"$WATCH_LOG" 2>&1 &
  WATCH_PID=$!
  # チャート表示データセット jp225_tick（tick 由来）のライブ供給: 毎分 当日 tick を全量再取得し
  # jp225_tick_m1.csv + rollups/jp225_tick へ増分更新し続ける（これが無いと tick 系が凍結し
  # /forming_bar が当日 parquet 不在で null・/candles も新規足なしとなり価格が更新されない）。
  echo "▶ tick ライブ更新を開始（毎分 当日tick全量再取得・ログ: $TICK_WATCH_LOG）"
  "$VENV_PY" "$TICK_WATCH_TOOL" --stream >"$TICK_WATCH_LOG" 2>&1 &
  TICK_WATCH_PID=$!
  # MT5 増分ティックの供給（ISSUE-447 段階 1・承認事項 A-4）。VM 側 feed から
  #   「最後に保存したティック以降」を引き続け、jp225_mt5 系列（M1 + rollups）へ供給する。
  #   秘密（MT5_BRIDGE_SECRET）が無い環境では供給そのものが成立しないため起動しない
  #   ＝橋渡しを設定していない環境の既定の起動は 1 バイトも変わらない。
  #   初回（ジャーナル不在）は再開点を推測しないため MT5_TICK_WATCH_FROM で開始点を渡す
  #   （端末の壁時計・例 "2026-09-01 12:00:00"）。2 回目以降はジャーナルから再開する。
  if [ -n "${MT5_BRIDGE_SECRET:-}" ]; then
    echo "▶ MT5 ティック供給を開始（増分・ログ: $MT5_WATCH_LOG）"
    if [ -n "${MT5_TICK_WATCH_FROM:-}" ]; then
      "$VENV_PY" "$MT5_WATCH_TOOL" --from "$MT5_TICK_WATCH_FROM" >"$MT5_WATCH_LOG" 2>&1 &
    else
      "$VENV_PY" "$MT5_WATCH_TOOL" >"$MT5_WATCH_LOG" 2>&1 &
    fi
    MT5_WATCH_PID=$!
  fi
fi

# サーバ停止時（Ctrl-C 等）にバックグラウンド watch も確実に止める。
cleanup() {
  [ -n "$WATCH_PID" ] && kill "$WATCH_PID" 2>/dev/null || true
  [ -n "$TICK_WATCH_PID" ] && kill "$TICK_WATCH_PID" 2>/dev/null || true
  [ -n "$MT5_WATCH_PID" ] && kill "$MT5_WATCH_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "indicator UI（B方式）を起動します: $URL"
echo "  停止: Ctrl-C"
# exec しない（trap を生かしてサーバ終了時に watch を停止するため）。
cd "$API_DIR"
"$VENV_PY" -m framework.server "$PORT"
