"""出力アダプタ: lightweight-charts への系列追加（duck typing）。

層名/責務:
    出力アダプタ。``lightweight_charts`` を import せず、``create_histogram`` /
    ``horizontal_line`` を持つオブジェクト（chart）をダックタイピングで受ける
    （ガイド §2/§6）。指標パッケージの依存を numpy/pandas に保つ。元 MQL4 は
    別ウィンドウのヒストグラム 1 本（MAKairi, Red）+ 水準線（1/0.5/-0.5/-1）で
    あるため、呼び出し側が用意した（サブ）チャートにヒストグラム系列と水平線を
    追加する。NaN（最古バー・MA 未確定）は dropna で除外する（ガイド §5）。

元 MQL4 の対応:
    ``DRAW_HISTOGRAM``（MAKairi, indicator_color1 Red, separate_window）と
    ``#property indicator_level1..4``（1 / 0.5 / -0.5 / -1）。

依存:
    標準: __future__, typing / 外部: numpy, pandas / プロジェクト内: osi_ma, core
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd

from common_view import LEVEL_LINE_WIDTH  # noqa: E402
from common_view.lwc_adapter import SeriesLike  # noqa: E402

from .core import DEFAULT_MA_MODE, DEFAULT_MA_PERIOD
from .osi_ma import KAIRI_COLUMN, build_osi_ma, osi_ma_levels

_COLOR = "rgba(211, 47, 47, 0.85)"      # 元 indicator_color1 Red
_LEVEL_COLOR = "rgba(84, 84, 84, 0.6)"  # 水準線（点線）

# 重畳する水準線の値（元 indicator_level1..4: 1 / 0.5 / -0.5 / -1）。
_LEVEL_VALUES: tuple[float, ...] = tuple(osi_ma_levels().values())


_Histogram = SeriesLike  # 共有 Protocol の別名（要求は ``set`` のみ・構造的部分型）


@runtime_checkable
class _Chart(Protocol):
    def create_histogram(self, name: str, **kwargs) -> _Histogram: ...
    def horizontal_line(self, price: float, **kwargs): ...


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


def add_osi_ma(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    ma_mode: int = DEFAULT_MA_MODE,
    ma_period: int = DEFAULT_MA_PERIOD,
    time_column: str | None = None,
    color: str = _COLOR,
    draw_levels: bool = True,
) -> list:
    """chart に OSI_MA（MAKairi）ヒストグラムと水準線を追加する。

    Args:
        chart: ``create_histogram(name, **kwargs)`` と ``horizontal_line(price, **kwargs)``
            を持つオブジェクト（duck typing。別ウィンドウの場合は subchart を渡す）。
        df: 少なくとも close 列を含む DataFrame（列名大小不問）。
        ma_mode: MA 種別（0=SMA,1=EMA,2=SMMA,3=LWMA, 既定 1）。
        ma_period: MA 期間（既定 21）。
        time_column: 時刻列の明示指定（省略時は time/date/DatetimeIndex を探索）。
        color: ヒストグラム色（既定 Red）。
        draw_levels: True で水準線（1/0.5/-0.5/-1）を水平線として追加。

    Returns:
        生成したオブジェクトのリスト（[histogram, *horizontal_lines]）。

    Raises:
        KeyError: 時刻が解決できない / close 列が無い場合。
    """
    bands = build_osi_ma(df, ma_mode=ma_mode, ma_period=ma_period)
    times = _resolve_times(df, time_column)

    hist = chart.create_histogram(
        name=KAIRI_COLUMN, color=color, price_line=False, price_label=False
    )
    # 値列名はヒストグラム名と完全一致させる（ガイド §5）。NaN は dropna で除外。
    series = pd.DataFrame(
        {"time": times, KAIRI_COLUMN: bands[KAIRI_COLUMN].to_numpy()}
    ).dropna()
    hist.set(series)

    created = [hist]
    if draw_levels:
        for value in _LEVEL_VALUES:
            created.append(chart.horizontal_line(
                price=float(value), color=_LEVEL_COLOR, width=LEVEL_LINE_WIDTH,
                style="dotted", text=f"{value:g}", axis_label_visible=False,
            ))
    return created
