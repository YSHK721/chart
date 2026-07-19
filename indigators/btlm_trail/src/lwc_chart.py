"""出力アダプタ: lightweight-charts への btlm_trail 系列追加（duck typing）。

層名/責務:
    出力アダプタ。``lightweight_charts`` を import せず、``create_line`` を持つ
    オブジェクト（chart）をダックタイピングで受ける（ガイド §2/§6）。btlm_trail の
    トレンド現在位置（btlm_mean）と分位ペアごとのバンド端（q_low/q_high）を系列として
    追加する。ドット/ライン・塗り・外れ値オフセット等の表示切替は front 描画層が担う
    （本層は数値系列を供給する）。

系列名（固定・F3 照合は catalog の SeriesDef 集合と突合）:
    - "btlm_trail_mean"       : 窓末尾 btlm_mean（トレンド現在位置＝ドットの原子）。
    - "btlm_trail_q{pct}"     : 分位ペアごとのバンド端（pct = round(q*100)）。

依存:
    標準: __future__, typing / 外部: numpy, pandas / プロジェクト内: core, trail
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

import numpy as np
import pandas as pd

from .core import DEFAULT_EMP_N, DEFAULT_MAXBARS, DEFAULT_Q_HIGH, DEFAULT_Q_LOW
from .trail import build_btlm_trail

_COLOR_MEAN = "rgba(123, 104, 238, 1)"   # MediumSlateBlue（tgp_btlm と同系色）
_COLOR_BAND = "rgba(123, 104, 238, 0.6)"


@runtime_checkable
class _Line(Protocol):
    def set(self, data: pd.DataFrame) -> None: ...


@runtime_checkable
class _Chart(Protocol):
    def create_line(self, name: str, **kwargs) -> _Line: ...


def _resolve_times(df: pd.DataFrame, time_column: Optional[str]) -> pd.Series:
    """時刻系列を解決する（明示指定 > time 列 > date 列 > DatetimeIndex の順）。"""
    lower_map = {str(c).lower(): c for c in df.columns}
    if time_column is not None:
        tcol = lower_map.get(time_column.lower(), time_column)
        if tcol not in df.columns:
            raise KeyError(f"指定された時刻列が存在しません: {time_column}")
        return pd.to_datetime(df[tcol]).reset_index(drop=True)
    if "time" in lower_map:
        return pd.to_datetime(df[lower_map["time"]]).reset_index(drop=True)
    if "date" in lower_map:
        return pd.to_datetime(df[lower_map["date"]]).reset_index(drop=True)
    if isinstance(df.index, pd.DatetimeIndex):
        return pd.Series(df.index, name="time").reset_index(drop=True)
    raise KeyError("時刻を解決できません（time/date 列、または DatetimeIndex が必要）。")


def _quantile_series_name(q: float) -> str:
    """分位 q（0..1）に対応する系列名（例 0.05 -> 'btlm_trail_q5'）。"""
    return f"btlm_trail_q{int(round(q * 100))}"


def _emit(chart, name, times, values, color, *, style="solid", width=1):
    """1 系列を chart へ追加する（値列名は系列名と一致・NaN は除外）。"""
    line = chart.create_line(
        name=name, color=color, style=style, width=width,
        price_line=False, price_label=False,
    )
    series = pd.DataFrame({"time": times, name: np.asarray(values, dtype=float)}).dropna()
    line.set(series)
    return line


def add_btlm_trail(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    source: str = "close",
    maxbars: int = DEFAULT_MAXBARS,
    q_low: float = DEFAULT_Q_LOW,
    q_high: float = DEFAULT_Q_HIGH,
    quantile_pairs=None,
    band_method: str = "ols",
    empirical_n: int = DEFAULT_EMP_N,
    time_column: Optional[str] = None,
    color: str = _COLOR_MEAN,
) -> dict[str, object]:
    """``chart`` へ btlm_trail の系列（btlm_mean ＋ 分位ペアごとのバンド端）を追加する。

    分位ペアは 2 経路: ``quantile_pairs`` を明示すれば複数ペア、未指定なら UI 由来の
    スカラ ``q_low`` / ``q_high`` から単一ペアを構成する（tgp_btlm と対称の結線様式）。

    Args:
        chart: ``create_line(name=...)`` を持つオブジェクト（duck typing）。
        df: OHLC DataFrame（時刻は time / date / DatetimeIndex で解決）。
        source: 8 択ソース（close/open/high/low/hl2/hlc3/ohlc4/hlcc4）。
        maxbars: 回帰窓の本数（既定 100）。
        q_low/q_high: 単一分位ペア（quantile_pairs 未指定時に使用）。
        quantile_pairs: 分位ペアの列（各 0<q_low<q_high<1・複数可）。明示時は q_low/q_high に優先。
        band_method: "ols"（名目）/ "empirical"（経験分位）。
        empirical_n: 経験分位バンドの参照本数（既定 500）。
        time_column: 時刻列の明示指定。
        color: btlm_mean の色。

    Returns:
        ``{series_name: Line}``。

    Raises:
        ValueError: source / 分位ペア / band_method 不正時。
        KeyError: 時刻が解決できない場合。
    """
    pairs = quantile_pairs if quantile_pairs is not None else [(q_low, q_high)]
    res = build_btlm_trail(
        df, source=source, maxbars=maxbars,
        quantile_pairs=pairs, band_method=band_method,
        empirical_n=empirical_n,
    )
    times = _resolve_times(df, time_column)

    lines: dict[str, object] = {}
    lines["btlm_trail_mean"] = _emit(
        chart, "btlm_trail_mean", times, res.mean, color, style="solid", width=2
    )
    for (ql, qh), (low, high) in res.bands.items():
        lo_name = _quantile_series_name(ql)
        hi_name = _quantile_series_name(qh)
        lines[lo_name] = _emit(chart, lo_name, times, low, _COLOR_BAND, style="dotted")
        lines[hi_name] = _emit(chart, hi_name, times, high, _COLOR_BAND, style="dotted")
    return lines
