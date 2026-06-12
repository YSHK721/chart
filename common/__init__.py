"""lib — MQL 移植で横断的に再利用する共有プリミティブ層。

特定の指標に属さず、複数の指標から再利用される純粋ロジック（numpy のみ）を置く。

公開 API:
    AppliedPrice                       : 適用価格の種別（MQL ENUM_APPLIED_PRICE 互換 ＋ OHLC4 拡張）。
    applied_price                      : 種別で 8 種を切り替えるディスパッチャ。
    close_price / open_price / high_price / low_price : 単純な列選択。
    median_price / typical_price / weighted_price / ohlc4_price : 算術合成。
    level_colors                       : レベルカウント系の値→HEX 色（緑→赤・|中心からの距離|）写像。

典型的な使い方:
    >>> import numpy as np
    >>> from lib import applied_price, AppliedPrice
    >>> high = np.array([10.0, 20.0]); low = np.array([2.0, 4.0])
    >>> close = np.array([8.0, 16.0]); open_ = np.array([5.0, 12.0])
    >>> applied_price(AppliedPrice.TYPICAL, open_, high, low, close)
    array([ 6.66666667, 13.33333333])
"""

from __future__ import annotations

from .applied_price import (
    AppliedPrice,
    applied_price,
    close_price,
    high_price,
    low_price,
    median_price,
    ohlc4_price,
    open_price,
    typical_price,
    weighted_price,
)
from .level_colors import level_colors

__all__ = [
    "AppliedPrice",
    "applied_price",
    "close_price",
    "open_price",
    "high_price",
    "low_price",
    "median_price",
    "typical_price",
    "weighted_price",
    "ohlc4_price",
    "level_colors",
]
