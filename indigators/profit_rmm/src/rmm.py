"""層名: 成果物層（pandas）。

責務:
    PRO!fitRMM の core 層 ``compute_rmm`` を pandas DataFrame 入出力でラップする
    成果物層。OHLCV を DataFrame から抽出し、レベルカウント列を元 index を継承した
    DataFrame として返す。σ6 水準辞書も提供する。出力アダプタ・loader・demo・docs は
    対象外。

含む構造:
    LEVEL_COUNT_COLUMN : レベルカウント列名（"rmm_lc"）。
    build_rmm          : df -> level_count 列を持つ DataFrame（元 index 継承）。
    rmm_levels         : df -> σ6 水準辞書。

元 MQL 対応:
    ``PRO!fitRMM.mq4`` の OnCalculate 出力（ExtBufferLevelCount）に相当する数値列を
    DataFrame 形式で提供する。

依存:
    標準: __future__ / 外部: numpy, pandas / プロジェクト内: src.core。
"""

from __future__ import annotations

import pandas as pd

from . import core

# レベルカウント列名。
LEVEL_COUNT_COLUMN: str = "rmm_lc"

# 必須列（小文字正準）。列名は大小不問で照合する。
_REQUIRED_COLUMNS: tuple[str, ...] = ("high", "low", "close", "volume")


def _resolve_columns(df: pd.DataFrame) -> dict[str, str]:
    """必須列を大小不問で解決し {正準名: 実列名} を返す。

    Raises:
        KeyError: 必須列（high/low/close/volume）が欠落している場合。
    """
    lower_map = {str(c).lower(): c for c in df.columns}
    resolved: dict[str, str] = {}
    for name in _REQUIRED_COLUMNS:
        if name not in lower_map:
            raise KeyError(f"必須列が見つかりません: {name}")
        resolved[name] = lower_map[name]
    return resolved


def build_rmm(
    df: pd.DataFrame,
    *,
    osc_period: int = core.DEFAULT_OSC_PERIOD,
    ma_period: int = core.DEFAULT_MA_PERIOD,
) -> pd.DataFrame:
    """OHLCV DataFrame から レベルカウント列を持つ DataFrame を返す（元 index 継承）。

    Args:
        df: high/low/close/volume を含む DataFrame（列名大小不問）。
        osc_period: オシレーター期間（既定 6）。
        ma_period: EMA 期間（既定 6）。

    Returns:
        ``LEVEL_COUNT_COLUMN``（"rmm_lc"）列を持つ DataFrame（df の index を継承）。

    Raises:
        KeyError: 必須列（high/low/close/volume）欠落。
        ValueError: osc_period<2、または抽出系列長不一致（core.compute_rmm 経由）。
    """
    cols = _resolve_columns(df)
    result = core.compute_rmm(
        df[cols["high"]].to_numpy(dtype=float),
        df[cols["low"]].to_numpy(dtype=float),
        df[cols["close"]].to_numpy(dtype=float),
        df[cols["volume"]].to_numpy(dtype=float),
        osc_period=osc_period,
        ma_period=ma_period,
    )
    return pd.DataFrame(
        {LEVEL_COUNT_COLUMN: result.level_count}, index=df.index
    )


def rmm_levels(
    df: pd.DataFrame,
    *,
    osc_period: int = core.DEFAULT_OSC_PERIOD,
    ma_period: int = core.DEFAULT_MA_PERIOD,
) -> dict[str, float]:
    """OHLCV DataFrame から level_count の σ6 水準辞書を返す。

    Args:
        df: high/low/close/volume を含む DataFrame（列名大小不問）。
        osc_period: オシレーター期間（既定 6）。
        ma_period: EMA 期間（既定 6）。

    Returns:
        σ6 水準辞書（up_1s..dn_3s の 6 要素）。

    Raises:
        KeyError: 必須列欠落。
        ValueError: osc_period<2、または長不一致（core.compute_rmm 経由）。
    """
    cols = _resolve_columns(df)
    result = core.compute_rmm(
        df[cols["high"]].to_numpy(dtype=float),
        df[cols["low"]].to_numpy(dtype=float),
        df[cols["close"]].to_numpy(dtype=float),
        df[cols["volume"]].to_numpy(dtype=float),
        osc_period=osc_period,
        ma_period=ma_period,
    )
    return dict(result.lc_levels)
