"""lib — MQL 移植で横断的に再利用する共有プリミティブ層。

特定の指標に属さず、複数の指標から再利用される純粋ロジック（numpy のみ）を置く。

公開 API:
    AppliedPrice                       : 適用価格の種別（MQL ENUM_APPLIED_PRICE 互換 ＋ OHLC4 拡張）。
    applied_price                      : 種別で 8 種を切り替えるディスパッチャ。
    close_price / open_price / high_price / low_price : 単純な列選択。
    median_price / typical_price / weighted_price / ohlc4_price : 算術合成。

表示系（level_colors / LEVEL_LINE_WIDTH 等）は common_view へ分離した（ISSUE-092 ⑥）。本モジュール
（計算・本質・安定層＝numpy のみ依存）から common_view（表示・偶有・可変層）への再エクスポートは
安定度逆転（安定→不安定の依存）を生むため撤去した（ISSUE-104 🟡-1）。表示定数は common_view から
直接 import すること（`from common_view import level_colors, LEVEL_LINE_WIDTH`）。

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
]
