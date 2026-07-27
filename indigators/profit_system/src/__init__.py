"""profit_system — ProfitSystem ``PS.mqh`` レベルカウント系プリミティブの共有実装。

入出力・描画を含まない純粋な数値計算ライブラリ（依存は numpy のみ）。
profit_adx_needle / profit_arctan / profit_oscillator / profit_volatility が
重複保持していた PS プリミティブを 1:1 で集約した正準実装。

公開 API（合成レベル）:
    ps_level_count       : PS_GetLevelCountValue（系列 → σ 距離単位へ変換・加算）
    compute_sigma_levels : iBandsOnArray σ12 水準（up_067..up_329 / dn_*）
    SIGMA_LEVELS         : σ 水準定数（0.67〜3.29）
    level_count_score    : funLevelCount 4 ケース採点（profit_rmm 正準形を集約）
    compute_marod        : MAROD = (typical-ma)/ma*100（profit_rmm 正準形を集約）

公開 API（PS プリミティブ。パッケージ境界を越えて参照される＝公開契約。ISSUE-182 項目 1）:
    ps_normalize         : NormalizeDouble(x, 5)
    ps_average           : PS_GetAverage（算術平均・Normalize 5）
    ps_std_ema           : iStdDevOnArray(..., MODE_EMA) 相当の標準偏差
    ps_unit_conversion   : PS_GetUnitConversion（オシレーター値 → σ 距離単位）

    旧アンダースコア名（``_normalize`` / ``_ps_average`` / ``_ps_std_ema`` /
    ``_unit_conversion``）は ``core`` 側に同一オブジェクトの別名として残るが、
    公開面ではない（``__all__`` に載せない）。新規参照は public 名を使うこと。

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
    ps_average,
    ps_level_count,
    ps_normalize,
    ps_std_ema,
    ps_unit_conversion,
)

__all__ = [
    "ps_level_count",
    "compute_sigma_levels",
    "SIGMA_LEVELS",
    "level_count_score",
    "compute_marod",
    "ps_normalize",
    "ps_average",
    "ps_std_ema",
    "ps_unit_conversion",
]
