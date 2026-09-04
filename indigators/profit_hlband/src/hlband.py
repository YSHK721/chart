"""層名: 成果物層（pandas）。

責務:
    high/low DataFrame から core を呼び、元 MQL の ExtVOLBuffer（レンジ）バッファに
    対応する機械可読 DataFrame（RANGE_COLUMN 1 列・元 index 継承）へ整形する。
    別ウィンドウの水準（separate レベル）と価格軸 overlay 8 本はスカラ参照値のため
    別関数で辞書として返す。本指標に input パラメータは無い（元コードに input 不在）。

元 MQL 対応:
    L61 ExtVOLBuffer[i] = high[i] - low[i]              → build_hlband（RANGE_COLUMN）
    L97-100 StcLCStdDevArray[1..4] / iMAOnArray 平均    → hlband_levels（avg/b165/b196/b258）
    L102 IndicatorSetDouble(INDICATOR_MINIMUM, 0)       → hlband_levels（sub_min=0.0）
    L103 IndicatorSetDouble(INDICATOR_MAXIMUM, [2]*2)   → hlband_levels（sub_max=b196*2）
    L67-74 iHigh(0)-... / iLow(0)+...                  → hlband_price_bands（overlay 8 本）

依存（PORTING_GUIDE §8）:
    標準: __future__ / 外部: numpy, pandas / プロジェクト内: core
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .core import compute_hlband

# 成果物の列名（PORTING_GUIDE §5: 機械可読）。
RANGE_COLUMN: str = "hl_range"


def _extract_hl(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """DataFrame から high/low を取り出す（列名の大小不問）。

    成果物層の空入力ガード（PORTING_GUIDE §8 の依存内向き維持）。core は空入力で
    ``high[-1]`` の IndexError や ``np.mean([])`` の nan を返すため（仕様 R1）、core を
    変更せず成果物層側でここを唯一の choke point として明示的な ValueError を投げる。

    Raises:
        KeyError: high/low いずれかが存在しない。
        ValueError: 行数が 0（空 DataFrame）の場合。最新 H/L 投影・統計が定義不能なため。
    """
    cols = {c.lower(): c for c in df.columns}
    missing = [k for k in ("high", "low") if k not in cols]
    if missing:
        raise KeyError(
            f"必須列が見つかりません: {missing}（存在する列: {list(df.columns)}）"
        )
    if len(df) == 0:
        raise ValueError(
            "空の DataFrame です（0 行）。レンジ統計・最新 H/L 投影には 1 行以上が必要です。"
        )
    return (
        df[cols["high"]].to_numpy(dtype=np.float64),
        df[cols["low"]].to_numpy(dtype=np.float64),
    )


def build_hlband(df: pd.DataFrame) -> pd.DataFrame:
    """high/low DataFrame → RANGE_COLUMN 1 列の成果物 DataFrame。

    元 index を継承する。``range[i] = high[i] - low[i]``（warm-up/NaN なし）。

    Args:
        df: high/low を含む DataFrame（列名大小不問）。

    Returns:
        RANGE_COLUMN 1 列・元 index 継承の DataFrame。

    Raises:
        KeyError: high/low いずれかが存在しない。
    """
    high, low = _extract_hl(df)
    result = compute_hlband(high, low)
    return pd.DataFrame({RANGE_COLUMN: result.range}, index=df.index)


def hlband_levels(df: pd.DataFrame) -> dict[str, float]:
    """別ウィンドウの separate レベル辞書を返す。

    ``{avg, b165, b196, b258, sub_min(=0.0), sub_max(=b196*2)}``。元
    ``iMAOnArray`` 平均・``iBandsOnArray`` σ 帯と
    ``IndicatorSetDouble(INDICATOR_MINIMUM=0, INDICATOR_MAXIMUM=b196*2)`` に対応。

    Args:
        df: high/low を含む DataFrame（列名大小不問）。

    Returns:
        ``{"avg", "b165", "b196", "b258", "sub_min", "sub_max"}`` の辞書。

    Raises:
        KeyError: high/low いずれかが存在しない。
    """
    high, low = _extract_hl(df)
    result = compute_hlband(high, low)
    return {
        "avg": result.stats.avg,
        "b165": result.stats.b165,
        "b196": result.stats.b196,
        "b258": result.stats.b258,
        "sub_min": result.sub_min,
        "sub_max": result.sub_max,
    }


def hlband_price_bands(df: pd.DataFrame) -> dict[str, float]:
    """価格軸 overlay 8 本の辞書を返す（High 側=減算 / Low 側=加算）。

    ``{high_avg, high_b165, high_b196, high_b258, low_avg, low_b165, low_b196,
    low_b258}``。最新 H/L（昇順 last = high[-1]/low[-1]）へ σ 帯を投影。元
    ``iHigh(NULL,0,0)-...`` / ``iLow(NULL,0,0)+...``（L67-74）に対応する。

    Args:
        df: high/low を含む DataFrame（列名大小不問）。

    Returns:
        overlay 8 本の辞書。

    Raises:
        KeyError: high/low いずれかが存在しない。
    """
    high, low = _extract_hl(df)
    result = compute_hlband(high, low)
    bands = result.bands
    return {
        "high_avg": bands.high_avg,
        "high_b165": bands.high_b165,
        "high_b196": bands.high_b196,
        "high_b258": bands.high_b258,
        "low_avg": bands.low_avg,
        "low_b165": bands.low_b165,
        "low_b196": bands.low_b196,
        "low_b258": bands.low_b258,
    }
