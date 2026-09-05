"""period_hl core/adapter パッケージ（依存: numpy / pandas・描画ライブラリ非依存）。

- :mod:`core`      … 暦年内の走行高安（純粋計算・numpy のみ）
- :mod:`lwc_chart` … lightweight-charts 出力アダプタ（:func:`add_period_hl` / :func:`add_ytd_hl`）
"""

from .core import year_runs, ytd_running_extremes
from .lwc_chart import add_period_hl, add_ytd_hl

__all__ = ["add_period_hl", "add_ytd_hl", "year_runs", "ytd_running_extremes"]
