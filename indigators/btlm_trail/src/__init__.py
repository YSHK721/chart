"""btlm_trail パッケージ公開 API。

新インジケーター btlm_trail。tgp_btlm の ols 参照実装（``OlsBtlmFitter``）と数値一致する
回帰窓末尾値を各バーでローリング算出し、トレンド現在位置（ドット）・傾き β・残差 σ・
2 方式（名目 ols / 経験分位）バンド・実現被覆率を提供する。確定バー不変（非リペイント）。

正本仕様: /root/.claude/plans/kind-twirling-hollerith.md
"""

from __future__ import annotations

from .core import (
    DEFAULT_EMP_N,
    DEFAULT_MAXBARS,
    DEFAULT_N_COV,
    DEFAULT_Q_HIGH,
    DEFAULT_Q_LOW,
    norm_ppf,
    resolve_source,
    rolling_ols_window_end,
)
from .trail import (
    TrailResult,
    build_btlm_trail,
    realized_coverage_latest,
    rolling_coverage,
)

__all__ = [
    "DEFAULT_EMP_N",
    "DEFAULT_MAXBARS",
    "DEFAULT_N_COV",
    "DEFAULT_Q_HIGH",
    "DEFAULT_Q_LOW",
    "TrailResult",
    "build_btlm_trail",
    "norm_ppf",
    "realized_coverage_latest",
    "resolve_source",
    "rolling_coverage",
    "rolling_ols_window_end",
]
