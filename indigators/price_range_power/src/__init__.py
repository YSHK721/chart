"""価格帯別ブルベアレシオ（PriceRangePower）— VBA インジケーターの Python 移植。

元 VBA（``sample/VBA/TECNICAL_ANALYSIS.cls`` の ``PriceRangePower``、UserForm
``PriceRangePower.frm`` から起動）は、OHLC を価格帯（級）別に集計し、各バーの始値→
終値方向（陽線/陰線）で分類したヒゲ幅の ±1/2/3σ 度数と、その比率（ブル/ベアレシオ）を
シート上の表に書き出す。本パッケージはガイド（.doc/PORTING_GUIDE.md）に従い、計算
（core/ratio）と入出力アダプタ（loader/plot/lwc_chart）を分離する。

公開 API:
    build_price_range_power : OHLC DataFrame → 成果物 DataFrame（度数14 + 比率12 + 合計）。
    build_bull_bear_profile : 帯別ブル/ベア勢力の要約 DataFrame（描画・分析用）。
    compute_price_range_power : 純粋計算（numpy 配列入出力）。
    load_ohlc_csv           : CSV → OHLC DataFrame。
    PrpResult / WickStats   : 計算成果の不変 DTO。
    各種定数・列名（DEFAULT_INTERVAL / COUNT_COLUMNS / RATIO_COLUMNS / TOTAL_COLUMN）。

典型:
    >>> from src import load_ohlc_csv, build_price_range_power
    >>> df = load_ohlc_csv("ohlc.csv")
    >>> res = build_price_range_power(df, interval=0.1)
"""

from __future__ import annotations

from .core import (
    COUNT_COLUMNS,
    DEFAULT_INTERVAL,
    INTERVAL_CHOICES,
    RATIO_COLUMNS,
    TOTAL_COLUMN,
    WICK_NAMES,
    PrpResult,
    WickStats,
    build_price_bands,
    compute_price_range_power,
    round_up,
    wick_samples,
    wick_stats,
)
from .loader import load_ohlc_csv
from .ratio import (
    PRP_INDEX_NAME,
    build_bull_bear_profile,
    build_price_range_power,
    result_to_frame,
)

__all__ = [
    "build_price_range_power",
    "build_bull_bear_profile",
    "result_to_frame",
    "compute_price_range_power",
    "load_ohlc_csv",
    "PrpResult",
    "WickStats",
    "build_price_bands",
    "wick_samples",
    "wick_stats",
    "round_up",
    "DEFAULT_INTERVAL",
    "INTERVAL_CHOICES",
    "WICK_NAMES",
    "COUNT_COLUMNS",
    "RATIO_COLUMNS",
    "TOTAL_COLUMN",
    "PRP_INDEX_NAME",
]
