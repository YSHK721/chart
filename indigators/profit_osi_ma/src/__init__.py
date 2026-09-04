"""PRO!fit_OSI_MA — MQL4 インジケーターの Python 移植（公開 API）。

元 MQL4 ``PRO!fit_OSI_MA.mq4`` は終値の移動平均（MAMode 選択, 既定 EMA(21)）から
の乖離率 MAKairi を別ウィンドウに描画する。本パッケージは PORTING_GUIDE §8 に従い
core 層（純粋計算）・成果物層（pandas）と入出力アダプタ（loader/plot/lwc_chart）を
分離する。

公開 API:
    compute_osi_ma : 純粋計算（numpy 配列入出力）。
    build_osi_ma   : close 列 DataFrame → KAIRI_COLUMN 1 列の成果物 DataFrame。
    osi_ma_levels  : 水準線（±1.0 / ±0.5）の辞書。
    load_ohlc_csv  : CSV → OHLC DataFrame（入力アダプタ）。
    add_osi_ma     : lightweight-charts（duck typing）への系列追加。
    （plot_osi_ma は matplotlib 依存のため src.plot から直接 import する。）
    各種定数（MA_MODES / DEFAULT_MA_MODE / DEFAULT_MA_PERIOD / KAIRI_COLUMN）。

典型:
    >>> from src import load_ohlc_csv, build_osi_ma, osi_ma_levels
    >>> df = load_ohlc_csv("ohlc.csv")        # close 必須（列名の大小不問）
    >>> out = build_osi_ma(df, ma_mode=1, ma_period=21)
"""

from __future__ import annotations

from .core import (
    DEFAULT_MA_MODE,
    DEFAULT_MA_PERIOD,
    MA_MODES,
    compute_osi_ma,
)
from .loader import load_ohlc_csv
from .lwc_chart import add_osi_ma
from .osi_ma import (
    KAIRI_COLUMN,
    build_osi_ma,
    osi_ma_levels,
)

__all__ = [
    "compute_osi_ma",
    "build_osi_ma",
    "osi_ma_levels",
    "load_ohlc_csv",
    "add_osi_ma",
    "MA_MODES",
    "DEFAULT_MA_MODE",
    "DEFAULT_MA_PERIOD",
    "KAIRI_COLUMN",
]
