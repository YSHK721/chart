"""出力アダプタ — lightweight-charts へ期間高安 / 年初来高安の系列を追加する。

層名/責務:
    出力アダプタ。``chart.create_line(name=...)`` を持つオブジェクト（実描画 / API 経路の
    FakeChart）へ系列を追加する。時刻解決・NaN 除外・``set`` は共有プリミティブ
    :mod:`common_view.lwc_adapter`（``resolve_times`` / ``emit_line``）へ委譲し、
    暦年の畳み込みは純粋ライブラリ :mod:`core` へ委譲する。

系列名（固定・F3 照合は catalog の静的 SeriesDef 集合と突合）:
    - ``add_period_hl``: "period_hl_hi" / "period_hl_lo"
        値は各バーの high / low **そのもの**。最終バーは形成中足なので、末尾値＝
        「その時間足の進行中期間の走行高安」になる（期間境界はロールアップのグリッド
        ＝marketdata が唯一源。本層は境界の定義を持たない）。
    - ``add_ytd_hl``: "ytd_hl_hi" / "ytd_hl_lo"
        値は暦年内の走行 max / min（core.ytd_running_extremes）。窓に現れる最初の年は
        年初被覆を保証できないため NaN（描画からは除外される・無言で誤った年初来を出さない）。
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from common_view.lwc_adapter import emit_line, resolve_times

from .core import ytd_running_extremes

# 系列の描画色（上側=抵抗=赤系 / 下側=支持=緑系。price_range_power の bull/bear と同系統）。
_COLOR_HI = "rgba(210, 67, 58, 0.9)"
_COLOR_LO = "rgba(46, 158, 91, 0.9)"


def _hl_columns(df: pd.DataFrame) -> "tuple[np.ndarray, np.ndarray]":
    """high / low 列を float 配列で返す（列名は大小不問）。無ければ ValueError。"""
    lower = {str(c).lower(): c for c in df.columns}
    for name in ("high", "low"):
        if name not in lower:
            raise ValueError(f"計算に必要な列がありません: {name}")
    return (
        df[lower["high"]].to_numpy(dtype=np.float64),
        df[lower["low"]].to_numpy(dtype=np.float64),
    )


def add_period_hl(
    chart, df: pd.DataFrame, *, time_column: Optional[str] = None
) -> "dict[str, object]":
    """``chart`` へ各バーの high / low の系列（期間内の走行高安）を追加する。

    Args:
        chart: ``create_line(name=...)`` を持つオブジェクト。
        df: OHLC DataFrame（時刻は time / date 列 / DatetimeIndex で解決）。
        time_column: 時刻列の明示指定。

    Returns:
        ``{series_name: Line}``（"period_hl_hi" / "period_hl_lo"）。
    """
    times = resolve_times(df, time_column)
    high, low = _hl_columns(df)
    return {
        "period_hl_hi": emit_line(chart, "period_hl_hi", times, high, _COLOR_HI, "solid"),
        "period_hl_lo": emit_line(chart, "period_hl_lo", times, low, _COLOR_LO, "solid"),
    }


def add_ytd_hl(
    chart, df: pd.DataFrame, *, time_column: Optional[str] = None
) -> "dict[str, object]":
    """``chart`` へ年初来（暦年内）の走行高安の系列を追加する。

    Args:
        chart: ``create_line(name=...)`` を持つオブジェクト。
        df: OHLC DataFrame（時刻は time / date 列 / DatetimeIndex で解決）。
        time_column: 時刻列の明示指定。

    Returns:
        ``{series_name: Line}``（"ytd_hl_hi" / "ytd_hl_lo"）。窓の最初の年は NaN
        ＝描画から除外される（窓が年境界を覆わないときは系列が空になる）。
    """
    times = resolve_times(df, time_column)
    high, low = _hl_columns(df)
    years = pd.DatetimeIndex(times).year.to_numpy()
    hi, lo = ytd_running_extremes(years, high, low)
    return {
        "ytd_hl_hi": emit_line(chart, "ytd_hl_hi", times, hi, _COLOR_HI, "dashed"),
        "ytd_hl_lo": emit_line(chart, "ytd_hl_lo", times, lo, _COLOR_LO, "dashed"),
    }
