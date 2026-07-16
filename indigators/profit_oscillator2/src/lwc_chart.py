"""層名: 出力アダプタ（lightweight-charts への系列追加・duck typing）。

責務:
    出力アダプタ。``lightweight_charts`` を import せず、``create_histogram`` /
    ``create_line`` / ``horizontal_line`` を持つオブジェクト（chart）をダックタイピング
    で受ける（PORTING_GUIDE §2/§6）。指標パッケージの依存を numpy/pandas に保ち、
    具体描画ライブラリを内側（core/成果物層）へ侵入させない。元 MQL4 は別ウィンドウの
    レベルカウント・ヒストグラム 1 本（ExtBufferLevelCount, DarkGreen, DRAW_HISTOGRAM）
    ＋ RCI 線 1 本（ExtBufferRCI, clrLime, DRAW_LINE）＋ σ6 水準線（グレー）であるため、
    呼び出し側が用意した（サブ）チャートにヒストグラム 1 本・ライン 1 本・水平線 6 本を
    追加する。subwindow 範囲は sub_min〜sub_max（LC クランプ無し・SPEC §7）。

元 MQL4 対応:
    ``PRO!fitOscillator.mq4`` の ``SetIndexStyle(0, DRAW_HISTOGRAM)``
    （ExtBufferLevelCount, indicator_color1 DarkGreen, indicator_width1 2）と
    ``SetIndexStyle(1, DRAW_LINE)``（ExtBufferRCI, indicator_color2 clrLime）、
    σ6 水準線（up_165/up_196/up_258/dn_165/dn_196/dn_258, グレー C'84,84,84',
    indicator_levelstyle STYLE_SOLID）。

依存:
    標準: __future__, typing / 外部: numpy, pandas / プロジェクト内: oscillator2
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd

from common_view import LEVEL_LINE_WIDTH, level_colors  # noqa: E402

from .oscillator2 import (
    LEVEL_COUNT_COLUMN,
    RCI_COLUMN,
    build_oscillator2,
    oscillator2_levels,
)

_HIST_COLOR = "rgba(0, 100, 0, 0.85)"   # 元 indicator_color1 DarkGreen（レベルカウント）
_RCI_COLOR = "rgba(0, 255, 0, 1)"       # 元 indicator_color2 clrLime（RCI 線）
_WIDTH = 1
_LEVEL_COLOR = "rgba(84, 84, 84, 0.6)"  # 元 σ6 水準線 グレー C'84,84,84'

# 重畳する σ6 水準線（up 3 本 + dn 3 本）。oscillator2_levels のキーと一致。
_LEVEL_KEYS: tuple[str, ...] = ("up_165", "up_196", "up_258", "dn_165", "dn_196", "dn_258")


@runtime_checkable
class _Series(Protocol):
    def set(self, data: pd.DataFrame) -> None: ...


@runtime_checkable
class _Chart(Protocol):
    def create_histogram(self, name: str, **kwargs) -> _Series: ...
    def create_line(self, name: str, **kwargs) -> _Series: ...
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


def add_oscillator2(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    osc_period: int = 6,
    stc_slow: int = 6,
    ma_period: int = 60,
    rci_period: int = 12,
    direction: bool = False,
    time_column: str | None = None,
    draw_levels: bool = True,
) -> list:
    """chart にレベルカウント・ヒストグラム（1 本）・RCI 線（1 本）と σ6 水準線（6 本）を追加する。

    Args:
        chart: ``create_histogram(name, **kwargs)`` / ``create_line(name, **kwargs)`` /
            ``horizontal_line(price, **kwargs)`` を持つオブジェクト（duck typing。別
            ウィンドウの場合は subchart を渡す）。
        df: OHLCV DataFrame（open/high/low/close/**volume** 必須・列名大小不問）。
        osc_period: オシレーター期間（既定 6。元 inpPeriodOscillator）。
        stc_slow: iStochastic slowing ＝ D 期間（既定 6。元 inpPeriodSTC_SLOW）。
        ma_period: MAROD の EMA 期間（既定 60。元 inpPeriodMA）。
        rci_period: RCI 期間（既定 12。元 inpPeriodRCI）。
        direction: RCI ソート方向（既定 False。元 direction）。
        time_column: 時刻列の明示指定（省略時は time/date/DatetimeIndex を探索）。
        draw_levels: True で σ6 水準線（6 本）を水平線として追加。

    Returns:
        生成したオブジェクトのリスト（[histogram, line, *horizontal_lines]）。

    Raises:
        KeyError: 必須列（open/high/low/close/volume）欠落、または時刻が解決できない場合。
        ValueError: osc_period<2 / ma_period<2 / 長不一致（build_oscillator2 経由）。
    """
    # 時刻解決を先に行い、時刻欠落を計算前に KeyError として確定させる。
    times = _resolve_times(df, time_column)
    built = build_oscillator2(
        df,
        osc_period=osc_period,
        stc_slow=stc_slow,
        ma_period=ma_period,
        rci_period=rci_period,
        direction=direction,
    )

    # レベルカウント・ヒストグラム 1 本（元 DRAW_HISTOGRAM, DarkGreen）。
    # 値列名はヒスト名と完全一致させる（ガイド §5）。多数系列のため price フラグ False（§6）。
    hist = chart.create_histogram(
        name=LEVEL_COUNT_COLUMN, color=_HIST_COLOR,
        price_line=False, price_label=False,
    )
    # color 列で各バーを値ごと（緑→赤・|中心からの距離|（両極=買われすぎ/売られ過ぎ=過熱=赤））に着色する（per-point 上書き）。
    hist_values = built[LEVEL_COUNT_COLUMN].to_numpy()
    hist.set(pd.DataFrame(
        {"time": times, LEVEL_COUNT_COLUMN: hist_values,
         "color": level_colors(hist_values)}
    ))

    # RCI 線 1 本（元 DRAW_LINE, clrLime）。値列名はライン名と完全一致させる。
    line = chart.create_line(
        name=RCI_COLUMN, color=_RCI_COLOR, style="solid", width=_WIDTH,
        price_line=False, price_label=False,
    )
    line.set(pd.DataFrame(
        {"time": times, RCI_COLUMN: built[RCI_COLUMN].to_numpy()}
    ))

    created = [hist, line]
    if draw_levels:
        levels = oscillator2_levels(
            df,
            osc_period=osc_period,
            stc_slow=stc_slow,
            ma_period=ma_period,
            rci_period=rci_period,
            direction=direction,
        )
        for key in _LEVEL_KEYS:
            created.append(chart.horizontal_line(
                price=float(levels[key]), color=_LEVEL_COLOR, width=LEVEL_LINE_WIDTH,
                style="solid", text=key, axis_label_visible=False,
            ))
    return created
