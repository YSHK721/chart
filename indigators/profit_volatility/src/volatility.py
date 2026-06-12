"""PRO!fit_Volatility の成果物層（DataFrame 入出力アダプタ）。

層名/責務:
    成果物層。OHLC を持つ pandas DataFrame を入力に取り、core 層
    （``compute_volatility_full`` / ``compute_volatility_levels``）を呼び出して、
    クランプ済みレベルカウント列を付与した DataFrame・σ12 水準辞書を返す。numpy 計算の
    偶有的詳細（49 系列・順序・バッファ）は core に隠蔽され、本層は列抽出・index 継承のみを
    担う。

元 MQL4 対応:
    元 OnCalculate がチャートバッファ ``ExtBufferLevelCount`` へ書き込む「描画対象
    （クランプ済みレベルカウント）」を、Python では DataFrame の 1 列として返す。

依存:
    標準: sys / 外部: numpy, pandas / プロジェクト内: src.core
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import (  # noqa: E402
    DEFAULT_PERIOD,
    compute_volatility_full,
)

# 成果物 DataFrame に付与する列名（クランプ済みレベルカウント）。
LEVEL_COUNT_COLUMN: str = "volatility_lc"

# OHLC の論理名 → DataFrame からの抽出キー（列名は大小不問）。
_OHLC_KEYS: tuple[str, ...] = ("open", "high", "low", "close")


def _extract_ohlc(df: pd.DataFrame) -> dict[str, "pd.Series"]:
    """DataFrame から O/H/L/C 列を大小不問で抽出する。

    Args:
        df: OHLC を含む DataFrame。

    Returns:
        ``{"open": ..., "high": ..., "low": ..., "close": ...}``。

    Raises:
        KeyError: 必須列（O/H/L/C いずれか）が欠落している場合。
    """
    lower_map = {str(col).lower(): col for col in df.columns}
    extracted: dict[str, pd.Series] = {}
    for key in _OHLC_KEYS:
        if key not in lower_map:
            raise KeyError(f"必須列が見つかりません: {key}")
        extracted[key] = df[lower_map[key]]
    return extracted


def build_volatility(
    df: pd.DataFrame,
    *,
    period: int = DEFAULT_PERIOD,
) -> pd.DataFrame:
    """OHLC DataFrame からクランプ済みレベルカウント列を付与した DataFrame を返す。

    Args:
        df: O/H/L/C 列（大小不問）を含む DataFrame。
        period: 乖離をとる足数（既定 6）。

    Returns:
        ``LEVEL_COUNT_COLUMN`` 列を持つ DataFrame（元 index を継承）。

    Raises:
        KeyError: 必須列が欠落している場合。
    """
    ohlc = _extract_ohlc(df)
    res = compute_volatility_full(
        ohlc["open"].to_numpy(dtype=float),
        ohlc["high"].to_numpy(dtype=float),
        ohlc["low"].to_numpy(dtype=float),
        ohlc["close"].to_numpy(dtype=float),
        period=period,
    )
    return pd.DataFrame(
        {LEVEL_COUNT_COLUMN: res.level_count_clamped},
        index=df.index,
    )


def volatility_levels(
    df: pd.DataFrame,
    *,
    period: int = DEFAULT_PERIOD,
) -> dict[str, float]:
    """OHLC DataFrame から σ12 水準線辞書（up_*/dn_*）を返す。

    Args:
        df: O/H/L/C 列（大小不問）を含む DataFrame。
        period: 乖離をとる足数（既定 6）。

    Returns:
        σ12 水準（``up_067``..``up_329`` / ``dn_067``..``dn_329``）の辞書。

    Raises:
        KeyError: 必須列が欠落している場合。
    """
    ohlc = _extract_ohlc(df)
    res = compute_volatility_full(
        ohlc["open"].to_numpy(dtype=float),
        ohlc["high"].to_numpy(dtype=float),
        ohlc["low"].to_numpy(dtype=float),
        ohlc["close"].to_numpy(dtype=float),
        period=period,
    )
    return dict(res.levels)
