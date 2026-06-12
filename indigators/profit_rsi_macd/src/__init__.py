"""profit_rsi_macd の公開 API（core 層 ＋ 成果物層 ＋ 入出力アダプタ）。

PRO!fitRSIMACD Python 移植のうち core 層（純粋計算）・成果物層（pandas）・入力
アダプタ（loader）・lightweight-charts 出力アダプタ（lwc_chart）の公開シンボルを
再エクスポートする。matplotlib 出力アダプタ（plot）は matplotlib 依存のため、先例
（profit_mfi_macd）同様 __init__ から除外し、``from src.plot import plot_rsimacd`` で
明示 import する（matplotlib 未導入環境でも ``import src`` を壊さないため）。
"""

from __future__ import annotations

from .core import (
    DEFAULT_FAST_EMA,
    DEFAULT_RSI_PERIOD,
    DEFAULT_SIGNAL_EMA,
    DEFAULT_SLOW_EMA,
    RsiMacdResult,
    compute_rsi,
    compute_rsimacd,
    compute_rsimacd_levels,
)
from .loader import load_ohlc_csv
from .lwc_chart import (
    MACD_LINE_NAME,
    SIGNAL_LINE_NAME,
    add_rsimacd,
)
from .rsimacd import (
    HIST_COLUMN,
    MACD_COLUMN,
    SIGNAL_COLUMN,
    build_rsimacd,
    rsimacd_levels,
)

__all__ = [
    "DEFAULT_RSI_PERIOD",
    "DEFAULT_FAST_EMA",
    "DEFAULT_SLOW_EMA",
    "DEFAULT_SIGNAL_EMA",
    "RsiMacdResult",
    "compute_rsi",
    "compute_rsimacd",
    "compute_rsimacd_levels",
    "HIST_COLUMN",
    "MACD_COLUMN",
    "SIGNAL_COLUMN",
    "build_rsimacd",
    "rsimacd_levels",
    "load_ohlc_csv",
    "add_rsimacd",
    "MACD_LINE_NAME",
    "SIGNAL_LINE_NAME",
]
