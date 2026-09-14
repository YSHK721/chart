"""ma_marod パッケージ公開 API。

新インジケーター ma_marod（移動平均乖離率・MA 種別選択式）。moving_averages core の
4 種 MA（sma/ema/smma/lwma）とソース写像を参照実装として再利用し（計算の原子＝価格
ソースは MA と同期・単一経路）、基準線からの相対偏差（%）を別 pane のオシレータとして
供給する。バンドは btlm_trail_marod core の系列汎用関数を無改変参照する。確定バー不変
（非リペイント・前進逐次計算）。ライブ・リプレイで実装は単一。

基本設計: .doc/MA_MAROD_BASIC_DESIGN.md
"""

from __future__ import annotations

from .core import (
    DEFAULT_EVENT_AGG,
    DEFAULT_K_EVENTS,
    DEFAULT_LENGTH,
    DEFAULT_MA_TYPE,
    DEFAULT_Q_HIGH,
    DEFAULT_Q_LOW,
    DEFAULT_Q_OUT,
    DEFAULT_SOURCE,
    DEFAULT_WINDOW_N,
    SIGMA_MULT,
    ma_marod_outlier_event_quantiles,
    ma_marod_quantile_bands,
    ma_marod_series,
    ma_marod_sigma_band,
)
from .lwc_chart import add_ma_marod

__all__ = [
    "DEFAULT_EVENT_AGG",
    "DEFAULT_K_EVENTS",
    "DEFAULT_LENGTH",
    "DEFAULT_MA_TYPE",
    "DEFAULT_Q_HIGH",
    "DEFAULT_Q_LOW",
    "DEFAULT_Q_OUT",
    "DEFAULT_SOURCE",
    "DEFAULT_WINDOW_N",
    "SIGMA_MULT",
    "add_ma_marod",
    "ma_marod_outlier_event_quantiles",
    "ma_marod_quantile_bands",
    "ma_marod_series",
    "ma_marod_sigma_band",
]
