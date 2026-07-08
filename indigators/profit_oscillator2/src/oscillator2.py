"""層名: 成果物層（DataFrame 入出力アダプタ）。

profit_oscillator2 の core 層成果を pandas DataFrame 入出力に橋渡しする層。
列名は大小不問で解決し、core 層の純粋計算結果を 2 列（level_count / rci）として返す。

含む構造:
    LEVEL_COUNT_COLUMN / RCI_COLUMN : 出力列名。
    build_oscillator2  : DataFrame → [LEVEL_COUNT_COLUMN, RCI_COLUMN] の 2 列 DataFrame。
    oscillator2_levels : DataFrame → σ6 dict ＋ sub_min/sub_max。

元 MQL 対応:
    ExtBufferLevelCount → LEVEL_COUNT_COLUMN（compute_level_count）。
    ExtBufferRCI        → RCI_COLUMN（compute_rci）。
    StcLCStdDevArray / INDICATOR_MIN/MAX → oscillator2_levels（compute_levels2）。

依存:
    標準: __future__ / 外部: numpy, pandas / 内部: src.core。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .core import compute_levels2, compute_oscillator2_full

# 出力列名（既存 profit_oscillator と分離した命名）。
LEVEL_COUNT_COLUMN: str = "oscillator2_lc"
RCI_COLUMN: str = "oscillator2_rci"

# 必須列（大小不問で解決する論理名）。
_REQUIRED: tuple[str, ...] = ("open", "high", "low", "close", "volume")


def _resolve_ohlcv(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """DataFrame から OHLCV 5 列を大小不問で解決し ndarray dict を返す。

    列名は小文字化して照合する（``Open``/``HIGH``/``Volume`` 等を許容）。

    Raises:
        KeyError: open/high/low/close/volume のいずれかが欠落した場合。
    """
    lower_map: dict[str, str] = {}
    for col in df.columns:
        lower_map.setdefault(str(col).lower(), col)
    resolved: dict[str, np.ndarray] = {}
    for name in _REQUIRED:
        if name not in lower_map:
            raise KeyError(f"必須列が見つかりません: {name}（大小不問で照合）")
        resolved[name] = df[lower_map[name]].to_numpy(dtype=np.float64)
    return resolved


def build_oscillator2(
    df: pd.DataFrame,
    *,
    osc_period: int = 6,
    stc_slow: int = 6,
    ma_period: int = 60,
    rci_period: int = 12,
    direction: bool = False,
) -> pd.DataFrame:
    """OHLCV DataFrame から level_count / rci の 2 列 DataFrame を返す。

    Args:
        df: open/high/low/close/volume を含む DataFrame（列名大小不問）。
        osc_period/stc_slow/ma_period/rci_period/direction: core パラメータ。

    Returns:
        ``[LEVEL_COUNT_COLUMN, RCI_COLUMN]`` の 2 列 DataFrame（index は df を継承）。

    Raises:
        KeyError: 必須列（volume 含む）欠落。
        ValueError: osc_period<2 / ma_period<2 / 長不一致（core 由来）。
    """
    ohlcv = _resolve_ohlcv(df)
    res = compute_oscillator2_full(
        ohlcv["open"],
        ohlcv["high"],
        ohlcv["low"],
        ohlcv["close"],
        ohlcv["volume"],
        osc_period=osc_period,
        stc_slow=stc_slow,
        ma_period=ma_period,
        rci_period=rci_period,
        direction=direction,
    )
    return pd.DataFrame(
        {
            LEVEL_COUNT_COLUMN: res.level_count,
            RCI_COLUMN: res.rci,
        },
        index=df.index,
    )


def oscillator2_levels(
    df: pd.DataFrame,
    *,
    osc_period: int = 6,
    stc_slow: int = 6,
    ma_period: int = 60,
    rci_period: int = 12,
    direction: bool = False,
) -> dict[str, float]:
    """OHLCV DataFrame から σ6 水準辞書 ＋ sub_min/sub_max を返す。

    Args:
        df: open/high/low/close/volume を含む DataFrame（列名大小不問）。
        osc_period/stc_slow/ma_period/rci_period/direction: core パラメータ。

    Returns:
        ``{up_165, up_196, up_258, dn_165, dn_196, dn_258, sub_min, sub_max}``。

    Raises:
        KeyError: 必須列（volume 含む）欠落。
        ValueError: osc_period<2 / ma_period<2 / 長不一致（core 由来）。
    """
    ohlcv = _resolve_ohlcv(df)
    res = compute_oscillator2_full(
        ohlcv["open"],
        ohlcv["high"],
        ohlcv["low"],
        ohlcv["close"],
        ohlcv["volume"],
        osc_period=osc_period,
        stc_slow=stc_slow,
        ma_period=ma_period,
        rci_period=rci_period,
        direction=direction,
    )
    return compute_levels2(res.level_count)
