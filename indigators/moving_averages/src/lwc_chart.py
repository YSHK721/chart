"""出力アダプタ — lightweight-charts へ単一の移動平均（＋平滑化＋ボリンジャーバンド）を追加する。

層名/責務:
    出力アダプタ。``chart.create_line(name=...)`` を持つオブジェクト（実描画 / API 経路の
    ``FakeLineChart``）へ、TradingView「移動平均」設定に準じた系列を追加する。主たる MA の計算は
    純粋ライブラリ :mod:`core`（MQL 移植・numpy のみ）へ委譲し、平滑化・BB・オフセット・ソース合成・
    確定待ちといった表示由来の派生処理を本層（pandas 利用可）で担う。

系列名（固定・F3 照合は catalog の静的 SeriesDef 集合と突合）:
    - "MA"        : 主たる移動平均（常に出力）。
    - "Smoothing" : 平滑化タイプ != none のとき、主 MA をさらに平滑化した線。
    - "Upper" / "Lower" : 平滑化タイプ == sma_bb のとき、平滑化(SMA)±stddev のバンド。
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

import numpy as np
import pandas as pd

# 適用価格（合成価格）は共有プリミティブ層に一本化する（自前実装を持たない）。
#   ロード境界（adapter/compute/call_binding.py）がワークスペース根を sys.path に追加するため
#   絶対 import で解決できる。UI のソース値 → AppliedPrice 種別の写像のみ本ファイルで持つ。
from common.applied_price import SOURCE_TO_APPLIED, resolve_source_prices
from common_view.lwc_adapter import SeriesLike  # noqa: E402

# 時刻解決の規則は共有アダプタが単一所有する（ISSUE-502 段階 2 D-7）。旧・自前実装は list を
#   返す第 3 変種だったが、唯一の利用点 ``_emit`` は ``times[j]`` の位置参照しかしないため、
#   index を 0..n-1 に振り直した Series でも取り出される値（Timestamp）は同一である。
from common_view.lwc_adapter import resolve_times as _resolve_times  # noqa: E402

from .core import MA_FROM_ZERO, _MA_ON_BUFFER

# UI ソース値（catalog の source enum） → 共有 AppliedPrice 種別。
#   合成価格は applied_price() に委譲（hl2=MEDIAN / hlc3=TYPICAL / hlcc4=WEIGHTED /
#   ohlc4=OHLC4・MQL 外拡張）。写像の実体は共有プリミティブへ 1 本化した（ISSUE-179 項目 4）。
_SOURCE_TO_APPLIED = SOURCE_TO_APPLIED

# 主 MA 種別（小文字） → core バッファ関数（``(rates_total, prev, begin, period, price, buffer)``）。
#   実体は core の ``_MA_ON_BUFFER``（``MA_TYPES`` の導出元）と同一オブジェクト＝単一情報源。
_MA_FUNCS = _MA_ON_BUFFER

# core が「最初の有効値」を index=0 から定義する種別（warm-up マスク不要）。他は period-1 までマスク。
#   実体は core の ``MA_FROM_ZERO``（MA 種別の規約は core が所有する）。
_FROM_ZERO = MA_FROM_ZERO

# 系列の描画色。
_COLOR_MA = "rgba(41, 98, 255, 1)"        # 青（主 MA）
_COLOR_SMOOTH = "rgba(255, 152, 0, 1)"    # 橙（平滑化）
_COLOR_BAND = "rgba(120, 123, 134, 0.9)"  # 灰（BB 上下）

# 既定値（catalog 既定と一致させること）。
_DEFAULTS = {
    "ma_type": "ema",
    "length": 9,
    "source": "close",
    "offset": 0,
    "smoothing_type": "none",
    "smoothing_length": 9,
    "bb_stddev": 2.0,
}


_Line = SeriesLike  # 共有 Protocol の別名（要求は ``set`` のみ・構造的部分型）


@runtime_checkable
class _Chart(Protocol):
    def create_line(self, name: str, **kwargs: object) -> _Line: ...


def _source_prices(df: pd.DataFrame, source: str) -> np.ndarray:
    """ソース価格列（合成含む）を float 配列で返す。計算は共有 ``applied_price`` に委譲する。

    UI のソース値（close/open/high/low/hl2/hlc3/ohlc4/hlcc4）を ``AppliedPrice`` 種別へ写像し、
    ``applied_price(kind, open, high, low, close)`` で系列を得る（合成価格の単一定義）。
    解決**手続き**（列名の小文字照合・欠落時の例外・抽出順）は共有
    ``common.applied_price.resolve_source_prices`` へ 1 本化した（ISSUE-502 段階 2 D-6）。
    """
    return resolve_source_prices(df, source)


def _main_ma(price: np.ndarray, ma_type: str, length: int) -> np.ndarray:
    """主たる MA を core バッファ関数で計算し、warm-up を NaN マスクして返す。"""
    fn = _MA_FUNCS[ma_type]
    n = int(len(price))
    buffer = np.zeros(n, dtype=np.float64)
    fn(n, 0, 0, length, price, buffer)
    valid_from = 0 if ma_type in _FROM_ZERO else length - 1
    if valid_from > 0:
        buffer[:valid_from] = np.nan
    return buffer


def _smooth(ma: pd.Series, smoothing_type: str, period: int) -> pd.Series:
    """主 MA を指定タイプで平滑化する（表示由来・pandas）。sma_bb は基準線=SMA。"""
    st = "sma" if smoothing_type == "sma_bb" else smoothing_type
    if st == "sma":
        return ma.rolling(period, min_periods=period).mean()
    if st == "ema":
        return ma.ewm(span=period, adjust=False, min_periods=period).mean()
    if st == "smma":
        return ma.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    if st == "wma":
        weights = np.arange(1, period + 1, dtype=np.float64)
        return ma.rolling(period, min_periods=period).apply(
            lambda w: float(np.dot(w, weights) / weights.sum()), raw=True
        )
    return ma.rolling(period, min_periods=period).mean()


def _emit(chart, name, times, values, offset, color, *, style="solid", width=1):
    """1 系列を chart へ追加する。``values[i]`` を ``times[i+offset]`` に配置（オフセットシフト）。NaN は除外。"""
    n = len(values)
    out_time, out_val = [], []
    for i in range(n):
        j = i + offset
        if 0 <= j < n:
            v = float(values[i])
            if v == v:  # NaN 除外（NaN != NaN）
                out_time.append(times[j])
                out_val.append(v)
    line = chart.create_line(name=name, color=color, style=style, width=width)
    line.set(pd.DataFrame({"time": out_time, name: out_val}))
    return line


def add_moving_averages(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    ma_type: str = "ema",
    length: int = 9,
    source: str = "close",
    offset: int = 0,
    smoothing_type: str = "none",
    smoothing_length: int = 9,
    bb_stddev: float = 2.0,
    time_column: Optional[str] = None,
) -> dict[str, object]:
    """``chart`` へ単一の移動平均（＋平滑化＋BB）を追加する（TradingView「移動平均」準拠）。

    Args:
        chart: ``create_line(name=...)`` を持つオブジェクト。
        df: OHLC DataFrame（時刻は index / time / date で解決）。
        ma_type: 主 MA 種別（sma / ema / smma / lwma）。
        length: 主 MA の期間（>=2・本数超は空）。
        source: ソース価格（close/open/high/low/hl2/hlc3/ohlc4/hlcc4）。
        offset: 系列を右(正)/左(負)へシフトする本数。
        smoothing_type: 平滑化タイプ（none / sma / ema / smma / wma / sma_bb）。
        smoothing_length: 平滑化の期間。
        bb_stddev: BB の標準偏差倍率（smoothing_type==sma_bb のときのみ使用）。
        time_column: 時刻列の明示指定。

    Returns:
        ``{series_name: Line}``（"MA" / "Smoothing" / "Upper" / "Lower" の出力分）。

    Raises:
        ValueError: source 不正・length 不正時（adapter が validation へ翻訳）。
    """
    ma_type = str(ma_type).lower()
    smoothing_type = str(smoothing_type).lower()
    if ma_type not in _MA_FUNCS:
        raise ValueError(f"未知の MA 種別です: {ma_type}")
    length = int(round(float(length)))
    smoothing_length = max(2, int(round(float(smoothing_length))))
    offset = int(round(float(offset)))

    times = _resolve_times(df, time_column)
    price = _source_prices(df, source)
    # 最終足（形成中バー）も計算対象に含める。かつて `wait_for_close` で除外を選べたが、
    #   同一時間足では `offset=1`（1 本シフト）と同義で概念が重複し、上位足計算は投影側が
    #   期間で使い分けるようになったため選択自体が不要になった（ISSUE-286）。
    n = int(len(price))
    if length < 2 or length > n:
        return {}  # 計算不能（期間 < 2 / 本数超）。系列を出さない。

    lines: dict[str, object] = {}

    # 主 MA。
    ma_vals = _main_ma(price, ma_type, length)
    lines["MA"] = _emit(chart, "MA", times, ma_vals, offset, _COLOR_MA)

    # 平滑化（type != none）。
    if smoothing_type != "none":
        ma_series = pd.Series(ma_vals)
        smoothed = _smooth(ma_series, smoothing_type, smoothing_length)
        lines["Smoothing"] = _emit(
            chart, "Smoothing", times, smoothed.to_numpy(), offset, _COLOR_SMOOTH
        )
        # ボリンジャーバンド（SMA + Bollinger Bands）。
        if smoothing_type == "sma_bb":
            basis = ma_series.rolling(smoothing_length, min_periods=smoothing_length).mean()
            std = ma_series.rolling(smoothing_length, min_periods=smoothing_length).std(ddof=0)
            upper = (basis + bb_stddev * std).to_numpy()
            lower = (basis - bb_stddev * std).to_numpy()
            lines["Upper"] = _emit(chart, "Upper", times, upper, offset, _COLOR_BAND, style="dashed")
            lines["Lower"] = _emit(chart, "Lower", times, lower, offset, _COLOR_BAND, style="dashed")

    return lines
