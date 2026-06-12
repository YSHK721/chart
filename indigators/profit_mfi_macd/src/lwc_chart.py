"""層名: 出力アダプタ（lightweight-charts への系列追加・duck typing）。

責務:
    出力アダプタ。``lightweight_charts`` を import せず、``create_histogram`` /
    ``create_line`` / ``horizontal_line`` を持つオブジェクト（chart）をダック
    タイピングで受ける（PORTING_GUIDE §2/§6）。指標パッケージの依存を numpy/pandas
    に保つ。元 MQL4 は別ウィンドウ（MACD 型・[0,100] 制約なし）のヒストグラム 1 本
    （MacdHistogram, DRAW_HISTOGRAM, C'133,219,24'）＋ ライン 2 本（MFIMACD ＝
    C'205,232,65', Signal ＝ C'167,197,32', 共に DRAW_LINE）＋ σ 水準線 7 本
    （StDevA1..A6 ＝ ±1/2/3σ, levelcolor C'84,84,84', STYLE_SOLID ＋ 中央線 50）で
    あるため、呼び出し側が用意した（サブ）チャートにヒストグラム 1 本・ライン 2 本・
    水平線 7 本を追加する。warm-up（i<mfi_period）は元 iMFI 既定どおり 0 起点で
    描画される（NaN は発生しない）。

元 MQL4 対応:
    ``SetIndexStyle(0, DRAW_HISTOGRAM)``（MacdHistogramBuffer, indicator_color1
    C'133,219,24'）/ ``SetIndexStyle(1, DRAW_LINE)``（MacdBuffer, label "MFIMACD",
    indicator_color2 C'205,232,65'）/ ``SetIndexStyle(2, DRAW_LINE)``（SignalBuffer,
    label "Signal", indicator_color3 C'167,197,32'）と StDevA1..A6（±1/2/3σ）＋
    中央線 50（indicator_levelcolor C'84,84,84' / indicator_levelstyle STYLE_SOLID）。

依存（PORTING_GUIDE §8）:
    標準: __future__, typing / 外部: numpy, pandas / プロジェクト内: mfimacd, core
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd

from .core import (
    DEFAULT_FAST_EMA,
    DEFAULT_MFI_PERIOD,
    DEFAULT_SIGNAL_EMA,
    DEFAULT_SLOW_EMA,
)
from .mfimacd import (
    HIST_COLUMN,
    MACD_COLUMN,
    SIGNAL_COLUMN,
    build_mfimacd,
    mfimacd_levels,
)

# ライン名（元 SetIndexLabel: MacdBuffer="MFIMACD", SignalBuffer="Signal"）。
# 値列名はライン名と完全一致させる（PORTING_GUIDE §5）。
MACD_LINE_NAME = "MFIMACD"
SIGNAL_LINE_NAME = "Signal"

_HIST_COLOR = "rgba(133, 219, 24, 0.85)"   # 元 indicator_color1 C'133,219,24'
_MACD_COLOR = "rgba(205, 232, 65, 1)"      # 元 indicator_color2 C'205,232,65'
_SIGNAL_COLOR = "rgba(167, 197, 32, 1)"    # 元 indicator_color3 C'167,197,32'
_WIDTH = 1                                 # 元 indicator_width 未指定（既定 1）
_LEVEL_COLOR = "rgba(84, 84, 84, 1)"       # 元 indicator_levelcolor C'84,84,84'

# 重畳する σ 水準線（p1/p2/p3=+1/2/3σ, m1/m2/m3=-1/2/3σ, mid50=中央線 50）。
_LEVEL_KEYS: tuple[str, ...] = ("p1", "p2", "p3", "m1", "m2", "m3", "mid50")


@runtime_checkable
class _Series(Protocol):
    def set(self, data: pd.DataFrame) -> None: ...


@runtime_checkable
class _Chart(Protocol):
    def create_histogram(self, name: str, **kwargs) -> _Series: ...
    def create_line(self, name: str, **kwargs) -> _Series: ...
    def horizontal_line(self, price: float, **kwargs): ...


def _resolve_times(df: pd.DataFrame, time_column: str | None) -> pd.Series:
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


def add_mfimacd(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    mfi_period: int = DEFAULT_MFI_PERIOD,
    fast: int = DEFAULT_FAST_EMA,
    slow: int = DEFAULT_SLOW_EMA,
    signal: int = DEFAULT_SIGNAL_EMA,
    time_column: str | None = None,
    draw_levels: bool = True,
) -> list:
    """chart に MFIMACD のヒストグラム 1 本・ライン 2 本・σ 水準線 7 本を追加する。

    Args:
        chart: ``create_histogram(name, **kwargs)`` / ``create_line(name, **kwargs)``
            / ``horizontal_line(price, **kwargs)`` を持つオブジェクト（duck typing。
            別ウィンドウの場合は subchart を渡す）。
        df: OHLCV DataFrame（high/low/close/**volume** 必須・列名大小不問）。
        mfi_period: MFI 期間（既定 13。元 MFIperiod）。
        fast: FastEMA 期間（既定 4）。
        slow: SlowEMA 期間（既定 8）。
        signal: SignalEMA 期間（既定 4）。
        time_column: 時刻列の明示指定（省略時は time/date/DatetimeIndex を探索）。
        draw_levels: True で σ 水準線（±1/2/3σ ＋ 中央線 50）を水平線として追加。

    Returns:
        生成したオブジェクトのリスト
        （``[histogram, macd_line, signal_line, *horizontal_lines]``）。

    Raises:
        KeyError: 時刻が解決できない / HLC・volume 列が無い場合。
    """
    built = build_mfimacd(
        df, mfi_period=mfi_period, fast=fast, slow=slow, signal=signal,
    )
    times = _resolve_times(df, time_column)

    created: list = []

    # ヒストグラム 1 本（MacdHistogram, DRAW_HISTOGRAM）。値列名はヒストグラム名と
    # 完全一致させる（§5）。多数系列のため price_line/label は False（§6）。
    hist = chart.create_histogram(
        name=HIST_COLUMN, color=_HIST_COLOR,
        price_line=False, price_label=False,
    )
    hist.set(pd.DataFrame({"time": times, HIST_COLUMN: built[HIST_COLUMN].to_numpy()}))
    created.append(hist)

    # ライン 2 本（MFIMACD / Signal, DRAW_LINE）。値列名はライン名と完全一致させる（§5）。
    line_specs = (
        (MACD_LINE_NAME, MACD_COLUMN, _MACD_COLOR),
        (SIGNAL_LINE_NAME, SIGNAL_COLUMN, _SIGNAL_COLOR),
    )
    for line_name, value_col, color in line_specs:
        line = chart.create_line(
            name=line_name, color=color, style="solid", width=_WIDTH,
            price_line=False, price_label=False,
        )
        # 値列名 = ライン name（§5）。元の列値（macd/signal）を name にマップする。
        line.set(pd.DataFrame({"time": times, line_name: built[value_col].to_numpy()}))
        created.append(line)

    if draw_levels:
        levels = mfimacd_levels(
            df, mfi_period=mfi_period, fast=fast, slow=slow, signal=signal,
        )
        for key in _LEVEL_KEYS:
            created.append(chart.horizontal_line(
                price=float(levels[key]), color=_LEVEL_COLOR, width=1,
                style="solid", text=key, axis_label_visible=False,
            ))
    return created
