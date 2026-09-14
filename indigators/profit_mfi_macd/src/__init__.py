"""profit_mfi_macd の公開 API（core 層 ＋ 成果物層 ＋ 入出力アダプタ）。

PRO!fitMFIMACD Python 移植のうち core 層（純粋計算）・成果物層（pandas）・入力
アダプタ（loader）・lightweight-charts 出力アダプタ（lwc_chart）の公開シンボルを
再エクスポートする。matplotlib 出力アダプタ（plot）は matplotlib 依存のため、先例
（profit_mfi）同様 __init__ から除外し、``from src.plot import plot_mfimacd`` で
明示 import する（matplotlib 未導入環境でも ``import src`` を壊さないため）。
"""

from __future__ import annotations

from .core import (
    DEFAULT_FAST_EMA,
    DEFAULT_MFI_PERIOD,
    DEFAULT_SIGNAL_EMA,
    DEFAULT_SLOW_EMA,
    MfiMacdResult,
    compute_mfi,
    compute_mfimacd,
    compute_mfimacd_levels,
)
from .loader import load_ohlcv_csv
from .lwc_chart import (
    MACD_LINE_NAME,
    SIGNAL_LINE_NAME,
    add_mfimacd,
)
from .mfimacd import (
    HIST_COLUMN,
    MACD_COLUMN,
    SIGNAL_COLUMN,
    build_mfimacd,
    mfimacd_levels,
)

__all__ = [
    "DEFAULT_FAST_EMA",
    "DEFAULT_MFI_PERIOD",
    "DEFAULT_SIGNAL_EMA",
    "DEFAULT_SLOW_EMA",
    "MfiMacdResult",
    "compute_mfi",
    "compute_mfimacd",
    "compute_mfimacd_levels",
    "HIST_COLUMN",
    "MACD_COLUMN",
    "SIGNAL_COLUMN",
    "build_mfimacd",
    "mfimacd_levels",
    "load_ohlcv_csv",
    "add_mfimacd",
    "MACD_LINE_NAME",
    "SIGNAL_LINE_NAME",
]
