"""PRO!fit_Band — MQL5 インジケーターの Python 再設計（計算ライブラリ）。

公開 API:
    load_ohlc_csv      : CSV から OHLC を読み込む。
    build_bands        : OHLC DataFrame から統計バンド DataFrame を生成する（大域・絶対）。
    build_robust_bands : 正規化＋因果窓で頑健化したバンド（先読み除去・スケール不変）。
    collect_distance_samples / compute_quantiles : 中間計算を直接利用する場合。
    PROBABILITIES, BUCKETS : 既定の確率・バケット定義。

典型的な使い方:
    >>> from profit_band import load_ohlc_csv, build_bands
    >>> df = load_ohlc_csv("ohlc.csv")
    >>> bands = build_bands(df)
"""

from __future__ import annotations

from .bands import EmptyBucketError, build_bands
from .core import (
    BUCKETS,
    PROBABILITIES,
    DistanceSamples,
    collect_distance_samples,
    compute_quantiles,
)
from .loader import load_ohlc_csv
from .robust_bands import build_robust_bands

__all__ = [
    "build_bands",
    "EmptyBucketError",
    "build_robust_bands",
    "load_ohlc_csv",
    "collect_distance_samples",
    "compute_quantiles",
    "DistanceSamples",
    "PROBABILITIES",
    "BUCKETS",
]
