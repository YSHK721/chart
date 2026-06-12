"""層名: 成果物層（pandas）。

責務:
    high/low/close DataFrame から core を呼び、元 MQL の
    ResBufferDivisionOpenHigh/Low（|H-C|/|L-C| 距離）バッファに対応する機械可読
    DataFrame（dist_high/dist_low の 2 列・元 index 継承）へ整形する。価格軸
    overlay の 8 バンドと起点 close_ref はスカラ参照値のため別関数で辞書として返す。
    本指標に計算 input は無い（元 input は inpSymbol/inpTimeFrame のみ）。

元 MQL 対応:
    L205 MathAbs(iHigh(i)-iClose(i))      → build_hl_band（DIST_HIGH_COLUMN）
    L206 MathAbs(iLow(i)-iClose(i))       → build_hl_band（DIST_LOW_COLUMN）
    L220-227 iClose(1)±iBandsOnArray(...)  → hl_band_levels（8 バンド + close_ref）

依存:
    標準: __future__ / 外部: numpy, pandas / プロジェクト内: core
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .core import compute_distances, compute_hl_band

# 成果物の列名（機械可読）。
DIST_HIGH_COLUMN: str = "hlband_dist_high"
DIST_LOW_COLUMN: str = "hlband_dist_low"


def _extract_hlc(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """DataFrame から high/low/close を取り出す（列名の大小不問）。

    Raises:
        KeyError: high/low/close いずれかが存在しない。
    """
    cols = {c.lower(): c for c in df.columns}
    missing = [k for k in ("high", "low", "close") if k not in cols]
    if missing:
        raise KeyError(
            f"必須列が見つかりません: {missing}（存在する列: {list(df.columns)}）"
        )
    return (
        df[cols["high"]].to_numpy(dtype=np.float64),
        df[cols["low"]].to_numpy(dtype=np.float64),
        df[cols["close"]].to_numpy(dtype=np.float64),
    )


def build_hl_band(df: pd.DataFrame) -> pd.DataFrame:
    """high/low/close DataFrame → dist_high/dist_low の 2 列の成果物 DataFrame。

    元 index を継承する。``dist_high=|high-close|``, ``dist_low=|low-close|``
    （warm-up/NaN なし）。

    Args:
        df: high/low/close を含む DataFrame（列名大小不問）。

    Returns:
        DIST_HIGH_COLUMN / DIST_LOW_COLUMN の 2 列・元 index 継承の DataFrame。

    Raises:
        KeyError: high/low/close いずれかが存在しない。
    """
    high, low, close = _extract_hlc(df)
    dist_high, dist_low = compute_distances(high, low, close)
    return pd.DataFrame(
        {DIST_HIGH_COLUMN: dist_high, DIST_LOW_COLUMN: dist_low},
        index=df.index,
    )


def hl_band_levels(df: pd.DataFrame) -> dict[str, float]:
    """価格軸 overlay 8 バンド + 起点 close_ref の辞書を返す。

    ``{up_067, up_165, up_196, up_258, dn_067, dn_165, dn_196, dn_258,
    close_ref}``。up=close[-2]+band_upper(|H-C|)、dn=close[-2]-band_upper(|L-C|)。
    元 ``iClose(1)±iBandsOnArray(...)``（L220-227）に対応する。

    Args:
        df: high/low/close を含む DataFrame（列名大小不問）。

    Returns:
        8 バンド + close_ref の辞書。

    Raises:
        KeyError: high/low/close いずれかが存在しない。
        ValueError: N<2（close[-2] 不在）。
    """
    high, low, close = _extract_hlc(df)
    result = compute_hl_band(high, low, close)
    levels = dict(result.levels)
    levels["close_ref"] = result.close_ref
    return levels
