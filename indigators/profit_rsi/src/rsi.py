"""層名: 成果物層（pandas）。

責務:
    DataFrame から OHLC を小文字正規化抽出し、core 層（compute_rsi_full）を呼んで
    RSI 列を付与した DataFrame（元 index 継承）と σ 水準辞書を返す薄い変換層。
    数値計算と適用価格選択は core（共有 common 経由）に委譲し、本層は列抽出・列名
    正規化・必須列欠落例外の I/O 契約のみを担う。

元 MQL 対応:
    OnCalculate 全体の入出力境界（OHLC ＋ Apply → RSI バッファ ＋ σ 水準）。

依存:
    標準: __future__ / 外部: pandas, numpy
    同一パッケージ: core（compute_rsi_full, DEFAULT_*）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .core import (
    DEFAULT_APPLY,
    DEFAULT_RSI_PERIOD,
    compute_rsi_full,
)

# 出力列名。
RSI_COLUMN = "rsi"

# 抽出する必須入力列（小文字正規化後）。
_REQUIRED_COLUMNS = ("open", "high", "low", "close")


def _extract_ohlc(df: pd.DataFrame) -> tuple[np.ndarray, ...]:
    """DataFrame から open/high/low/close を小文字正規化して抽出する。

    Args:
        df: 入力 DataFrame（列名の大小不問）。

    Returns:
        ``(open, high, low, close)`` の float64 ndarray タプル。

    Raises:
        KeyError: open/high/low/close のいずれかが欠落している場合。
    """
    lower_map = {str(c).lower(): c for c in df.columns}
    missing = [c for c in _REQUIRED_COLUMNS if c not in lower_map]
    if missing:
        raise KeyError(f"必須列が欠落しています: {missing}")
    return tuple(
        df[lower_map[c]].to_numpy(dtype=np.float64) for c in _REQUIRED_COLUMNS
    )


def build_rsi(
    df: pd.DataFrame,
    *,
    rsi_period: int = DEFAULT_RSI_PERIOD,
    apply: int = DEFAULT_APPLY,
) -> pd.DataFrame:
    """RSI 列を付与した DataFrame（元 index 継承）を返す。

    Args:
        df: open/high/low/close を含む DataFrame（列名の大小不問）。
        rsi_period: RSI 期間（既定 6）。
        apply: 適用価格選択（既定 5 -> TYPICAL。core の APPLY_TO_PRICE 写像に従う）。

    Returns:
        ``RSI_COLUMN`` 列を付与した DataFrame（元 index 継承）。

    Raises:
        KeyError: 必須列欠落。
        ValueError: rsi_period<2 / OHLC 長不一致。
    """
    open_, high, low, close = _extract_ohlc(df)
    full = compute_rsi_full(
        open_, high, low, close,
        rsi_period=rsi_period, apply=apply,
    )
    out = df.copy()
    out[RSI_COLUMN] = full.rsi
    return out


def rsi_levels(
    df: pd.DataFrame,
    *,
    rsi_period: int = DEFAULT_RSI_PERIOD,
    apply: int = DEFAULT_APPLY,
) -> dict[str, float]:
    """生 RSI 系列全体の σ 水準辞書（7 水準）を返す。

    Args:
        df: open/high/low/close を含む DataFrame（列名の大小不問）。
        rsi_period: RSI 期間（既定 6）。
        apply: 適用価格選択（既定 5 -> TYPICAL）。

    Returns:
        ``{"p1","p2","p3","m1","m2","m3","mid50"}``。

    Raises:
        KeyError: 必須列欠落。
        ValueError: rsi_period<2 / OHLC 長不一致。
    """
    open_, high, low, close = _extract_ohlc(df)
    full = compute_rsi_full(
        open_, high, low, close,
        rsi_period=rsi_period, apply=apply,
    )
    return full.levels
