"""層名: 成果物層（pandas）。

責務:
    OHLC（少なくとも close 列）の DataFrame から core.compute_osi_ma を呼び、
    元 MQL の MAKairi バッファに対応する機械可読 DataFrame（KAIRI_COLUMN 1 列・
    元 index 継承）に整形する。水準線はスカラ参照値のため別関数で返す。

元 MQL 対応:
    ``SetIndexBuffer(0, MAKairi)`` への書き込みと
    ``#property indicator_level1..4``（±1.0 / ±0.5）の水準線設定。

依存（PORTING_GUIDE §8）:
    標準: __future__ / 外部: numpy, pandas / プロジェクト内: core
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .core import DEFAULT_MA_MODE, DEFAULT_MA_PERIOD, compute_osi_ma

# 成果物の列名（PORTING_GUIDE §5: 機械可読）。
KAIRI_COLUMN: str = "osi_ma_kairi"


def _extract_close(df: pd.DataFrame) -> np.ndarray:
    """DataFrame から close 列を取り出す（列名の大小不問）。"""
    cols = {c.lower(): c for c in df.columns}
    if "close" not in cols:
        raise KeyError(f"close 列が見つかりません（存在する列: {list(df.columns)}）")
    return df[cols["close"]].to_numpy(dtype=np.float64)


def build_osi_ma(
    df: pd.DataFrame,
    *,
    ma_mode: int = DEFAULT_MA_MODE,
    ma_period: int = DEFAULT_MA_PERIOD,
) -> pd.DataFrame:
    """close 列 DataFrame → KAIRI_COLUMN 1 列の成果物 DataFrame。

    元 index を継承し NaN を保持する。

    Args:
        df: 少なくとも close 列を含む DataFrame（列名大小不問）。
        ma_mode: MA 種別（0=SMA,1=EMA,2=SMMA,3=LWMA）。
        ma_period: MA 期間（>0）。

    Returns:
        KAIRI_COLUMN 1 列・元 index 継承の DataFrame。

    Raises:
        KeyError: close 列が存在しない。
        ValueError: ``ma_mode`` が範囲外、または ``ma_period<=0``。
    """
    close = _extract_close(df)
    kairi = compute_osi_ma(close, ma_mode=ma_mode, ma_period=ma_period)
    return pd.DataFrame({KAIRI_COLUMN: kairi}, index=df.index)


def osi_ma_levels() -> dict:
    """水準線（±1.0 / ±0.5）の辞書を返す。

    元 MQL の ``#property indicator_level1..4`` に対応する。
    """
    return {"lvl_1": 1.0, "lvl_05": 0.5, "lvl_-05": -0.5, "lvl_-1": -1.0}
