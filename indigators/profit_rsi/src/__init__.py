"""profit_rsi の公開 API（core 層 ＋ 水準層 ＋ 成果物層 ＋ 入出力アダプタ）。

PRO!fitRSI Python 移植のうち core 層（純粋計算）・水準層（正常帯と POT/GPD 外れ値水準）・
成果物層（pandas）・入力アダプタ（loader）・lightweight-charts 出力アダプタ（lwc_chart）の
公開シンボルを再エクスポートする。matplotlib 出力アダプタ（``src.plot``）は matplotlib 依存の
ため公開 API から除外し、``from src.plot import plot_rsi`` で明示 import する（matplotlib 未導入
環境でも ``import src`` を壊さないため。先例 profit_mfi/profit_stc 準拠）。
"""

from __future__ import annotations

from .core import (
    APPLY_TO_PRICE,
    DEFAULT_APPLY,
    DEFAULT_RSI_PERIOD,
    RsiResult,
    compute_rsi,
    compute_rsi_full,
)
from .levels import (
    BAND_KEYS,
    DEFAULT_Q_HIGH,
    DEFAULT_Q_LOW,
    DEFAULT_WINDOW_N,
    LEVEL_KEYS,
    RSI_MAX,
    RSI_MIN,
    excess_fraction,
    headroom,
    levels_at,
    levels_latest,
    rsi_levels,
    step_excess_event,
)
from .loader import load_ohlc_csv
from .lwc_chart import add_rsi
from .rsi import (
    LEVEL_COLUMNS,
    RSI_COLUMN,
    build_rsi,
    quantile_column,
)

__all__ = [
    "APPLY_TO_PRICE",
    "DEFAULT_APPLY",
    "DEFAULT_RSI_PERIOD",
    "RsiResult",
    "compute_rsi",
    "compute_rsi_full",
    "BAND_KEYS",
    "DEFAULT_Q_HIGH",
    "DEFAULT_Q_LOW",
    "DEFAULT_WINDOW_N",
    "LEVEL_KEYS",
    "RSI_MAX",
    "RSI_MIN",
    "excess_fraction",
    "headroom",
    "levels_at",
    "levels_latest",
    "rsi_levels",
    "step_excess_event",
    "LEVEL_COLUMNS",
    "RSI_COLUMN",
    "build_rsi",
    "quantile_column",
    "load_ohlc_csv",
    "add_rsi",
]
