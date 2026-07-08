"""層名: 成果物層（pandas）。

責務:
    DataFrame から high/low/close/volume を小文字正規化抽出し、core 層
    （compute_mfi_full / compute_mfi_levels）を呼んで MFI 列・MA 列を付与した
    DataFrame（元 index 継承）と σ 水準辞書を返す薄い変換層。数値計算は core に
    委譲し、本層は列抽出・列名正規化・必須列欠落例外の I/O 契約のみを担う。

元 MQL 対応:
    OnCalculate 全体の入出力境界（OHLCV → MFI/MA バッファ ＋ σ 水準）。

依存:
    標準: __future__ / 外部: pandas, numpy / 同一パッケージ: core。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .core import (
    DEFAULT_MA_PERIOD,
    DEFAULT_MFI_PERIOD,
    compute_mfi_full,
)

# 出力列名。
MFI_COLUMN = "mfi"
MA_COLUMN = "mfi_ma"

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


def build_mfi(
    df: pd.DataFrame,
    *,
    mfi_period: int = DEFAULT_MFI_PERIOD,
    ma_period: int = DEFAULT_MA_PERIOD,
) -> pd.DataFrame:
    """MFI 列・MA 列を付与した DataFrame（元 index 継承）を返す。

    Args:
        df: high/low/close/volume を含む DataFrame（列名の大小不問）。
        mfi_period: MFI 期間（既定 14）。
        ma_period: EMA 期間（既定 5）。

    Returns:
        ``MFI_COLUMN`` / ``MA_COLUMN`` 列を付与した DataFrame（元 index 継承）。

    Raises:
        KeyError: 必須列（volume 含む）欠落。
    """
    high, low, close, volume = _extract_ohlcv(df)
    full = compute_mfi_full(
        high, low, close, volume, mfi_period=mfi_period, ma_period=ma_period
    )
    out = df.copy()
    out[MFI_COLUMN] = full.mfi
    out[MA_COLUMN] = full.ma
    return out


def mfi_levels(
    df: pd.DataFrame,
    *,
    mfi_period: int = DEFAULT_MFI_PERIOD,
    ma_period: int = DEFAULT_MA_PERIOD,
) -> dict[str, float]:
    """EMA 系列全体の σ 水準辞書（7 水準）を返す。

    Args:
        df: high/low/close/volume を含む DataFrame（列名の大小不問）。
        mfi_period: MFI 期間（既定 14）。
        ma_period: EMA 期間（既定 5）。

    Returns:
        ``{"p1","p2","p3","m1","m2","m3","mid50"}``。

    Raises:
        KeyError: 必須列（volume 含む）欠落。
    """
    high, low, close, volume = _extract_ohlcv(df)
    full = compute_mfi_full(
        high, low, close, volume, mfi_period=mfi_period, ma_period=ma_period
    )
    return full.levels
