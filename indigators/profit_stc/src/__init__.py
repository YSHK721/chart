"""PRO!fitSTC（PRO!fitOscillator）— MQL4 インジケーターの Python 移植（公開 API）。

元 MQL4 ``PRO!fitSTC.mq4`` は終値の高安レンジ位置（Stochastic %K, fast, 既定
period=70）を別ウィンドウに描画し、その系列全体の ±1.0σ/±1.96σ を水準線として
引く（StcLCStdDevArray[1..4]）。本パッケージは PORTING_GUIDE §8 に従い core 層
（純粋計算）と成果物層（pandas）を分離する。

公開 API:
    compute_stochastic : 生 %K（fast, MODE_MAIN）の純粋計算。warm-up/ゼロ割は 0。
    compute_osc_levels : 全系列（warm-up 0 込み）の Bollinger 水準（P1/P2/M1/M2）。
    compute_stc        : 統合 frozen DTO（StcResult）を返す。
    StcResult          : oscillator/levels(P1/P2/M1/M2)/sub_min(=M2)/sub_max(=P2)。
    build_stc          : high/low/close DataFrame → OSC_COLUMN 1 列の成果物。
    stc_levels         : 成果物の {P1,P2,M1,M2,sub_min,sub_max} 水準辞書。
    load_ohlc_csv      : CSV → OHLC DataFrame（入力アダプタ）。
    add_stc            : lightweight-charts へオシレーター線＋水準線を追加（出力アダプタ）。
    定数: DEFAULT_PERIOD / OSC_COLUMN。

注記:
    matplotlib 依存の ``src.plot``（PNG 出力アダプタ）は先例（profit_adx_needle）同様
    本 __init__ から除外する（matplotlib 未導入環境でも import を壊さないため）。

典型:
    >>> from src import load_ohlc_csv, build_stc, stc_levels
    >>> df = load_ohlc_csv("ohlc.csv")      # high/low/close 必須（列名大小不問）
    >>> out = build_stc(df, period=70)
    >>> levels = stc_levels(df, period=70)
"""

from __future__ import annotations

from .core import (
    DEFAULT_PERIOD,
    StcResult,
    compute_osc_levels,
    compute_stc,
    compute_stochastic,
)
from .loader import load_ohlc_csv
from .lwc_chart import add_stc
from .stc import (
    OSC_COLUMN,
    build_stc,
    stc_levels,
)

__all__ = [
    "compute_stochastic",
    "compute_stc",
    "compute_osc_levels",
    "StcResult",
    "build_stc",
    "stc_levels",
    "load_ohlc_csv",
    "add_stc",
    "DEFAULT_PERIOD",
    "OSC_COLUMN",
]
