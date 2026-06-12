"""PRO!fit_Oscillator パッケージ（src 層）。

core 層（純粋計算）と成果物層（DataFrame アダプタ）・入出力アダプタ（loader /
lwc_chart）を公開する。元 MQL4 ``PRO!fit_Oscillator.mq4``（18 サブ系列 PS レベル
カウント複合）の Python 移植。

matplotlib 出力アダプタ（``src.plot.plot_oscillator``）は matplotlib 依存を本
パッケージ import に持ち込まないため公開 API から除外し、``from src.plot import
plot_oscillator`` で明示 import する（matplotlib 未導入環境でも ``import src`` を
壊さないため。先例 profit_arctan / profit_mfi 準拠）。
"""

from __future__ import annotations

from .core import (
    DEFAULT_PERIOD_A,
    DEFAULT_PERIOD_B,
    SIGMA_LEVELS,
    OscillatorResult,
    compute_level_count,
    compute_oscillator_full,
    compute_oscillator_levels,
    compute_sigma_levels,
    ps_level_count,
)
from .loader import load_ohlcv_csv
from .lwc_chart import add_oscillator
from .oscillator import (
    LEVEL_COUNT_COLUMN,
    build_oscillator,
    oscillator_levels,
)

__all__ = [
    "load_ohlcv_csv",
    "build_oscillator",
    "oscillator_levels",
    "add_oscillator",
    "compute_oscillator_full",
    "compute_oscillator_levels",
    "compute_level_count",
    "compute_sigma_levels",
    "ps_level_count",
    "OscillatorResult",
    "DEFAULT_PERIOD_A",
    "DEFAULT_PERIOD_B",
    "SIGMA_LEVELS",
    "LEVEL_COUNT_COLUMN",
]
