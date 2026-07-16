"""層名: 出力アダプタ（lightweight-charts への系列追加・duck typing）。

責務:
    出力アダプタ。``lightweight_charts`` を import せず、``create_histogram`` /
    ``horizontal_line`` / ``create_line`` を持つオブジェクト（chart / subchart）を
    ダックタイピングで受ける（PORTING_GUIDE §2/§6）。指標パッケージの依存を
    numpy/pandas に保つ。元 MQL4 は二系統の描画を持つため、別関数で再現する:

    (A) separate window（``add_hlband_separate``）:
        元 ``SetIndexStyle(0, DRAW_HISTOGRAM)``（ExtVOLBuffer=hl_range, color clrLime,
        width2, separate_window）＋ σ 水準線 4 本（avg/b165/b196/b258,
        indicator_levelcolor C'84,84,84' グレー, STYLE_SOLID）。subwindow は
        INDICATOR_MINIMUM=0 / INDICATOR_MAXIMUM=b196*2。

    (B) main chart overlay（``add_hlband_overlay``）:
        元 ``ObjectCreate(..., OBJ_TREND, ...)`` 8 本（high_*/low_*, LimeGreen）を
        メインチャートの水平線で再表現する。MT4 の描画オブジェクト（ObjectCreate /
        ObjectDelete）そのものは移植対象外（SPEC §2）で、計算と描画の「意味」のみを移す。

    値列名はライン名と完全一致させる（ガイド §5）。多数の水平線は
    axis_label_visible=False（ガイド §6。実 horizontal_line API 準拠）。
    具体描画ライブラリを core/成果物層へ侵入させない。

元 MQL4 対応:
    L32 SetIndexStyle(0, DRAW_HISTOGRAM) / indicator_color1 clrLime / indicator_width1 2
        → add_hlband_separate のヒストグラム（name=hl_range）。
    L97-100 StcLCStdDevArray[1..4]（b165/b196/b258/avg）/ indicator_levelcolor C'84,84,84'
        → add_hlband_separate の水平線 4 本。
    L102-103 INDICATOR_MINIMUM=0 / INDICATOR_MAXIMUM=b196*2
        → hlband_levels の sub_min/sub_max として呼び出し側へ提供
        （create_histogram には渡さない。実 API 非侵襲）。
    L83-94 ObjectCreate(OBJ_TREND) 8 本 / OBJPROP_COLOR LimeGreen
        → add_hlband_overlay の水平線 8 本。

依存（PORTING_GUIDE §8）:
    標準: __future__, typing / 外部: numpy, pandas / プロジェクト内: hlband
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd

from common_view import LEVEL_LINE_WIDTH  # noqa: E402

from .hlband import (
    RANGE_COLUMN,
    build_hlband,
    hlband_levels,
    hlband_price_bands,
)

_HIST_COLOR = "rgba(0, 255, 0, 1)"        # 元 indicator_color1 clrLime
_HIST_WIDTH = 2                           # 元 indicator_width1 2
_LEVEL_COLOR = "rgba(84, 84, 84, 1)"      # 元 indicator_levelcolor C'84,84,84'
_OVERLAY_COLOR = "rgba(50, 205, 50, 1)"   # 元 OBJPROP_COLOR LimeGreen

# separate の σ 水準線（avg/b165/b196/b258）。元 StcLCStdDevArray[1..4]。
_LEVEL_KEYS: tuple[str, ...] = ("avg", "b165", "b196", "b258")

# overlay の水平線 8 本（High 側=減算 / Low 側=加算）。元 OBJ_TREND 8 本。
_OVERLAY_KEYS: tuple[str, ...] = (
    "high_avg",
    "high_b165",
    "high_b196",
    "high_b258",
    "low_avg",
    "low_b165",
    "low_b196",
    "low_b258",
)


@runtime_checkable
class _Histogram(Protocol):
    def set(self, data: pd.DataFrame) -> None: ...


@runtime_checkable
class _SubChart(Protocol):
    def create_histogram(self, name: str, **kwargs) -> _Histogram: ...
    def horizontal_line(self, price: float, **kwargs): ...


@runtime_checkable
class _Chart(Protocol):
    def horizontal_line(self, price: float, **kwargs): ...


def _resolve_times(df: pd.DataFrame, time_column: str | None) -> pd.Series:
    """時刻系列を解決する（明示指定 > time 列 > date 列 > DatetimeIndex の順）。

    Raises:
        KeyError: 指定時刻列が無い / time・date 列も DatetimeIndex も無い場合。
    """
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


def add_hlband_separate(
    subchart: _SubChart,
    df: pd.DataFrame,
    *,
    time_column: str | None = None,
    color: str = _HIST_COLOR,
    draw_levels: bool = True,
) -> list:
    """別ウィンドウ（subchart）に hl_range ヒストグラムと σ 水準線 4 本を追加する。

    元 ``SetIndexStyle(0, DRAW_HISTOGRAM)``（clrLime, width2, separate_window）と
    σ 水準線（avg/b165/b196/b258, グレー C'84,84,84', SOLID）に対応する。subwindow の
    範囲（MIN=0 / MAX=b196*2）は ``hlband_levels`` の sub_min/sub_max で呼び出し側に
    提供し、``create_histogram`` には渡さない（実 API 非侵襲）。

    Args:
        subchart: ``create_histogram(name, **kwargs)`` と
            ``horizontal_line(price, **kwargs)`` を持つオブジェクト（duck typing。
            別ウィンドウ用の subchart を渡す）。
        df: high/low を含む DataFrame（列名大小不問）。
        time_column: 時刻列の明示指定（省略時は time/date/DatetimeIndex を探索）。
        color: ヒストグラム色（既定 clrLime）。
        draw_levels: True で σ 水準線（avg/b165/b196/b258）を水平線として追加。

    Returns:
        生成したオブジェクトのリスト（[histogram, *horizontal_lines]）。

    Raises:
        KeyError: high/low 列が無い / 時刻が解決できない場合。
        ValueError: 空 DataFrame の場合（成果物層ガード）。
    """
    bands = build_hlband(df)  # high/low 欠落 KeyError・空 ValueError はここで送出
    times = _resolve_times(df, time_column)
    levels = hlband_levels(df)

    hist = subchart.create_histogram(
        name=RANGE_COLUMN, color=color, price_line=False, price_label=False,
    )
    # 値列名はライン名と完全一致させる（ガイド §5）。warm-up は無い（全 i 定義）。
    series = pd.DataFrame(
        {"time": times, RANGE_COLUMN: bands[RANGE_COLUMN].to_numpy()}
    )
    hist.set(series)

    created: list = [hist]
    if draw_levels:
        for key in _LEVEL_KEYS:
            created.append(subchart.horizontal_line(
                price=float(levels[key]), color=_LEVEL_COLOR, width=LEVEL_LINE_WIDTH,
                style="solid", text=key, axis_label_visible=False,
            ))
    return created


def add_hlband_overlay(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    color: str = _OVERLAY_COLOR,
) -> list:
    """メインチャートに High/Low バンドの水平線 8 本（high_*/low_*）を追加する。

    元 ``ObjectCreate(..., OBJ_TREND, ...)`` 8 本（LimeGreen）を水平線で再表現する
    （MT4 描画オブジェクト自体は移植対象外・SPEC §2、最新足 H/L への投影値のみ移す）。
    多数線のため axis_label_visible=False（ガイド §6。実 horizontal_line API 準拠）。

    Args:
        chart: ``horizontal_line(price, **kwargs)`` を持つメインチャート（duck typing）。
        df: high/low を含む DataFrame（列名大小不問）。
        color: 水平線色（既定 LimeGreen）。

    Returns:
        生成した水平線オブジェクトのリスト（8 本）。

    Raises:
        KeyError: high/low 列が無い場合。
        ValueError: 空 DataFrame の場合（成果物層ガード）。
    """
    bands = hlband_price_bands(df)  # high/low 欠落 KeyError・空 ValueError はここで送出
    created: list = []
    for key in _OVERLAY_KEYS:
        created.append(chart.horizontal_line(
            price=float(bands[key]), color=color, width=LEVEL_LINE_WIDTH,
            style="solid", text=key, axis_label_visible=False,
        ))
    return created
