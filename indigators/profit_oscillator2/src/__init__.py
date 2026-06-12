"""profit_oscillator2 src パッケージ（core 層・成果物層）。

PRO!fitOscillator.mq4（funLevelCount 加重複合 ＋ Spearman RCI ＋ σ6）の Python 移植。
既存 profit_oscillator とは完全分離（compute_oscillator2_full / Oscillator2Result /
oscillator2_lc 等の命名で衝突回避）。

公開 API:
    core 層:
        compute_rsi / compute_mfi / compute_wpr / compute_marod    : サブオシレーター（複製元一致）。
        compute_stochastic                                         : 生 %K（複製元一致）。
        level_count_score                                          : funLevelCount 採点（複製元一致）。
        compute_level_count                                        : 加重複合レベルカウント。
        compute_levels2                                            : σ6 水準（母σ÷N）＋ sub_min/sub_max。
        compute_rci                                                : Spearman RCI（int 切り捨て順位）。
        compute_oscillator2_full / Oscillator2Result               : 統合 frozen DTO。
    成果物層:
        build_oscillator2 / oscillator2_levels                     : DataFrame 入出力。
        LEVEL_COUNT_COLUMN / RCI_COLUMN                            : 出力列名。
    入出力アダプタ:
        load_ohlcv_csv                                             : CSV → OHLCV DataFrame。
        add_oscillator2                                            : lightweight-charts 系列追加。

    plot（matplotlib）は本 __init__ から除外する（matplotlib 未導入環境でも import 可能に
    保つため。描画は src.plot から直接 import する）。
"""

from __future__ import annotations

from .core import (
    Oscillator2Result,
    compute_level_count,
    compute_levels2,
    compute_marod,
    compute_mfi,
    compute_oscillator2_full,
    compute_rci,
    compute_rsi,
    compute_stochastic,
    compute_wpr,
    level_count_score,
)
from .loader import load_ohlcv_csv
from .lwc_chart import add_oscillator2
from .oscillator2 import (
    LEVEL_COUNT_COLUMN,
    RCI_COLUMN,
    build_oscillator2,
    oscillator2_levels,
)

__all__ = [
    "Oscillator2Result",
    "compute_level_count",
    "compute_levels2",
    "compute_marod",
    "compute_mfi",
    "compute_oscillator2_full",
    "compute_rci",
    "compute_rsi",
    "compute_stochastic",
    "compute_wpr",
    "level_count_score",
    "LEVEL_COUNT_COLUMN",
    "RCI_COLUMN",
    "build_oscillator2",
    "oscillator2_levels",
    "load_ohlcv_csv",
    "add_oscillator2",
]
