"""PRO!fit_Arctan — MQL4 インジケーターの Python 移植（core + 成果物層）。

元 MQL4（``sample/MQL4/Indicators/PRO!fit_Arctan.mq4`` + ``ProfitSystem/PS.mqh`` の
``iARCTAN``）は、移動平均の隣接差を ``MathArctan`` で角度（度）へ変換するオシレーター
（iARCTAN）を 7 種の適用価格（W/T/M/H/L/O/C）で算出し、各値を「平均からの σ 距離」へ
単位変換して加算した「市場の温度」（レベルカウント）を別ウィンドウのヒストグラムで表示する。

本パッケージは profit_adx_needle と同型構造で、オシレーターのみ ADX → iARCTAN に置換する。
``ps_level_count`` / ``compute_sigma_levels`` は共有層 ``profit_system`` から供給される
（import 再公開。profit_adx_needle と同一実装を参照）。

公開 API:
    load_ohlc_csv       : CSV → OHLC DataFrame（入力アダプタ）。
    build_arctan        : OHLC DataFrame → 成果物 DataFrame（クランプ済みレベルカウント）。
    arctan_levels       : σ 水準線の辞書。
    add_arctan          : lightweight-charts へヒストグラム＋σ12 水準線を追加（出力アダプタ）。
    compute_arctan_full : 純粋計算（numpy 配列入出力）。
    compute_arctan / compute_level_count / compute_arctan_levels : 部品計算。
    ArctanResult        : 計算成果の不変 DTO。
    各種定数・列名。

注記:
    matplotlib 描画（``src.plot.plot_arctan``）は matplotlib 依存を本パッケージ import に
    持ち込まないため公開 API から除外する（``from src.plot import plot_arctan`` で個別 import）。
"""

from __future__ import annotations

from .arctan import (
    LEVEL_COUNT_COLUMN,
    arctan_levels,
    build_arctan,
)
from .loader import load_ohlc_csv
from .lwc_chart import add_arctan
from .core import (
    APPLIED_PRICES,
    DEFAULT_PERIOD,
    DEFAULT_WINDOW,
    SIGMA_LEVELS,
    ArctanResult,
    compute_arctan,
    compute_arctan_full,
    compute_arctan_levels,
    compute_level_count,
    compute_sigma_levels,
    ps_level_count,
)

__all__ = [
    "load_ohlc_csv",
    "build_arctan",
    "arctan_levels",
    "add_arctan",
    "compute_arctan_full",
    "compute_arctan",
    "compute_level_count",
    "compute_arctan_levels",
    "compute_sigma_levels",
    "ps_level_count",
    "ArctanResult",
    "DEFAULT_PERIOD",
    "DEFAULT_WINDOW",
    "SIGMA_LEVELS",
    "APPLIED_PRICES",
    "LEVEL_COUNT_COLUMN",
]
