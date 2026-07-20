"""出力アダプタ: lightweight-charts への btlm_trail_marod 系列追加（duck typing）。

層名/責務:
    出力アダプタ。``lightweight_charts`` を import せず、``create_line`` と
    ``horizontal_line`` を持つオブジェクト（chart）をダックタイピングで受ける
    （btlm_trail / profit_rsi の出力アダプタと同一様式）。MAROD（移動平均乖離率）を
    別 pane のオシレータ line 系列として供給し、0% 水平基準線を 1 本添える
    （±閾値線は対象外）。NaN（warm-up・未定義）は描画から除外する。

系列名（固定・F3 照合は catalog の SeriesDef 集合と突合）:
    - "btlm_trail_marod" : MAROD line 系列（別 pane オシレータ）。0% 基準線群 payload の
      name も compute_id（= "btlm_trail_marod"）に一致する（統合 FakeChart 規約）。

依存:
    標準: __future__, typing / 外部: numpy, pandas / プロジェクト内: core
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

import numpy as np
import pandas as pd

from .core import DEFAULT_MAXBARS, DEFAULT_SOURCE, marod_series

_SERIES_NAME = "btlm_trail_marod"
_COLOR_MAROD = "rgba(123, 104, 238, 1)"   # MediumSlateBlue（btlm_trail 系と同系色）
_BASELINE = 0.0                            # 0% 基準線（乖離ゼロ＝トレンド一致水準）
_LEVEL_COLOR = "rgba(84, 84, 84, 1)"       # 基準線色（profit_* 水準線と同系）
_LEVEL_WIDTH = 1


@runtime_checkable
class _Line(Protocol):
    def set(self, data: pd.DataFrame) -> None: ...


@runtime_checkable
class _Chart(Protocol):
    def create_line(self, name: str, **kwargs) -> _Line: ...
    def horizontal_line(self, price: float, **kwargs): ...


def _resolve_times(df: pd.DataFrame, time_column: Optional[str]) -> pd.Series:
    """時刻系列を解決する（明示指定 > time 列 > date 列 > DatetimeIndex の順）。

    btlm_trail/src/lwc_chart.py:44-58 の実装を踏襲する。
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


def add_btlm_trail_marod(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    source: str = DEFAULT_SOURCE,
    maxbars: int = DEFAULT_MAXBARS,
    time_column: Optional[str] = None,
    color: str = _COLOR_MAROD,
) -> list:
    """``chart`` へ MAROD line 系列と 0% 基準線を追加する（別 pane オシレータ）。

    Args:
        chart: ``create_line(name, **kwargs)`` と ``horizontal_line(price, **kwargs)``
            を持つオブジェクト（duck typing。別 pane の場合は subchart を渡す）。
        df: OHLC DataFrame（時刻は time / date / DatetimeIndex で解決）。
        source: 8 択ソース（既定 close）。maxbars: 回帰窓（既定 100・min 3）。
        time_column: 時刻列。color: MAROD 線の色。

    Returns:
        生成したオブジェクトのリスト（[marod_line, baseline_hline]）。

    Raises:
        ValueError: source / maxbars 不正時（btlm_trail core の契約）。
        KeyError: 時刻が解決できない場合。
    """
    values = marod_series(df, source=source, maxbars=maxbars)
    times = _resolve_times(df, time_column)

    created: list = []
    line = chart.create_line(
        name=_SERIES_NAME, color=color, style="solid", width=1,
        price_line=False, price_label=False,
    )
    series = pd.DataFrame(
        {"time": times, _SERIES_NAME: np.asarray(values, dtype=float)}
    ).dropna()
    line.set(series)
    created.append(line)

    # 0% 水平基準線（乖離ゼロ＝価格が OLS トレンドに一致する水準）。±閾値線は対象外。
    created.append(chart.horizontal_line(
        price=_BASELINE, color=_LEVEL_COLOR, width=_LEVEL_WIDTH,
        style="solid", text="0%", axis_label_visible=False,
    ))
    return created
