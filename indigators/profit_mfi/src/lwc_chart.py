"""層名: 出力アダプタ（lightweight-charts への系列追加・duck typing）。

責務:
    出力アダプタ。``lightweight_charts`` を import せず、``create_line`` /
    ``horizontal_line`` を持つオブジェクト（chart）をダックタイピングで受ける
    （PORTING_GUIDE §2/§6）。指標パッケージの依存を numpy/pandas に保つ。元 MQL4 は
    別ウィンドウ（[0,100]）の MFI 線 ＋ EMA 平滑線（共に DRAW_LINE, clrLime）と
    σ 水準線 7 本（±1/2/3σ, levelcolor C'84,84,84', STYLE_SOLID ＋ 中央線 50）で
    あるため、呼び出し側が用意した（サブ）チャートにライン 2 本と水平線 7 本を追加
    する。warm-up（i<mfi_period）は元 iMFI 既定どおり 0 で描画される（NaN は発生しない）。

元 MQL4 対応:
    ``SetIndexStyle(0/1, DRAW_LINE)``（ExtMFIBuffer / ExtMABuffer,
    indicator_color1/2 clrLime, separate_window, indicator_minimum 0 /
    indicator_maximum 100）と StDevA1..A6（±1/2/3σ）＋ 中央線 50
    （indicator_levelcolor C'84,84,84' / indicator_levelstyle STYLE_SOLID）。

依存（PORTING_GUIDE §8）:
    標準: __future__, typing / 外部: numpy, pandas / プロジェクト内: mfi, core
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol, runtime_checkable

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root → common
from common import LEVEL_LINE_WIDTH  # noqa: E402

from .core import DEFAULT_MA_PERIOD, DEFAULT_MFI_PERIOD
from .mfi import MA_COLUMN, MFI_COLUMN, build_mfi, mfi_levels

_MFI_COLOR = "rgba(0, 255, 0, 1)"      # 元 indicator_color1 clrLime
_MA_COLOR = "rgba(0, 255, 0, 1)"       # 元 indicator_color2 clrLime
_WIDTH = 1                             # 元 indicator_width 未指定（既定 1）
_LEVEL_COLOR = "rgba(84, 84, 84, 1)"   # 元 indicator_levelcolor C'84,84,84'

# 重畳する σ 水準線（p1/p2/p3=+1/2/3σ, m1/m2/m3=-1/2/3σ, mid50=中央線 50）。
_LEVEL_KEYS: tuple[str, ...] = ("p1", "p2", "p3", "m1", "m2", "m3", "mid50")


@runtime_checkable
class _Line(Protocol):
    def set(self, data: pd.DataFrame) -> None: ...


@runtime_checkable
class _Chart(Protocol):
    def create_line(self, name: str, **kwargs) -> _Line: ...
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


def add_mfi(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    mfi_period: int = DEFAULT_MFI_PERIOD,
    ma_period: int = DEFAULT_MA_PERIOD,
    time_column: str | None = None,
    draw_levels: bool = True,
) -> list:
    """chart に MFI 線・EMA 平滑線（2 本）と σ 水準線（7 本）を追加する。

    Args:
        chart: ``create_line(name, **kwargs)`` と ``horizontal_line(price, **kwargs)``
            を持つオブジェクト（duck typing。別ウィンドウの場合は subchart を渡す）。
        df: OHLCV DataFrame（high/low/close/**volume** 必須・列名大小不問）。
        mfi_period: MFI 期間（既定 14。元 InpMFIPeriod）。
        ma_period: EMA 期間（既定 5。元 InpMAPeriod）。
        time_column: 時刻列の明示指定（省略時は time/date/DatetimeIndex を探索）。
        draw_levels: True で σ 水準線（±1/2/3σ ＋ 中央線 50）を水平線として追加。

    Returns:
        生成したオブジェクトのリスト（[mfi_line, ma_line, *horizontal_lines]）。

    Raises:
        KeyError: 時刻が解決できない / HLC・volume 列が無い場合。
    """
    built = build_mfi(df, mfi_period=mfi_period, ma_period=ma_period)
    times = _resolve_times(df, time_column)

    created: list = []
    # MFI 線・EMA 平滑線。値列名はライン名と完全一致させる（ガイド §5）。
    # 多数線のため price_line/label は False（§6）。warm-up は 0 で残る（NaN 無し）。
    for col, color in ((MFI_COLUMN, _MFI_COLOR), (MA_COLUMN, _MA_COLOR)):
        line = chart.create_line(
            name=col, color=color, style="solid", width=_WIDTH,
            price_line=False, price_label=False,
        )
        series = pd.DataFrame({"time": times, col: built[col].to_numpy()})
        line.set(series)
        created.append(line)

    if draw_levels:
        levels = mfi_levels(df, mfi_period=mfi_period, ma_period=ma_period)
        for key in _LEVEL_KEYS:
            created.append(chart.horizontal_line(
                price=float(levels[key]), color=_LEVEL_COLOR, width=LEVEL_LINE_WIDTH,
                style="solid", text=key, axis_label_visible=False,
            ))
    return created
