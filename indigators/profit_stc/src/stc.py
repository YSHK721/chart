"""層名: 成果物層（pandas）。

責務:
    OHLC（high/low/close）DataFrame から core を呼び、元 MQL の
    ExtBufferOscillator バッファに対応する機械可読 DataFrame（OSC_COLUMN 1 列・
    元 index 継承）へ整形する。水準線はスカラ参照値のため別関数で返す。

元 MQL 対応:
    ``SetIndexBuffer(0, ExtBufferOscillator)`` への書き込み（build_stc）と
    ``StcLCStdDevArray[1..4]`` / ``IndicatorSetDouble(INDICATOR_MINIMUM/MAXIMUM)``
    の水準線設定（stc_levels）。

依存（PORTING_GUIDE §8）:
    標準: __future__ / 外部: numpy, pandas / プロジェクト内: core
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .core import DEFAULT_PERIOD, compute_stc

# 成果物の列名（PORTING_GUIDE §5: 機械可読）。
OSC_COLUMN: str = "stc_osc"


def _extract_hlc(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """DataFrame から high/low/close を取り出す（列名の大小不問）。"""
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


def build_stc(df: pd.DataFrame, *, period: int = DEFAULT_PERIOD) -> pd.DataFrame:
    """high/low/close DataFrame → OSC_COLUMN 1 列の成果物 DataFrame。

    元 index を継承する。warm-up（i<period-1）は元 iStochastic 既定どおり 0
    （NaN ではない）。

    Args:
        df: high/low/close を含む DataFrame（列名大小不問）。
        period: 期間（>=2, 元 inpPeriodOscillator）。

    Returns:
        OSC_COLUMN 1 列・元 index 継承の DataFrame。

    Raises:
        KeyError: high/low/close いずれかが存在しない。
        ValueError: ``period < 2``。
    """
    high, low, close = _extract_hlc(df)
    result = compute_stc(high, low, close, period=period)
    return pd.DataFrame({OSC_COLUMN: result.oscillator}, index=df.index)


def stc_levels(df: pd.DataFrame, *, period: int = DEFAULT_PERIOD) -> dict[str, float]:
    """成果物オシレーターの σ 水準辞書 {P1,P2,M1,M2,sub_min,sub_max} を返す。

    元 ``StcLCStdDevArray[1..4]``（P1/P2/M1/M2）および
    ``IndicatorSetDouble(INDICATOR_MINIMUM=M2, INDICATOR_MAXIMUM=P2)`` に対応する。
    水準は時系列ではなく価格軸の水平参照値のため DataFrame と分離して返す。

    Args:
        df: high/low/close を含む DataFrame（列名大小不問）。
        period: 期間（>=2）。

    Returns:
        ``{"P1", "P2", "M1", "M2", "sub_min", "sub_max"}`` の辞書。
    """
    high, low, close = _extract_hlc(df)
    result = compute_stc(high, low, close, period=period)
    levels = dict(result.levels)
    levels["sub_min"] = result.sub_min
    levels["sub_max"] = result.sub_max
    return levels
