"""PRO!fitHLBand — MQL4 インジケーターの Python 移植（公開 API）。

元 MQL4 ``PRO!fitHLBand.mq4`` は高安レンジ（high-low）の全系列平均と母σ帯
（±1.65/1.96/2.58σ）を算出し、最新足の High からの減算・Low への加算で価格軸へ
投影した overlay 8 本を描画する（別ウィンドウには range を DRAW_HISTOGRAM・
下限0 / 上限 b196*2）。本パッケージは PORTING_GUIDE §8 に従い core 層（純粋計算）
と成果物層（pandas）を分離する。本指標に input パラメータは無い（元コードに input
不在）。

公開 API:
    compute_range       : range[i]=high[i]-low[i] の純粋計算。
    compute_range_stats : 全系列平均・母σ（÷N）と σ 帯（b165/b196/b258）。
    compute_hl_bands    : 最新 H/L へ σ 帯を投影した overlay 8 本。
    compute_hlband      : 統合 frozen DTO（HLBandResult）。
    RangeStats / HLPriceBands / HLBandResult : 各不変 DTO。
    build_hlband        : high/low DataFrame → RANGE_COLUMN 1 列の成果物。
    hlband_levels       : separate レベル {avg,b165,b196,b258,sub_min,sub_max}。
    hlband_price_bands  : overlay 8 本 {high_*/low_*}。
    load_ohlc_csv       : CSV → OHLC DataFrame（入力アダプタ）。
    add_hlband_separate : lightweight-charts の別ウィンドウへヒストグラム＋水準線4本（出力アダプタ）。
    add_hlband_overlay  : lightweight-charts のメインチャートへ水平線8本（出力アダプタ）。
    定数: RANGE_COLUMN。

注記:
    matplotlib 依存の ``src.plot``（PNG 出力アダプタ）は先例（profit_stc）同様
    本 __init__ から除外する（matplotlib 未導入環境でも import を壊さないため）。
    PNG 描画は ``from src.plot import plot_hlband`` で明示的に import する。
"""

from __future__ import annotations

from .core import (
    HLBandResult,
    HLPriceBands,
    RangeStats,
    compute_hl_bands,
    compute_hlband,
    compute_range,
    compute_range_stats,
)
from .hlband import (
    RANGE_COLUMN,
    build_hlband,
    hlband_levels,
    hlband_price_bands,
)
from .loader import load_ohlc_csv
from .lwc_chart import add_hlband_overlay, add_hlband_separate

__all__ = [
    "compute_range",
    "compute_range_stats",
    "compute_hl_bands",
    "compute_hlband",
    "RangeStats",
    "HLPriceBands",
    "HLBandResult",
    "build_hlband",
    "hlband_levels",
    "hlband_price_bands",
    "load_ohlc_csv",
    "add_hlband_separate",
    "add_hlband_overlay",
    "RANGE_COLUMN",
]
