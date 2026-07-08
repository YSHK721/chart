"""MovingAverages — MQL5 標準ライブラリ ``MovingAverages.mqh`` の Python 移植。

入出力・描画を含まない純粋な移動平均計算ライブラリ（依存は numpy のみ）。

公開 API（スカラー版・指定位置 1 点）:
    simple_ma            : 単純移動平均（SMA）
    exponential_ma       : 指数移動平均（EMA）
    smoothed_ma          : 平滑移動平均（SMMA / RMA）
    linear_weighted_ma   : 線形加重移動平均（LWMA）

公開 API（バッファ版・配列全体を逐次計算）:
    simple_ma_on_buffer
    exponential_ma_on_buffer
    linear_weighted_ma_on_buffer
    linear_weighted_ma_on_buffer_fast
    smoothed_ma_on_buffer

典型的な使い方:
    >>> import numpy as np
    >>> from moving_averages import simple_ma
    >>> price = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    >>> simple_ma(4, 3, price)
    4.0
"""

from __future__ import annotations

from .core import (
    exponential_ma,
    exponential_ma_on_buffer,
    linear_weighted_ma,
    linear_weighted_ma_on_buffer,
    linear_weighted_ma_on_buffer_fast,
    simple_ma,
    simple_ma_on_buffer,
    smoothed_ma,
    smoothed_ma_on_buffer,
)

__all__ = [
    "simple_ma",
    "exponential_ma",
    "smoothed_ma",
    "linear_weighted_ma",
    "simple_ma_on_buffer",
    "exponential_ma_on_buffer",
    "linear_weighted_ma_on_buffer",
    "linear_weighted_ma_on_buffer_fast",
    "smoothed_ma_on_buffer",
]
