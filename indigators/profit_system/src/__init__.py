"""profit_system — ProfitSystem ``PS.mqh`` レベルカウント系プリミティブの共有実装。

入出力・描画を含まない純粋な数値計算ライブラリ（依存は numpy のみ）。
profit_adx_needle / profit_arctan / profit_oscillator / profit_volatility が
重複保持していた PS プリミティブを 1:1 で集約した正準実装。

公開 API:
    ps_level_count       : PS_GetLevelCountValue（系列 → σ 距離単位へ変換・加算）
    compute_sigma_levels : iBandsOnArray σ12 水準（up_067..up_329 / dn_*）
    SIGMA_LEVELS         : σ 水準定数（0.67〜3.29）
    level_count_score    : funLevelCount 4 ケース採点（profit_rmm 正準形を集約）
    compute_marod        : MAROD = (typical-ma)/ma*100（profit_rmm 正準形を集約）

典型的な使い方:
    >>> import numpy as np
    >>> from profit_system import ps_level_count
    >>> lc = ps_level_count(np.array([1.0, 2.0, 3.0]), initialization=True)
"""

from __future__ import annotations

from .core import (
    SIGMA_LEVELS,
    compute_marod,
    compute_sigma_levels,
    level_count_score,
    ps_level_count,
)

__all__ = [
    "ps_level_count",
    "compute_sigma_levels",
    "SIGMA_LEVELS",
    "level_count_score",
    "compute_marod",
]
