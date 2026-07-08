"""層名: 出力アダプタ（lightweight-charts への系列追加・duck typing）。

責務:
    出力アダプタ。``lightweight_charts`` を import せず、``create_histogram`` /
    ``create_line`` を持つオブジェクト（chart）をダックタイピングで受ける
    （PORTING_GUIDE §2/§6）。指標パッケージの依存を numpy/pandas に保つ。元 MQL4 は
    別ウィンドウ（MACD 型・[0,100] 制約なし）のヒストグラム 1 本（MacdHistogram,
    DRAW_HISTOGRAM, C'133,219,24'）＋ ライン 2 本（RMMWMACD ＝ C'205,232,65',
    Signal ＝ C'167,197,32', 共に DRAW_LINE）であるため、呼び出し側が用意した
    （サブ）チャートにヒストグラム 1 本・ライン 2 本を追加する。

    **本指標は σ 水準線を持たない**（元 ``funIndicatorSet`` を OnCalculate で呼ばず
    水準を出力しない）。MFIMACD/RSIMACD の先例にある ``horizontal_line`` ／
    ``draw_levels`` は本指標では実装しない（水平線 0 本）。

元 MQL4 対応:
    ``SetIndexStyle(0, DRAW_HISTOGRAM)``（MacdHistogramBuffer, indicator_color1
    C'133,219,24', label "MacdHistogram"）/ ``SetIndexStyle(1, DRAW_LINE)``
    （MacdBuffer, label "RMMWMACD", indicator_color2 C'205,232,65'）/
    ``SetIndexStyle(2, DRAW_LINE)``（SignalBuffer, label "Signal", indicator_color3
    C'167,197,32'）。水準線（INDICATOR_LEVELS / funIndicatorSet）は OnCalculate から
    呼ばれず出力されない。

依存（PORTING_GUIDE §8）:
    標準: __future__, typing / 外部: numpy, pandas / プロジェクト内: rmmmacd, core
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd

from .core import (
    DEFAULT_FAST_EMA,
    DEFAULT_MA_PERIOD,
    DEFAULT_OSC_PERIOD,
    DEFAULT_SIGNAL_EMA,
    DEFAULT_SLOW_EMA,
    DEFAULT_WINDOW,
)
from .rmmmacd import (
    HIST_COLUMN,
    MACD_COLUMN,
    SIGNAL_COLUMN,
    build_rmmmacd,
)

# ライン名（元 SetIndexLabel: MacdBuffer="RMMWMACD", SignalBuffer="Signal"）。
# 値列名はライン名と完全一致させる（PORTING_GUIDE §5）。
MACD_LINE_NAME = "RMMWMACD"
SIGNAL_LINE_NAME = "Signal"

_HIST_COLOR = "rgba(133, 219, 24, 0.85)"   # 元 indicator_color1 C'133,219,24'
_MACD_COLOR = "rgba(205, 232, 65, 1)"      # 元 indicator_color2 C'205,232,65'
_SIGNAL_COLOR = "rgba(167, 197, 32, 1)"    # 元 indicator_color3 C'167,197,32'
_WIDTH = 1                                 # 元 indicator_width 未指定（既定 1）


@runtime_checkable
class _Series(Protocol):
    def set(self, data: pd.DataFrame) -> None: ...


@runtime_checkable
class _Chart(Protocol):
    def create_histogram(self, name: str, **kwargs) -> _Series: ...
    def create_line(self, name: str, **kwargs) -> _Series: ...


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


def add_rmmmacd(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    osc_period: int = DEFAULT_OSC_PERIOD,
    ma_period: int = DEFAULT_MA_PERIOD,
    fast: int = DEFAULT_FAST_EMA,
    slow: int = DEFAULT_SLOW_EMA,
    signal: int = DEFAULT_SIGNAL_EMA,
    window: int | None = DEFAULT_WINDOW,
    time_column: str | None = None,
) -> list:
    """chart に RMMMACD のヒストグラム 1 本・ライン 2 本を追加する（水準線なし）。

    **σ 水準線は引かない**（元は水準を出力しない）。``horizontal_line`` は呼ばない。

    Args:
        chart: ``create_histogram(name, **kwargs)`` / ``create_line(name, **kwargs)``
            を持つオブジェクト（duck typing。別ウィンドウの場合は subchart を渡す）。
        df: OHLCV DataFrame（high/low/close/**volume** 必須・列名大小不問）。
        osc_period: オシレーター期間（既定 6。元 inpOscillatorPeriod）。
        ma_period: EMA 期間（既定 6。元 inpMovingAveragePeriod）。
        fast: FastEMA 期間（既定 4）。
        slow: SlowEMA 期間（既定 8）。
        signal: SignalEMA 期間（既定 4）。
        window: 標準化窓 W（既定 120＝因果。None で全期間バッチ）。
        time_column: 時刻列の明示指定（省略時は time/date/DatetimeIndex を探索）。

    Returns:
        生成したオブジェクトのリスト（``[histogram, macd_line, signal_line]``）。

    Raises:
        KeyError: 時刻が解決できない / HLC・volume 列が無い場合。
    """
    built = build_rmmmacd(
        df, osc_period=osc_period, ma_period=ma_period,
        fast=fast, slow=slow, signal=signal, window=window,
    )
    times = _resolve_times(df, time_column)

    created: list = []

    # ヒストグラム 1 本（MacdHistogram, DRAW_HISTOGRAM）。値列名はヒストグラム名と
    # 完全一致させる（§5）。多数系列のため price_line/label は False（§6）。
    hist = chart.create_histogram(
        name=HIST_COLUMN, color=_HIST_COLOR,
        price_line=False, price_label=False,
    )
    # warm-up NaN（先頭 window-1）は非描画。lightweight-charts へ NaN を渡さず
    # dropna で除外して有限値のみ set する（姉妹 profit_rmm/adx_needle/arctan/
    # oscillator と整合）。値列名はヒストグラム名と完全一致させる（§5）。
    hist_df = pd.DataFrame(
        {"time": times, HIST_COLUMN: built[HIST_COLUMN].to_numpy()}
    ).dropna(subset=[HIST_COLUMN])
    hist.set(hist_df)
    created.append(hist)

    # ライン 2 本（RMMWMACD / Signal, DRAW_LINE）。値列名はライン名と完全一致させる（§5）。
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
        # warm-up NaN（先頭 window-1）はヒストグラムと同様 dropna で除外して有限値のみ set する
        # （姉妹 profit_rmm/adx_needle/arctan/oscillator・仕様§1.3 と整合）。dropna しないと
        # iterrows が「datetime の time 列＋NaN 値列」の warm-up 行を datetime64 と推論し NaN を
        # NaT へ強制変換 → 描画側 float(NaT) が TypeError で落ちる（既定 window=120 で発火）。
        line_df = pd.DataFrame(
            {"time": times, line_name: built[value_col].to_numpy()}
        ).dropna(subset=[line_name])
        line.set(line_df)
        created.append(line)

    # σ 水準線は無い（元 funIndicatorSet 未呼出）。horizontal_line は呼ばない。
    return created
