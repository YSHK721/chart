"""層名: 成果物層（pandas）。

責務:
    DataFrame から high/low/close/volume を小文字正規化抽出し、core 層
    （compute_rmmmacd）を呼んで histogram/macd/signal の 3 列のみを付与した
    DataFrame（元 index 継承）を返す薄い変換層。**σ 水準が無いため levels 関数は
    持たない**（rmmmacd_levels を作らない）。数値計算は core に委譲し、本層は列抽出・
    列名正規化・必須列欠落例外の I/O 契約のみを担う。

元 MQL 対応:
    OnCalculate 全体の入出力境界（OHLCV → Histogram/MACD/Signal バッファ）。
    σ 水準は出力しない（元は funIndicatorSet を呼ばず水準を出さない）。

依存:
    標準: __future__ / 外部: pandas, numpy / 同一パッケージ: core。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .core import (
    DEFAULT_FAST_EMA,
    DEFAULT_MA_PERIOD,
    DEFAULT_OSC_PERIOD,
    DEFAULT_SIGNAL_EMA,
    DEFAULT_SLOW_EMA,
    compute_rmmmacd,
)

# 出力列名（描画対象は histogram/macd/signal の 3 列のみ）。
HIST_COLUMN = "rmmmacd_hist"
MACD_COLUMN = "rmmmacd_macd"
SIGNAL_COLUMN = "rmmmacd_signal"

# 抽出する必須入力列（小文字正規化後）。
_REQUIRED_COLUMNS = ("high", "low", "close", "volume")


def _extract_ohlcv(df: pd.DataFrame) -> tuple[np.ndarray, ...]:
    """DataFrame から high/low/close/volume を小文字正規化して抽出する。

    Args:
        df: 入力 DataFrame（列名の大小不問）。

    Returns:
        ``(high, low, close, volume)`` の float64 ndarray タプル。

    Raises:
        KeyError: high/low/close/volume のいずれかが欠落している場合。
    """
    lower_map = {str(c).lower(): c for c in df.columns}
    missing = [c for c in _REQUIRED_COLUMNS if c not in lower_map]
    if missing:
        raise KeyError(f"必須列が欠落しています: {missing}")
    return tuple(
        df[lower_map[c]].to_numpy(dtype=np.float64) for c in _REQUIRED_COLUMNS
    )


def build_rmmmacd(
    df: pd.DataFrame,
    *,
    osc_period: int = DEFAULT_OSC_PERIOD,
    ma_period: int = DEFAULT_MA_PERIOD,
    fast: int = DEFAULT_FAST_EMA,
    slow: int = DEFAULT_SLOW_EMA,
    signal: int = DEFAULT_SIGNAL_EMA,
) -> pd.DataFrame:
    """histogram/macd/signal の 3 列を付与した DataFrame（元 index 継承）を返す。

    σ 水準は出力しない（levels 列・levels 関数を作らない）。

    Args:
        df: high/low/close/volume を含む DataFrame（列名の大小不問）。
        osc_period: オシレーター期間（既定 6）。
        ma_period: EMA 期間（既定 6）。
        fast: FastEMA 期間（既定 4）。
        slow: SlowEMA 期間（既定 8）。
        signal: SignalEMA 期間（既定 4）。

    Returns:
        ``HIST_COLUMN`` / ``MACD_COLUMN`` / ``SIGNAL_COLUMN`` 列を付与した
        DataFrame（元 index 継承）。

    Raises:
        KeyError: 必須列（volume 含む）欠落。
    """
    high, low, close, volume = _extract_ohlcv(df)
    result = compute_rmmmacd(
        high, low, close, volume,
        osc_period=osc_period, ma_period=ma_period,
        fast=fast, slow=slow, signal=signal,
    )
    out = df.copy()
    out[HIST_COLUMN] = result.histogram
    out[MACD_COLUMN] = result.macd
    out[SIGNAL_COLUMN] = result.signal
    return out
