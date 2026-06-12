"""PRO!fit_Arctan の成果物層（DataFrame 入出力アダプタ）。

層名/責務:
    成果物層。OHLC を持つ pandas DataFrame を入力に取り、core 層
    （``compute_arctan_full`` / ``compute_arctan_levels``）を呼び出して、クランプ済み
    レベルカウント列を付与した DataFrame・σ12 水準辞書を返す。numpy 計算の偶有的詳細
    （バッファ・順序）は core に隠蔽され、本層は列抽出・index 継承のみを担う。

元 MQL4 対応:
    元 OnCalculate がチャートバッファへ書き込む「描画対象（クランプ済みレベルカウント）」を、
    Python では DataFrame の 1 列として返す。

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
    compute_arctan_full,
)

# 成果物 DataFrame に付与する列名（クランプ済みレベルカウント）。
LEVEL_COUNT_COLUMN: str = "arctan_lc"

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


def build_arctan(
    df: pd.DataFrame,
    *,
    period: int = DEFAULT_PERIOD,
    ma_method: int = 1,
    bar_width: float = 0.1,
) -> pd.DataFrame:
    """OHLC DataFrame からクランプ済みレベルカウント列を付与した DataFrame を返す。

    Args:
        df: O/H/L/C 列（大小不問）を含む DataFrame。
        period: MA 平滑期間（既定 6）。
        ma_method: 0=SMA/1=EMA/2=SMMA/3=LWMA（既定 1）。
        bar_width: iARCTAN の角度スケール（既定 0.1）。

    Returns:
        ``LEVEL_COUNT_COLUMN`` 列を持つ DataFrame（元 index を継承）。

    Raises:
        KeyError: 必須列が欠落している場合。
    """
    ohlc = _extract_ohlc(df)
    res = compute_arctan_full(
        ohlc["open"].to_numpy(dtype=float),
        ohlc["high"].to_numpy(dtype=float),
        ohlc["low"].to_numpy(dtype=float),
        ohlc["close"].to_numpy(dtype=float),
        period=period,
        ma_method=ma_method,
        bar_width=bar_width,
    )
    return pd.DataFrame(
        {LEVEL_COUNT_COLUMN: res.level_count_clamped},
        index=df.index,
    )


def arctan_levels(
    df: pd.DataFrame,
    *,
    period: int = DEFAULT_PERIOD,
    ma_method: int = 1,
    bar_width: float = 0.1,
) -> dict[str, float]:
    """OHLC DataFrame から σ12 水準線辞書（up_*/dn_*）を返す。

    Args:
        df: O/H/L/C 列（大小不問）を含む DataFrame。
        period: MA 平滑期間（既定 6）。
        ma_method: 0=SMA/1=EMA/2=SMMA/3=LWMA（既定 1）。
        bar_width: iARCTAN の角度スケール（既定 0.1）。

    Returns:
        σ12 水準（``up_067``..``up_329`` / ``dn_067``..``dn_329``）の辞書。

    Raises:
        KeyError: 必須列が欠落している場合。
    """
    ohlc = _extract_ohlc(df)
    res = compute_arctan_full(
        ohlc["open"].to_numpy(dtype=float),
        ohlc["high"].to_numpy(dtype=float),
        ohlc["low"].to_numpy(dtype=float),
        ohlc["close"].to_numpy(dtype=float),
        period=period,
        ma_method=ma_method,
        bar_width=bar_width,
    )
    return dict(res.levels)
