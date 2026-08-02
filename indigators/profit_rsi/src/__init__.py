"""profit_rsi の公開 API（core 層 ＋ 成果物層 ＋ 入出力アダプタ）。

PRO!fitRSI Python 移植のうち core 層（純粋計算）・成果物層（pandas）・入力アダプタ
（loader）・lightweight-charts 出力アダプタ（lwc_chart）の公開シンボルを再エクスポート
する。matplotlib 出力アダプタ（``src.plot``）は matplotlib 依存のため公開 API から除外
し、``from src.plot import plot_rsi`` で明示 import する（matplotlib 未導入環境でも
``import src`` を壊さないため。先例 profit_mfi/profit_stc 準拠）。
"""

from __future__ import annotations

from .core import (
    APPLY_TO_PRICE,
    DEFAULT_APPLY,
    DEFAULT_RSI_PERIOD,
    RsiResult,
    compute_rsi,
    compute_rsi_full,
    compute_rsi_levels,
)
from .loader import load_ohlc_csv
from .lwc_chart import add_rsi
from .rsi import (
    RSI_COLUMN,
    build_rsi,
    rsi_levels,
)

__all__ = [
    "APPLY_TO_PRICE",
    "DEFAULT_APPLY",
    "DEFAULT_RSI_PERIOD",
    "RsiResult",
    "compute_rsi",
    "compute_rsi_full",
    "compute_rsi_levels",
    "RSI_COLUMN",
    "build_rsi",
    "rsi_levels",
    "load_ohlc_csv",
    "add_rsi",
]
