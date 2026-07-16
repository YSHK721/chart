"""層名: 出力アダプタ（lightweight-charts への系列追加・duck typing）。

責務:
    出力アダプタ。``lightweight_charts`` を import せず、``create_line`` /
    ``horizontal_line`` を持つオブジェクト（chart）をダックタイピングで受ける
    （PORTING_GUIDE §2/§6）。指標パッケージの依存を numpy/pandas に保つ。元 MQL4 は
    別ウィンドウ（[0,100]）の RSI 線 ＋ EMA 平滑線（共に DRAW_LINE, clrLime）と
    σ 水準線 7 本（±1/2/3σ, levelcolor C'84,84,84', STYLE_SOLID ＋ 中央線 50）で
    あるため、呼び出し側が用意した（サブ）チャートにライン 2 本と水平線 7 本を追加
    する。warm-up（i<rsi_period）は元 iRSI 既定どおり 0 で描画される（NaN は発生しない）。

    ライン ``name`` は値列名（``rsi`` / ``rsi_ma``）と完全一致させる（ガイド §5）。
    Apply 依存の短名（"RSI-Typical price (6)" 等）は plot 凡例側の関心事であり、lwc の
    ライン name には用いない（lwc は値列名一致が制約のため）。

元 MQL4 対応:
    ``SetIndexStyle(0/1, DRAW_LINE)``（ExtRSIBuffer / ExtMABuffer,
    indicator_color1/2 clrLime, separate_window, indicator_minimum 0 /
    indicator_maximum 100）と StDevA1..A6（±1/2/3σ）＋ 中央線 50
    （indicator_levelcolor C'84,84,84' / indicator_levelstyle STYLE_SOLID）。

依存（PORTING_GUIDE §8）:
    標準: __future__, typing / 外部: numpy, pandas / プロジェクト内: rsi, core
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd

from common_view import LEVEL_LINE_WIDTH  # noqa: E402

from .core import DEFAULT_APPLY, DEFAULT_MA_PERIOD, DEFAULT_RSI_PERIOD
from .rsi import MA_COLUMN, RSI_COLUMN, build_rsi, rsi_levels

_RSI_COLOR = "rgba(0, 255, 0, 1)"      # 元 indicator_color1 clrLime
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


def add_rsi(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    rsi_period: int = DEFAULT_RSI_PERIOD,
    apply: int = DEFAULT_APPLY,
    ma_period: int = DEFAULT_MA_PERIOD,
    time_column: str | None = None,
    draw_levels: bool = True,
) -> list:
    """chart に RSI 線・EMA 平滑線（2 本）と σ 水準線（7 本）を追加する。

    Args:
        chart: ``create_line(name, **kwargs)`` と ``horizontal_line(price, **kwargs)``
            を持つオブジェクト（duck typing。別ウィンドウの場合は subchart を渡す）。
        df: OHLC DataFrame（open/high/low/close 必須・列名大小不問・**volume 不要**）。
        rsi_period: RSI 期間（既定 6。元 InpRSIPeriod）。
        apply: 適用価格選択（既定 5 -> TYPICAL。元 Apply。core の APPLY_TO_PRICE 写像）。
        ma_period: EMA 期間（既定 5。元 InpMAPeriod）。
        time_column: 時刻列の明示指定（省略時は time/date/DatetimeIndex を探索）。
        draw_levels: True で σ 水準線（±1/2/3σ ＋ 中央線 50）を水平線として追加。

    Returns:
        生成したオブジェクトのリスト（[rsi_line, ma_line, *horizontal_lines]）。

    Raises:
        KeyError: 時刻が解決できない / OHLC 列が無い場合。
    """
    built = build_rsi(df, rsi_period=rsi_period, apply=apply, ma_period=ma_period)
    times = _resolve_times(df, time_column)

    created: list = []
    # RSI 線・EMA 平滑線。値列名はライン名と完全一致させる（ガイド §5）。
    # 多数線のため price_line/label は False（§6）。warm-up は 0 で残る（NaN 無し）。
    for col, color in ((RSI_COLUMN, _RSI_COLOR), (MA_COLUMN, _MA_COLOR)):
        line = chart.create_line(
            name=col, color=color, style="solid", width=_WIDTH,
            price_line=False, price_label=False,
        )
        series = pd.DataFrame({"time": times, col: built[col].to_numpy()})
        line.set(series)
        created.append(line)

    if draw_levels:
        levels = rsi_levels(
            df, rsi_period=rsi_period, apply=apply, ma_period=ma_period
        )
        for key in _LEVEL_KEYS:
            created.append(chart.horizontal_line(
                price=float(levels[key]), color=_LEVEL_COLOR, width=LEVEL_LINE_WIDTH,
                style="solid", text=key, axis_label_visible=False,
            ))
    return created
