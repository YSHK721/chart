"""層名: src パッケージ（profit_rmm_macd の core / 成果物層 / 入出力アダプタの再公開）。

責務:
    PRO!fitRMMMACD（RMM レベルカウント＋MACD連鎖の変種）の純粋計算 core 層
    （``core``）・pandas 成果物層（``rmmmacd``）・入力アダプタ（``loader``）・
    lightweight-charts 出力アダプタ（``lwc_chart``）の公開シンボルを束ねる薄い
    再公開層。matplotlib 出力アダプタ（``plot``）は matplotlib 依存のため、先例
    （profit_mfi_macd）同様 __init__ から除外し、``from src.plot import plot_rmmmacd``
    で明示 import する（matplotlib 未導入環境でも ``import src`` を壊さないため）。

公開 API:
    compute_rmm_level_count : RMM level_count 複製（numpy 配列入出力）。
    compute_rmmmacd         : level_count → fast/slow EMA → macd(=slow-fast) →
        signal EMA → histogram(=macd-signal・係数なし) を統合 → RmmMacdResult。
    RmmMacdResult           : 計算成果の不変 DTO（σ levels フィールドを持たない）。
    build_rmmmacd           : OHLCV DataFrame → histogram/macd/signal 3 列の DataFrame。
    load_ohlcv_csv          : CSV → OHLCV DataFrame（入力アダプタ）。
    add_rmmmacd             : lightweight-charts へヒスト 1 本・線 2 本を追加（水準線なし）。
    定数・列名（DEFAULT_OSC_PERIOD ほか / HIST_COLUMN ほか / MACD_LINE_NAME ほか）。

元 MQL 対応:
    ``PRO!fitRMMMACD.mq4`` を昇順=古→新へ 1:1 変換する。

依存:
    core: numpy ＋ 共有（profit_rmm 複製ロジック / moving_averages.exponential_ma_on_buffer）。
    rmmmacd: pandas（成果物層）。loader: pandas。lwc_chart: numpy/pandas（duck typing）。
"""

from __future__ import annotations

from . import core, rmmmacd  # noqa: F401
from .core import (
    DEFAULT_FAST_EMA,
    DEFAULT_MA_PERIOD,
    DEFAULT_OSC_PERIOD,
    DEFAULT_SIGNAL_EMA,
    DEFAULT_SLOW_EMA,
    RmmMacdResult,
    compute_rmm_level_count,
    compute_rmmmacd,
)
from .loader import load_ohlcv_csv
from .lwc_chart import (
    MACD_LINE_NAME,
    SIGNAL_LINE_NAME,
    add_rmmmacd,
)
from .rmmmacd import (
    HIST_COLUMN,
    MACD_COLUMN,
    SIGNAL_COLUMN,
    build_rmmmacd,
)

__all__ = [
    "core",
    "rmmmacd",
    "compute_rmm_level_count",
    "compute_rmmmacd",
    "RmmMacdResult",
    "build_rmmmacd",
    "load_ohlcv_csv",
    "add_rmmmacd",
    "MACD_LINE_NAME",
    "SIGNAL_LINE_NAME",
    "HIST_COLUMN",
    "MACD_COLUMN",
    "SIGNAL_COLUMN",
    "DEFAULT_OSC_PERIOD",
    "DEFAULT_MA_PERIOD",
    "DEFAULT_FAST_EMA",
    "DEFAULT_SLOW_EMA",
    "DEFAULT_SIGNAL_EMA",
]
