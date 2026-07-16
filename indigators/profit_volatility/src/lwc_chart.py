"""出力アダプタ: lightweight-charts への系列追加（duck typing）。

層名/責務:
    出力アダプタ。``lightweight_charts`` を import せず、``create_histogram`` /
    ``horizontal_line`` を持つオブジェクト（chart）をダックタイピングで受ける
    （ガイド §2/§6）。指標パッケージの依存を numpy/pandas に保つ。元 MQL4
    ``PRO!fit_Volatility`` は別ウィンドウのヒストグラム 1 本（DRAW_HISTOGRAM,
    DarkGreen, height 100）+ σ12 水準線であるため、呼び出し側が用意した（サブ）チャートに
    ヒストグラム系列と水平線（上下 σ 各 6 本＝計 12 本）を追加する。

元 MQL4 の対応:
    ``DRAW_HISTOGRAM``（ExtBufferLevelCount のクランプ済みレベルカウント, DarkGreen,
    separate_window, height 100）と σ 水準線（StdDevArray[1..6]=上方 / [7..12]=下方,
    indicator_levelcolor C'84,84,84'）。

差分:
    profit_arctan/src/lwc_chart.py を踏襲。オシレーターのみ iVOLATILITY（49 系列）に
    置換し、MA 方式・bar_width 等のパラメータは持たない（period のみ）。σ12（上方 6 本 +
    下方 6 本＝12 本）を描く（``compute_sigma_levels`` が up_*/dn_* の 12 キーを返す）。

依存:
    標準: __future__, typing / 外部: numpy, pandas / プロジェクト内: src.volatility, src.core
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd

from common_view import LEVEL_LINE_WIDTH, level_colors  # noqa: E402

from .core import DEFAULT_PERIOD, DEFAULT_WINDOW
from .volatility import LEVEL_COUNT_COLUMN, build_volatility, volatility_levels

_COLOR = "rgba(0, 100, 0, 0.85)"        # DarkGreen
_LEVEL_COLOR = "rgba(84, 84, 84, 0.6)"  # 元 indicator_levelcolor C'84,84,84'

# 重畳する σ12 水準線（上方 6 本 + 下方 6 本）。
_LEVEL_KEYS: tuple[str, ...] = (
    "up_067", "up_128", "up_165", "up_196", "up_258", "up_329",
    "dn_067", "dn_128", "dn_165", "dn_196", "dn_258", "dn_329",
)


@runtime_checkable
class _Histogram(Protocol):
    def set(self, data: pd.DataFrame) -> None: ...


@runtime_checkable
class _Chart(Protocol):
    def create_histogram(self, name: str, **kwargs) -> _Histogram: ...
    def horizontal_line(self, price: float, **kwargs): ...


def _resolve_times(df: pd.DataFrame, time_column: str | None) -> pd.Series:
    """時刻系列を解決する（明示指定 > time 列 > date 列 > DatetimeIndex の順）。

    Raises:
        KeyError: 指定の時刻列が無い、または time/date/DatetimeIndex が解決できない場合。
    """
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


def add_volatility(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    period: int = DEFAULT_PERIOD,
    window: int | None = DEFAULT_WINDOW,
    time_column: str | None = None,
    color: str = _COLOR,
    draw_levels: bool = True,
) -> list:
    """chart に Volatility ヒストグラムと σ12 水準線を追加する。

    Args:
        chart: ``create_histogram(name, **kwargs)`` と ``horizontal_line(price, **kwargs)``
            を持つオブジェクト（duck typing。別ウィンドウの場合は subchart を渡す）。
        df: OHLC DataFrame（open/high/low/close 必須・大小不問）。
        period: 変化をとる足数（既定 6）。
        window: 標準化窓 W（因果ローリング。既定 120）。``None`` で全期間バッチ。
        time_column: 時刻列の明示指定（省略時は time/date/DatetimeIndex を探索）。
        color: ヒストグラム色（既定 DarkGreen）。
        draw_levels: True で σ12 水準線（上下各 6 本＝12 本）を水平線として追加。

    Returns:
        生成したオブジェクトのリスト（[histogram, *horizontal_lines]）。

    Raises:
        KeyError: 時刻が解決できない / 必須列（OHLC）が無い場合。
    """
    bands = build_volatility(df, period=period, window=window)
    times = _resolve_times(df, time_column)

    hist = chart.create_histogram(
        name=LEVEL_COUNT_COLUMN, color=color, price_line=False, price_label=False
    )
    # 値列名はヒストグラム名と完全一致させる（ガイド §5）。NaN は描画側で除外。
    # color 列で各バーを値ごと（緑→赤・|中心からの距離|（両極=買われすぎ/売られ過ぎ=過熱=赤））に着色する（per-point 上書き）。
    values = bands[LEVEL_COUNT_COLUMN].to_numpy()
    series = pd.DataFrame(
        {"time": times, LEVEL_COUNT_COLUMN: values, "color": level_colors(values)}
    ).dropna(subset=[LEVEL_COUNT_COLUMN])
    hist.set(series)

    created = [hist]
    if draw_levels:
        levels = volatility_levels(df, period=period, window=window)
        for key in _LEVEL_KEYS:
            created.append(chart.horizontal_line(
                price=float(levels[key]), color=_LEVEL_COLOR, width=LEVEL_LINE_WIDTH,
                style="dotted", text=key, axis_label_visible=False,
            ))
    return created
