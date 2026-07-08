"""PRO!fit_Volatility — MQL4 インジケーターの Python 移植（core + 成果物層）。

元 MQL4（``sample/MQL4/Indicators/PRO!fit_Volatility.mq4`` + ``ProfitSystem/PS.mqh`` の
``iVOLATILITY``）は、現足の価格 X と period 本前の価格 Y の乖離 ``pX[a]-pY[a-period]`` を
49 系列（X∈0..6 × Y∈0..6 = price_A × price_B）で算出し、各値を「平均からの σ 距離」へ
単位変換して加算した「価格乖離の温度」（レベルカウント）を別ウィンドウのヒストグラムで表示する。

本パッケージは profit_arctan / profit_adx_needle と同型構造で、オシレーターのみ
iVOLATILITY（49 系列）に置換する。``ps_level_count`` / ``compute_sigma_levels`` は
共有層 ``profit_system`` から供給される（import 再公開。profit_adx_needle と同一実装を参照）。

公開 API（core + 成果物層 + 入出力アダプタ）:
    load_ohlc_csv          : 入力アダプタ（CSV → OHLC DataFrame）。
    build_volatility       : OHLC DataFrame → 成果物 DataFrame（クランプ済みレベルカウント）。
    volatility_levels      : σ 水準線の辞書。
    add_volatility         : 出力アダプタ（lightweight-charts へヒスト1本＋σ12水準線を追加）。
    compute_volatility_full: 純粋計算（numpy 配列入出力）。
    compute_volatility / compute_level_count / compute_volatility_levels : 部品計算。
    VolatilityResult       : 計算成果の不変 DTO。
    各種定数・列名。

注記:
    matplotlib 描画（``src.plot.plot_volatility``）は matplotlib 依存を本パッケージの
    import に持ち込まないため公開 API から除外し、個別 import で利用する。
"""

from __future__ import annotations

from .core import (
    DEFAULT_PERIOD,
    DEFAULT_WINDOW,
    SIGMA_LEVELS,
    VOLATILITY_MODES,
    CoreVolatilityResult,
    VolatilityResult,
    compute_core_divergence,
    compute_core_volatility,
    compute_level_count,
    compute_sigma_levels,
    compute_volatility,
    compute_volatility_full,
    compute_volatility_levels,
    ps_level_count,
)
from .loader import load_ohlc_csv
from .lwc_chart import add_volatility
from .volatility import (
    LEVEL_COUNT_COLUMN,
    build_volatility,
    volatility_levels,
)

__all__ = [
    "load_ohlc_csv",
    "build_volatility",
    "volatility_levels",
    "add_volatility",
    "compute_core_volatility",
    "compute_core_divergence",
    "compute_volatility_full",
    "compute_volatility",
    "compute_level_count",
    "compute_volatility_levels",
    "compute_sigma_levels",
    "ps_level_count",
    "CoreVolatilityResult",
    "VolatilityResult",
    "DEFAULT_PERIOD",
    "DEFAULT_WINDOW",
    "SIGMA_LEVELS",
    "VOLATILITY_MODES",
    "LEVEL_COUNT_COLUMN",
]
