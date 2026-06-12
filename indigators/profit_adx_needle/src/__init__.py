"""PRO!fit_ADX_NEEDLE — MQL4 インジケーターの Python 移植。

元 MQL4（``sample/MQL4/Indicators/PRO!fit_ADX_NEEDLE.mq4`` + ``ProfitSystem/PS.mqh``）は、
ADX を ``inpPeriod=6`` で 7 種の適用価格により算出し、各値を「平均からの σ 距離」へ単位
変換して加算した「市場の温度」（レベルカウント）を別ウィンドウのヒストグラムで表示する。
本パッケージはガイド（indigators/PORTING_GUIDE.md）に従い、計算（core/needle）と入出力アダプタ
（loader/plot/lwc_chart）を分離する。

公開 API:
    build_adx_needle    : OHLC DataFrame → 成果物 DataFrame（needle/level_count/adx）。
    needle_levels       : σ 水準線とクランプ境界の辞書。
    compute_adx_needle  : 純粋計算（numpy 配列入出力）。
    compute_adx / compute_level_count / compute_sigma_levels : 部品計算。
    load_ohlc_csv       : CSV → OHLC DataFrame。
    AdxNeedleResult     : 計算成果の不変 DTO。
    各種定数・列名（DEFAULT_PERIOD / SIGMA_LEVELS / NEEDLE_COLUMN ほか）。

典型:
    >>> from src import load_ohlc_csv, build_adx_needle
    >>> df = load_ohlc_csv("ohlc.csv")
    >>> res = build_adx_needle(df, period=6)
"""

from __future__ import annotations

from .core import (
    APPLIED_PRICES,
    DEFAULT_PERIOD,
    SIGMA_LEVELS,
    AdxNeedleResult,
    compute_adx,
    compute_adx_needle,
    compute_level_count,
    compute_sigma_levels,
    ps_level_count,
)
from .loader import load_ohlc_csv
from .needle import (
    ADX_COLUMN,
    LEVEL_COLUMN,
    NEEDLE_COLUMN,
    build_adx_needle,
    needle_levels,
)

__all__ = [
    "build_adx_needle",
    "needle_levels",
    "compute_adx_needle",
    "compute_adx",
    "compute_level_count",
    "compute_sigma_levels",
    "ps_level_count",
    "load_ohlc_csv",
    "AdxNeedleResult",
    "DEFAULT_PERIOD",
    "SIGMA_LEVELS",
    "APPLIED_PRICES",
    "NEEDLE_COLUMN",
    "LEVEL_COLUMN",
    "ADX_COLUMN",
]
