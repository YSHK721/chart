"""common_view — MQL 移植チャートの「表示仕様」を担う共有プリミティブ層。

`common` が純粋な価格計算（applied_price 系・numpy のみ）を担うのに対し、本パッケージは
チャート描画の**表示仕様**（レベル色写像・水準線の線幅）を担う。計算仕様と表示仕様は
変更を要求するアクターが異なる（計算＝MQL 移植の数値仕様／表示＝UI の視認性仕様）ため、
SRP に基づき common から分離した（ISSUE-092 ⑥）。matplotlib（plot.py）・
lightweight-charts（lwc_chart.py）双方から同一規則で利用する。

公開 API:
    level_colors     : レベルカウント系の値→HEX 色（緑→赤・|中心からの距離|）写像。
    LEVEL_LINE_WIDTH : σ水準線（horizontal_line）の既定線幅（px）の単一ソース。
    EVQ_COLOR / EVQ_LINE_SPECS / emit_event_quantile_lines :
        イベント分位水準線の表示仕様（色・線種・系列名サフィックス）と定型 emit。

典型的な使い方:
    >>> import numpy as np
    >>> from common_view import level_colors
    >>> level_colors(np.array([-3.0, 0.0, 3.0]))  # 両極=赤, 中心=緑
    ['#d32f2f', '#2e7d32', '#d32f2f']
"""

from __future__ import annotations

from .event_quantile_view import EVQ_COLOR, EVQ_LINE_SPECS, emit_event_quantile_lines
from .level_colors import level_colors
from .level_style import LEVEL_LINE_WIDTH

__all__ = [
    "level_colors",
    "LEVEL_LINE_WIDTH",
    "EVQ_COLOR",
    "EVQ_LINE_SPECS",
    "emit_event_quantile_lines",
]
