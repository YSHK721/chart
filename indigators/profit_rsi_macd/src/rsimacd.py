"""層名: 成果物層（pandas）。

責務:
    DataFrame から high/low/close を小文字正規化抽出し、core 層（compute_rsimacd）を
    呼んで histogram/macd/signal の 3 列のみを付与した DataFrame（元 index 継承）と
    σ7水準辞書を返す薄い変換層。中間 rsi/fast/slow は描画不要のため列化しない。
    数値計算は core に委譲し、本層は列抽出・列名正規化・必須列欠落例外の I/O 契約のみ
    を担う。価格は PRICE_TYPICAL 固定のため high/low/close のみ必須（open 不要）。

元 MQL 対応:
    OnCalculate 全体の入出力境界（OHLC → Histogram/MACD/Signal バッファ ＋ σ7水準）。

依存:
    標準: __future__ / 外部: pandas, numpy / 同一パッケージ: core。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .core import (
    DEFAULT_FAST_EMA,
    DEFAULT_RSI_PERIOD,
    DEFAULT_SIGNAL_EMA,
    DEFAULT_SLOW_EMA,
    compute_rsimacd,
)

# 出力列名（描画対象は histogram/macd/signal の 3 列のみ）。
HIST_COLUMN = "rsimacd_hist"
MACD_COLUMN = "rsimacd_macd"
SIGNAL_COLUMN = "rsimacd_signal"

# 抽出する必須入力列（小文字正規化後）。価格 Typical 固定のため open は不要。
_REQUIRED_COLUMNS = ("high", "low", "close")


def _extract_hlc(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """DataFrame から high/low/close を小文字正規化して抽出する。

    Args:
        df: 入力 DataFrame（列名の大小不問）。

    Returns:
        ``(high, low, close)`` の float64 ndarray タプル。

    Raises:
        KeyError: high/low/close のいずれかが欠落している場合。
    """
    lower_map = {str(c).lower(): c for c in df.columns}
    missing = [c for c in _REQUIRED_COLUMNS if c not in lower_map]
    if missing:
        raise KeyError(f"必須列が欠落しています: {missing}")
    high, low, close = (
        df[lower_map[c]].to_numpy(dtype=np.float64) for c in _REQUIRED_COLUMNS
    )
    return high, low, close


def build_rsimacd(
    df: pd.DataFrame,
    *,
    rsi_period: int = DEFAULT_RSI_PERIOD,
    fast: int = DEFAULT_FAST_EMA,
    slow: int = DEFAULT_SLOW_EMA,
    signal: int = DEFAULT_SIGNAL_EMA,
) -> pd.DataFrame:
    """histogram/macd/signal の 3 列を付与した DataFrame（元 index 継承）を返す。

    中間 rsi/fast/slow は描画不要のため列化しない。価格は PRICE_TYPICAL 固定。

    Args:
        df: high/low/close を含む DataFrame（列名の大小不問）。
        rsi_period: RSI 期間（既定 13）。
        fast: FastEMA 期間（既定 4）。
        slow: SlowEMA 期間（既定 8）。
        signal: SignalEMA 期間（既定 4）。

    Returns:
        ``HIST_COLUMN`` / ``MACD_COLUMN`` / ``SIGNAL_COLUMN`` 列を付与した
        DataFrame（元 index 継承）。

    Raises:
        KeyError: 必須列（high/low/close）欠落。
    """
    high, low, close = _extract_hlc(df)
    # open は Typical 固定で未使用。長さ整合用に high を仮置き（計算に影響しない）。
    result = compute_rsimacd(
        high, high, low, close,
        rsi_period=rsi_period, fast=fast, slow=slow, signal=signal,
    )
    out = df.copy()
    out[HIST_COLUMN] = result.histogram
    out[MACD_COLUMN] = result.macd
    out[SIGNAL_COLUMN] = result.signal
    return out


def rsimacd_levels(
    df: pd.DataFrame,
    *,
    rsi_period: int = DEFAULT_RSI_PERIOD,
    fast: int = DEFAULT_FAST_EMA,
    slow: int = DEFAULT_SLOW_EMA,
    signal: int = DEFAULT_SIGNAL_EMA,
) -> dict[str, float]:
    """histogram 全系列の σ7水準辞書を返す。

    Args:
        df: high/low/close を含む DataFrame（列名の大小不問）。
        rsi_period: RSI 期間（既定 13）。
        fast: FastEMA 期間（既定 4）。
        slow: SlowEMA 期間（既定 8）。
        signal: SignalEMA 期間（既定 4）。

    Returns:
        ``{"p1","p2","p3","m1","m2","m3","mid50"}``。

    Raises:
        KeyError: 必須列（high/low/close）欠落。
    """
    high, low, close = _extract_hlc(df)
    result = compute_rsimacd(
        high, high, low, close,
        rsi_period=rsi_period, fast=fast, slow=slow, signal=signal,
    )
    return result.levels
