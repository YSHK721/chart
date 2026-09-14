"""層名: 成果物層（pandas）。

責務:
    DataFrame から high/low/close/volume を小文字正規化抽出し、core 層
    （compute_mfimacd）を呼んで histogram/macd/signal の 3 列のみを付与した
    DataFrame（元 index 継承）と σ7水準辞書を返す薄い変換層。中間 mfi/fast/slow
    は描画不要のため列化しない。数値計算は core に委譲し、本層は列抽出・列名正規化
    ・必須列欠落例外の I/O 契約のみを担う。

元 MQL 対応:
    OnCalculate 全体の入出力境界（OHLCV → Histogram/MACD/Signal バッファ ＋
    σ7水準）。

依存:
    標準: __future__ / 外部: pandas, numpy / 同一パッケージ: core。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .core import (
    DEFAULT_FAST_EMA,
    DEFAULT_MFI_PERIOD,
    DEFAULT_SIGNAL_EMA,
    DEFAULT_SLOW_EMA,
    compute_mfimacd,
)
from marketdata.time_column import extract_columns as _extract_columns  # noqa: E402

# 出力列名（描画対象は histogram/macd/signal の 3 列のみ）。
HIST_COLUMN = "mfimacd_hist"
MACD_COLUMN = "mfimacd_macd"
SIGNAL_COLUMN = "mfimacd_signal"

# 抽出する必須入力列（小文字正規化後）。
_REQUIRED_COLUMNS = ("high", "low", "close", "volume")


def _extract_ohlcv(df: pd.DataFrame) -> tuple[np.ndarray, ...]:
    """必須列を抽出する（規則の単一源は marketdata.time_column.extract_columns）。"""
    return _extract_columns(df, _REQUIRED_COLUMNS)


def build_mfimacd(
    df: pd.DataFrame,
    *,
    mfi_period: int = DEFAULT_MFI_PERIOD,
    fast: int = DEFAULT_FAST_EMA,
    slow: int = DEFAULT_SLOW_EMA,
    signal: int = DEFAULT_SIGNAL_EMA,
) -> pd.DataFrame:
    """histogram/macd/signal の 3 列を付与した DataFrame（元 index 継承）を返す。

    中間 mfi/fast/slow は描画不要のため列化しない。

    Args:
        df: high/low/close/volume を含む DataFrame（列名の大小不問）。
        mfi_period: MFI 期間（既定 13）。
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
    result = compute_mfimacd(
        high, low, close, volume,
        mfi_period=mfi_period, fast=fast, slow=slow, signal=signal,
    )
    out = df.copy()
    out[HIST_COLUMN] = result.histogram
    out[MACD_COLUMN] = result.macd
    out[SIGNAL_COLUMN] = result.signal
    return out


def mfimacd_levels(
    df: pd.DataFrame,
    *,
    mfi_period: int = DEFAULT_MFI_PERIOD,
    fast: int = DEFAULT_FAST_EMA,
    slow: int = DEFAULT_SLOW_EMA,
    signal: int = DEFAULT_SIGNAL_EMA,
) -> dict[str, float]:
    """histogram 全系列の σ7水準辞書を返す。

    Args:
        df: high/low/close/volume を含む DataFrame（列名の大小不問）。
        mfi_period: MFI 期間（既定 13）。
        fast: FastEMA 期間（既定 4）。
        slow: SlowEMA 期間（既定 8）。
        signal: SignalEMA 期間（既定 4）。

    Returns:
        ``{"p1","p2","p3","m1","m2","m3","mid50"}``。

    Raises:
        KeyError: 必須列（volume 含む）欠落。
    """
    high, low, close, volume = _extract_ohlcv(df)
    result = compute_mfimacd(
        high, low, close, volume,
        mfi_period=mfi_period, fast=fast, slow=slow, signal=signal,
    )
    return result.levels
