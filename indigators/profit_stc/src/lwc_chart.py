"""層名: 出力アダプタ（lightweight-charts への系列追加・duck typing）。

責務:
    出力アダプタ。``lightweight_charts`` を import せず、``create_line`` /
    ``horizontal_line`` を持つオブジェクト（chart）をダックタイピングで受ける
    （PORTING_GUIDE §2/§6）。指標パッケージの依存を numpy/pandas に保つ。元 MQL4 は
    別ウィンドウのオシレーター線 1 本（DRAW_LINE, DarkGreen, width2）+ σ 水準線 4 本
    （P1/P2/M1/M2, levelcolor C'84,84,84', STYLE_SOLID）であるため、呼び出し側が用意
    した（サブ）チャートにライン系列と水平線を追加する。warm-up（i<period-1）は元
    iStochastic 既定どおり 0 で描画される（NaN は発生しない）。

元 MQL4 対応:
    ``SetIndexStyle(0, DRAW_LINE)``（ExtBufferOscillator, indicator_color1 DarkGreen,
    indicator_width1 2, separate_window）と ``StcLCStdDevArray[1..4]``
    （P1/P2/M1/M2, indicator_levelcolor / indicator_levelstyle STYLE_SOLID）。

依存（PORTING_GUIDE §8）:
    標準: __future__, typing / 外部: numpy, pandas / プロジェクト内: stc, core
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd

from common_view import LEVEL_LINE_WIDTH  # noqa: E402
from common_view.lwc_adapter import SeriesLike  # noqa: E402

from .core import DEFAULT_PERIOD
from .stc import OSC_COLUMN, build_stc, stc_levels
from marketdata.time_column import resolve_times as _resolve_times  # noqa: E402

_COLOR = "rgba(0, 100, 0, 1)"           # 元 indicator_color1 DarkGreen
_WIDTH = 2                              # 元 indicator_width1 2
_LEVEL_COLOR = "rgba(84, 84, 84, 1)"   # 元 indicator_levelcolor C'84,84,84'

# 重畳する σ 水準線（P1=+1.00σ / P2=+1.96σ / M1=-1.00σ / M2=-1.96σ）。
_LEVEL_KEYS: tuple[str, ...] = ("P1", "P2", "M1", "M2")


_Line = SeriesLike  # 共有 Protocol の別名（要求は ``set`` のみ・構造的部分型）


@runtime_checkable
class _Chart(Protocol):
    def create_line(self, name: str, **kwargs) -> _Line: ...
    def horizontal_line(self, price: float, **kwargs): ...


def add_stc(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    period: int = DEFAULT_PERIOD,
    time_column: str | None = None,
    color: str = _COLOR,
    draw_levels: bool = True,
) -> list:
    """chart に STC オシレーター線と σ 水準線（4 本）を追加する。

    Args:
        chart: ``create_line(name, **kwargs)`` と ``horizontal_line(price, **kwargs)``
            を持つオブジェクト（duck typing。別ウィンドウの場合は subchart を渡す）。
        df: OHLC DataFrame（high/low/close 必須・列名大小不問）。
        period: オシレーター期間（既定 70。元 inpPeriodOscillator）。
        time_column: 時刻列の明示指定（省略時は time/date/DatetimeIndex を探索）。
        color: ライン色（既定 DarkGreen）。
        draw_levels: True で σ 水準線（P1/P2/M1/M2）を水平線として追加。

    Returns:
        生成したオブジェクトのリスト（[line, *horizontal_lines]）。

    Raises:
        KeyError: 時刻が解決できない / HLC 列が無い場合。
    """
    bands = build_stc(df, period=period)
    times = _resolve_times(df, time_column)

    line = chart.create_line(
        name=OSC_COLUMN, color=color, style="solid", width=_WIDTH,
        price_line=False, price_label=False,
    )
    # 値列名はライン名と完全一致させる（ガイド §5）。warm-up は 0 で残る（NaN 無し）。
    series = pd.DataFrame(
        {"time": times, OSC_COLUMN: bands[OSC_COLUMN].to_numpy()}
    ).dropna()
    line.set(series)

    created = [line]
    if draw_levels:
        levels = stc_levels(df, period=period)
        for key in _LEVEL_KEYS:
            created.append(chart.horizontal_line(
                price=float(levels[key]), color=_LEVEL_COLOR, width=LEVEL_LINE_WIDTH,
                style="solid", text=key, axis_label_visible=False,
            ))
    return created
