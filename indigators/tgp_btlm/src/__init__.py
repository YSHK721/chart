"""!!R-tgp.BTLM-Ind — MQL4 インジケーターの Python 移植（計算ライブラリ）。

元 MQL4 は計算を R（tgp パッケージの btlm: Bayesian Treed Linear Model）へ委譲し、
予測平均と上下分位点（信用区間）を 3 本のラインで描く。本パッケージはガイド
（.doc/PORTING_GUIDE.md）に従い、計算（core/bands）と入出力アダプタ（loader/plot/
lwc_chart）を分離し、R 連携（rbridge）を Protocol 境界の外側へ隔離する。

公開 API:
    build_btlm_bands : 価格 + Fitter → 成果物 DataFrame（btlm_mean / btlm_q{lo} / btlm_q{hi}）。
    load_ohlc_csv    : CSV → OHLC DataFrame。
    TgpBtlmFitter    : R tgp バックエンド（要 R + tgp + rpy2）。
    OlsBtlmFitter    : numpy 参照バックエンド（R 不要・デモ/テスト/フォールバック用）。
    BtlmFitter/BtlmResult, make_design, norm_ppf, 既定定数。

典型:
    >>> from src import load_ohlc_csv, build_btlm_bands, OlsBtlmFitter
    >>> df = load_ohlc_csv("ohlc.csv", time_column="time")
    >>> bands = build_btlm_bands(df, OlsBtlmFitter(), maxbars=100)
"""

from __future__ import annotations

from .bands import build_btlm_bands
from .core import (
    DEFAULT_BTE,
    DEFAULT_MAXBARS,
    DEFAULT_Q_HIGH,
    DEFAULT_Q_LOW,
    DEFAULT_R,
    BtlmFitter,
    BtlmResult,
    make_design,
    mean_column,
    norm_ppf,
    quantile_column,
)
from .loader import load_ohlc_csv
from .rbridge import TgpBtlmFitter
from .reference import OlsBtlmFitter

__all__ = [
    "build_btlm_bands",
    "load_ohlc_csv",
    "TgpBtlmFitter",
    "OlsBtlmFitter",
    "BtlmFitter",
    "BtlmResult",
    "make_design",
    "norm_ppf",
    "mean_column",
    "quantile_column",
    "DEFAULT_MAXBARS",
    "DEFAULT_Q_LOW",
    "DEFAULT_Q_HIGH",
    "DEFAULT_BTE",
    "DEFAULT_R",
]
