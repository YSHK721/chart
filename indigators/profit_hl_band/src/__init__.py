"""PRO!fit_HLBand — MQL4 インジケーター（アンダースコア版）の Python 移植（公開 API）。

元 MQL4 ``PRO!fit_HLBand.mq4``（Copyright 2017, PRO!fit Investars）は High-Close /
Low-Close の絶対距離系列（|H-C| / |L-C|）の全系列平均 + dev·母σ（dev =
0.67/1.65/1.96/2.58）を求め、起点終値 close[-2]（= iClose(...,1)）へ加算（上側 4 本）・
減算（下側 4 本）して価格軸へ投影した overlay バンド 8 本（OBJ_TREND）をメインチャート
（``indicator_chart_window``）に描く。本指標は separate ウィンドウ・ヒストグラム・
プロット用バッファを持たない overlay 専用指標である。

本パッケージは PORTING_GUIDE §8 に従い core 層（純粋計算）と成果物層（pandas）を
分離する。本指標に計算 input は無い（元 input は inpSymbol/inpTimeFrame のみで計算
period でない）。

公開 API:
    compute_distances : dist_high=|H-C| / dist_low=|L-C| の純粋計算。
    band_upper        : mean(dist) + dev·母σ(dist)（÷N 全系列）。
    compute_hl_band   : close[-2] へ 8 バンドを投影した frozen DTO。
    HlBandResult      : dist_high/dist_low/close_ref/levels の不変 DTO。
    HL_BAND_DEVS      : 固定偏差 (0.67, 1.65, 1.96, 2.58)。
    build_hl_band     : high/low/close DataFrame → dist_high/dist_low の 2 列の成果物。
    hl_band_levels    : overlay 8 バンド + close_ref の辞書。
    load_ohlc_csv     : CSV → OHLC DataFrame（入力アダプタ）。
    add_hl_band       : lightweight-charts のメイン chart へ水平線 8 本（出力アダプタ）。
    定数: DIST_HIGH_COLUMN / DIST_LOW_COLUMN。

注記:
    matplotlib 依存の ``src.plot``（PNG 出力アダプタ）は先例（profit_hlband）同様
    本 __init__ から除外する（matplotlib 未導入環境でも import を壊さないため）。
    PNG 描画は ``from src.plot import plot_hl_band`` で明示的に import する。
"""

from __future__ import annotations

from .core import (
    HL_BAND_DEVS,
    HlBandResult,
    band_upper,
    compute_distances,
    compute_hl_band,
)
from .hl_band import (
    DIST_HIGH_COLUMN,
    DIST_LOW_COLUMN,
    build_hl_band,
    hl_band_levels,
)
from .loader import load_ohlc_csv
from .lwc_chart import add_hl_band

__all__ = [
    "compute_distances",
    "band_upper",
    "compute_hl_band",
    "HlBandResult",
    "HL_BAND_DEVS",
    "build_hl_band",
    "hl_band_levels",
    "DIST_HIGH_COLUMN",
    "DIST_LOW_COLUMN",
    "load_ohlc_csv",
    "add_hl_band",
]
