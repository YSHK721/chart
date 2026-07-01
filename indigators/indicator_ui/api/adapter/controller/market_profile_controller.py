"""MarketProfileController — GET /market_profile の純ロジック（HTTP 殻非依存）。

``handle_market_profile(ref, timeframe, limit, bins, va) -> (HTTPステータス, ボディ)`` は
HTTP サーバ本体（BaseHTTPRequestHandler・ソケット）に依存しない純関数である。HTTP 殻
（``api/framework/server.py``）は本関数を呼ぶ薄い分岐として配線する（``handle_compute`` と同型）。

処理:
  1. datasetRef をホワイトリスト解決する（未知キー・パス文字列は 400 で拒否＝§7.3 パストラバーサル対策）。
  2. timeframe を検証する（None は原子＝再集計なし・後方互換。未知コードは 400）。
  3. ``dataset.load_candles(ref, tf, limit)`` で OHLC candles（``[{time,open,high,low,close}]``）を取得する
     ── この形はそのまま ``compute_candle_profile`` の入力形（time/open/high/low/close の辞書リスト）。
  4. ``bins``（int・既定 60・[1, _MAX_BINS] にクランプ）/ ``va``（float・既定 0.70・有限かつ
     [_MIN_VA, 1.0] にクランプ）を反映して足ベース TPO プロファイルを計算する。
  5. 成功は (200, {ok, profile})。ref/timeframe の検証失敗は §6.3.4 nested error（error.type→
     HTTPステータスは adapter.compute.ERROR_STATUS・単一定義）で 400 に翻訳する。
     bins/va は例外化せずクランプで吸収する（500 化しない）。data load / 計算の想定外失敗のみ
     HTTP 殻の包括 try/except で internal 500 になる。

依存方向: framework → adapter（本 controller）→ adapter.compute（dataset / market_profile）。
既存 dataset / market_profile 計算コアは read-only（改変しない）。src は candle（足ベース TPO）のみ。
"""

from __future__ import annotations

import math
from typing import Any

from adapter.compute import ERROR_STATUS, dataset
from adapter.compute.market_profile import compute_candle_profile

# パラメータ既定値（GET /market_profile のクエリ省略時）。
_DEFAULT_BINS = 60
_DEFAULT_VA = 0.70
# 入力境界（単一スレッド常駐サーバの占有防止・退化した VA の防止）。
#   bins は [1, _MAX_BINS] にクランプ（0 での ValueError→500 と巨大値での占有を封じる）。
#   va は有限かつ [0.01, 1.0] にクランプ（負/NaN/Inf/>1 での無言退化を防ぐ）。
_MAX_BINS = 1000
_MIN_VA = 0.01


def _error_body(error_type: str, message: str) -> tuple[int, dict[str, Any]]:
    """§6.3.4 nested error（{ok:false, generation, error:{type, message, violations}}）。

    error_type→HTTPステータスは adapter.compute.ERROR_STATUS（単一定義）を参照する
    （handle_compute の _error_body と同一のステータス翻訳・応答規約）。
    """
    status = ERROR_STATUS.get(error_type, 500)
    return status, {
        "ok": False,
        "generation": 0,
        "error": {"type": error_type, "message": message, "violations": []},
    }


def _parse_int(raw: Any, default: int | None) -> int | None:
    """クエリ由来の値（str|None）を非負 int へ変換する（不正・None は default）。"""
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return default


def _parse_float(raw: Any, default: float) -> float:
    """クエリ由来の値（str|None）を float へ変換する（不正・None は default）。"""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return default
    return default


def handle_market_profile(
    ref: Any,
    timeframe: Any = None,
    limit: Any = None,
    bins: Any = None,
    va: Any = None,
) -> tuple[int, dict[str, Any]]:
    """GET /market_profile の純ロジック。

    Args:
        ref: datasetRef（ホワイトリスト済みキー）。未知は 400。
        timeframe: 時間足コード（None=原子・再集計なし）。未知は 400。
        limit: 直近 N 本に制限（クエリ str|int|None。不正・None は全件）。
        bins: 価格ビン分割数（クエリ str|int|None。不正・None は 60）。
        va: バリューエリア比率 0..1（クエリ str|float|None。不正・None は 0.70）。

    Returns:
        (HTTPステータス, ボディ)。成功は (200, {ok:true, profile:{...}})、
        失敗は (400/5xx, {ok:false, generation, error:{...}})。
    """
    # datasetRef ホワイトリスト解決（§7.3）。未知キー・パス文字列は拒否。
    if not dataset.is_known(ref):
        return _error_body("validation", f"未知の datasetRef です: {ref!r}")

    # timeframe — None は原子（後方互換）。未知コードは拒否（§7.3 同様）。
    if timeframe is not None and not dataset.is_known_timeframe(timeframe):
        return _error_body("validation", f"未知の timeframe です: {timeframe!r}")

    # bins/va は範囲クランプ（不正値でも 500/退化させず、安全な範囲へ丸める）。
    n_bins = max(1, min(int(_parse_int(bins, _DEFAULT_BINS)), _MAX_BINS))
    va_pct = _parse_float(va, _DEFAULT_VA)
    if not math.isfinite(va_pct):
        va_pct = _DEFAULT_VA
    va_pct = min(1.0, max(_MIN_VA, va_pct))
    limit_n = _parse_int(limit, None)

    # candles は load_candles が返す [{time,open,high,low,close}] がそのまま compute の入力形。
    candles = dataset.load_candles(ref, timeframe, limit_n)
    profile = compute_candle_profile(candles, n_bins=n_bins, va_pct=va_pct)
    return 200, {"ok": True, "profile": profile}
