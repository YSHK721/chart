"""出力アダプタ: lightweight-charts への系列追加（duck typing）。

層名/責務:
    出力アダプタ。``lightweight_charts`` を import せず、``create_line`` を持つ
    オブジェクト（chart）をダックタイピングで受ける（ガイド §2/§6）。指標パッケージの
    依存を numpy/pandas に保つ。塗り(fill)は wrapper 未対応のため、平均=実線、
    上下分位点=点線で表現する（ガイド §6）。

元 MQL4 の対応:
    buf_mean/buf_q1/buf_q2 の 3 ライン（実線＋点線2本、MediumSlateBlue、overlay）。

依存:
    標準: __future__, typing / 外部: numpy, pandas / プロジェクト内: bands, core
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd
from common_view.lwc_adapter import SeriesLike  # noqa: E402

from .bands import build_btlm_bands
from .core import (
    DEFAULT_MAXBARS,
    DEFAULT_Q_HIGH,
    DEFAULT_Q_LOW,
    BtlmFitter,
    mean_column,
    quantile_column,
)

_COLOR = "rgba(123, 104, 238, 1)"  # MediumSlateBlue


_Line = SeriesLike  # 共有 Protocol の別名（要求は ``set`` のみ・構造的部分型）


@runtime_checkable
class _Chart(Protocol):
    def create_line(self, name: str, **kwargs) -> _Line: ...


def _resolve_times(df: pd.DataFrame, time_column: str | None) -> pd.Series:
    """時刻系列を解決する（明示指定 > time 列 > date 列 > DatetimeIndex の順）。"""
    lower_map = {c.lower(): c for c in df.columns}
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


def add_btlm(
    chart: _Chart,
    df: pd.DataFrame,
    fitter: BtlmFitter,
    *,
    price: str = "open",
    maxbars: int = DEFAULT_MAXBARS,
    q_low: float = DEFAULT_Q_LOW,
    q_high: float = DEFAULT_Q_HIGH,
    time_column: str | None = None,
    color: str = _COLOR,
) -> list:
    """chart に btlm の 3 ライン（平均=実線、上下分位点=点線）を追加する。

    Args:
        chart: ``create_line(name, **kwargs)`` を持つオブジェクト（duck typing）。
        df: 価格列を持つ DataFrame。
        fitter: BtlmFitter 実装。
        price/maxbars/q_low/q_high: build_btlm_bands と同じ意味。
        time_column: 時刻列の明示指定（省略時は time/date/DatetimeIndex を探索）。
        color: ライン色（既定 MediumSlateBlue）。

    Returns:
        生成した Line オブジェクトのリスト [mean, lower, upper]。

    Raises:
        KeyError: 時刻が解決できない場合。
    """
    bands = build_btlm_bands(
        df, fitter, price=price, maxbars=maxbars, q_low=q_low, q_high=q_high
    )
    times = _resolve_times(df, time_column)

    mean_name = mean_column()
    low_name = quantile_column(q_low)
    high_name = quantile_column(q_high)

    specs = [
        (mean_name, "solid", 2),
        (low_name, "dotted", 1),
        (high_name, "dotted", 1),
    ]
    lines = []
    for name, style, width in specs:
        line = chart.create_line(
            name=name, color=color, style=style, width=width,
            price_line=False, price_label=False,
        )
        # 値列名はライン名と完全一致させる（ガイド §5）。NaN は描画側で除外。
        series = pd.DataFrame({"time": times, name: bands[name].to_numpy()}).dropna()
        line.set(series)
        lines.append(line)
    return lines
