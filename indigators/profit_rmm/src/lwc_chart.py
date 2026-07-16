"""出力アダプタ: lightweight-charts への系列追加（duck typing）。

層名/責務:
    出力アダプタ。``lightweight_charts`` を import せず、``create_histogram`` /
    ``horizontal_line`` を持つオブジェクト（chart）をダックタイピングで受ける。
    指標パッケージの依存を numpy/pandas に保ち、具体描画ライブラリを内側（core/
    成果物層）へ侵入させない。元 MQL4 は別ウィンドウ [-10,10] のヒストグラム 1 本
    （ExtBufferLevelCount, clrLime）+ σ6 水準線（グレー）であるため、呼び出し側が
    用意した（サブ）チャートにヒストグラム系列と水平線 6 本を追加する。

元 MQL4 対応:
    ``PRO!fitRMM.mq4`` の ``DRAW_HISTOGRAM``（ExtBufferLevelCount, color clrLime,
    separate_window, indicator_minimum/maximum=[-10,10]）と σ6 水準線
    （up_1s/up_2s/up_3s/dn_1s/dn_2s/dn_3s, グレー C'84,84,84'）。

依存:
    標準: __future__, typing / 外部: numpy, pandas / プロジェクト内: rmm, core
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol, runtime_checkable

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root → common
from common_view import LEVEL_LINE_WIDTH, level_colors  # noqa: E402

from . import core
from .rmm import LEVEL_COUNT_COLUMN, build_rmm, rmm_levels

_COLOR = "rgba(0, 255, 0, 0.85)"        # 元 indicator_color1 clrLime
_LEVEL_COLOR = "rgba(84, 84, 84, 0.6)"  # 元 σ6 水準線 グレー C'84,84,84'

# 重畳する σ6 水準線（up 3 本 + dn 3 本）。core.compute_rmm_levels のキーと一致。
_LEVEL_KEYS: tuple[str, ...] = ("up_1s", "up_2s", "up_3s", "dn_1s", "dn_2s", "dn_3s")


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
        KeyError: 指定の時刻列が無い、または time/date 列・DatetimeIndex で解決できない場合。
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


def add_rmm(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    osc_period: int = core.DEFAULT_OSC_PERIOD,
    ma_period: int = core.DEFAULT_MA_PERIOD,
    window: int | None = core.DEFAULT_WINDOW,
    time_column: str | None = None,
    color: str = _COLOR,
    draw_levels: bool = True,
) -> list:
    """chart に RMM レベルカウント・ヒストグラムと σ6 水準線を追加する。

    Args:
        chart: ``create_histogram(name, **kwargs)`` と ``horizontal_line(price, **kwargs)``
            を持つオブジェクト（duck typing。別ウィンドウの場合は subchart を渡す）。
        df: OHLCV DataFrame（high/low/close/volume 必須・列名大小不問）。
        osc_period: オシレーター期間（既定 6）。
        ma_period: EMA 期間（既定 6）。
        time_column: 時刻列の明示指定（省略時は time/date/DatetimeIndex を探索）。
        color: ヒストグラム色（既定 clrLime）。
        draw_levels: True で σ6 水準線（6 本）を水平線として追加。

    Returns:
        生成したオブジェクトのリスト（[histogram, *horizontal_lines]）。

    Raises:
        KeyError: 必須列（high/low/close/volume）欠落、または時刻が解決できない場合。
        ValueError: osc_period<2、または抽出系列長不一致（build_rmm 経由）。
    """
    bands = build_rmm(df, osc_period=osc_period, ma_period=ma_period, window=window)
    times = _resolve_times(df, time_column)

    hist = chart.create_histogram(
        name=LEVEL_COUNT_COLUMN, color=color, price_line=False, price_label=False
    )
    # 値列名はヒストグラム名と完全一致させる。NaN は描画側で除外。
    # color 列で各バーを値ごと（緑→赤・|中心からの距離|（両極=買われすぎ/売られ過ぎ=過熱=赤））に着色する（per-point 上書き）。
    values = bands[LEVEL_COUNT_COLUMN].to_numpy()
    series = pd.DataFrame(
        {"time": times, LEVEL_COUNT_COLUMN: values, "color": level_colors(values)}
    ).dropna(subset=[LEVEL_COUNT_COLUMN])
    hist.set(series)

    created = [hist]
    if draw_levels:
        levels = rmm_levels(df, osc_period=osc_period, ma_period=ma_period, window=window)
        for key in _LEVEL_KEYS:
            created.append(
                chart.horizontal_line(
                    price=float(levels[key]), color=_LEVEL_COLOR, width=LEVEL_LINE_WIDTH,
                    style="solid", text=key, axis_label_visible=False,
                )
            )
    return created
