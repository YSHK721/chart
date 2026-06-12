"""PRO!fit_Oscillator の成果物層（DataFrame 入出力アダプタ）。

層名/責務:
    成果物層。OHLCV を持つ pandas DataFrame を入力に取り、core 層
    （``compute_oscillator_full`` / ``compute_oscillator_levels``）を呼び出して、
    クランプ済みレベルカウント列を付与した DataFrame・σ12 水準辞書を返す。numpy 計算の
    偶有的詳細（18 系列集計順序・バッファ）は core に隠蔽され、本層は列抽出・index 継承のみを担う。

元 MQL4 対応:
    元 OnCalculate がチャートバッファ ``ExtBufferLevelCount`` へ書き込む「描画対象
    （クランプ済みレベルカウント）」を、Python では DataFrame の 1 列として返す。

依存:
    標準: sys, pathlib / 外部: pandas / プロジェクト内: src.core
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import (  # noqa: E402
    DEFAULT_PERIOD_A,
    DEFAULT_PERIOD_B,
    compute_oscillator_full,
)

# 成果物 DataFrame に付与する列名（クランプ済みレベルカウント）。
LEVEL_COUNT_COLUMN: str = "oscillator_lc"

# OHLCV の論理名 → DataFrame からの抽出キー（列名は大小不問）。volume を含む。
_OHLCV_KEYS: tuple[str, ...] = ("open", "high", "low", "close", "volume")


def _extract_ohlcv(df: pd.DataFrame) -> dict[str, "pd.Series"]:
    """DataFrame から O/H/L/C/Volume 列を大小不問で抽出する。

    Args:
        df: OHLCV を含む DataFrame。

    Returns:
        ``{"open": ..., "high": ..., "low": ..., "close": ..., "volume": ...}``。

    Raises:
        KeyError: 必須列（O/H/L/C/Volume いずれか）が欠落している場合。
    """
    lower_map = {str(col).lower(): col for col in df.columns}
    extracted: dict[str, pd.Series] = {}
    for key in _OHLCV_KEYS:
        if key not in lower_map:
            raise KeyError(f"必須列が見つかりません: {key}")
        extracted[key] = df[lower_map[key]]
    return extracted


def build_oscillator(
    df: pd.DataFrame,
    *,
    period_a: int = DEFAULT_PERIOD_A,
    period_b: int = DEFAULT_PERIOD_B,
) -> pd.DataFrame:
    """OHLCV DataFrame からクランプ済みレベルカウント列を付与した DataFrame を返す。

    Args:
        df: O/H/L/C/Volume 列（大小不問）を含む DataFrame。
        period_a: オシレーター期間（既定 6）。
        period_b: MARD 期間（既定 60）。

    Returns:
        ``LEVEL_COUNT_COLUMN`` 列を持つ DataFrame（元 index を継承）。

    Raises:
        KeyError: 必須列が欠落している場合。
    """
    ohlcv = _extract_ohlcv(df)
    res = compute_oscillator_full(
        ohlcv["open"].to_numpy(dtype=float),
        ohlcv["high"].to_numpy(dtype=float),
        ohlcv["low"].to_numpy(dtype=float),
        ohlcv["close"].to_numpy(dtype=float),
        ohlcv["volume"].to_numpy(dtype=float),
        period_a=period_a,
        period_b=period_b,
    )
    return pd.DataFrame(
        {LEVEL_COUNT_COLUMN: res.level_count_clamped},
        index=df.index,
    )


def oscillator_levels(
    df: pd.DataFrame,
    *,
    period_a: int = DEFAULT_PERIOD_A,
    period_b: int = DEFAULT_PERIOD_B,
) -> dict[str, float]:
    """OHLCV DataFrame から σ12 水準線辞書（up_*/dn_*）を返す。

    Args:
        df: O/H/L/C/Volume 列（大小不問）を含む DataFrame。
        period_a: オシレーター期間（既定 6）。
        period_b: MARD 期間（既定 60）。

    Returns:
        σ12 水準（``up_067``..``up_329`` / ``dn_067``..``dn_329``）の辞書。

    Raises:
        KeyError: 必須列が欠落している場合。
    """
    ohlcv = _extract_ohlcv(df)
    res = compute_oscillator_full(
        ohlcv["open"].to_numpy(dtype=float),
        ohlcv["high"].to_numpy(dtype=float),
        ohlcv["low"].to_numpy(dtype=float),
        ohlcv["close"].to_numpy(dtype=float),
        ohlcv["volume"].to_numpy(dtype=float),
        period_a=period_a,
        period_b=period_b,
    )
    return dict(res.levels)
