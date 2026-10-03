"""ブロック高値・安値の計算本体（純関数）。

定義:
    N = 全バー数。段 k のブロック単位数 ``L_k = floor(N / 2^k)``（``L_k >= 1`` の間）。
    段 k では先頭から長さ L_k のブロックを並べ、余り ``N mod L_k`` を最後のブロックにする。
    同値の高値（安値）が複数あるときは最も早いバーを採る。

観測の境界（計算量検定の注入点・宣言）:
    バーの読み出しは :class:`BarSource` 経由のみで行う。計算量検定はこの注入点を
    Test Spy で包んで発行を数える（内部名の差し替えはしない）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol

import numpy as np


class BarSource(Protocol):
    """バー系列の読み出し口（計算量検定の注入点）。"""

    def __len__(self) -> int: ...

    def highs(self) -> np.ndarray: ...

    def lows(self) -> np.ndarray: ...

    def times(self, indices: np.ndarray) -> np.ndarray: ...


@dataclass(frozen=True)
class LevelExtrema:
    """1 段ぶんの結果。配列はブロック順に並ぶ。"""

    bars: np.ndarray      # 各ブロックの実バー数
    high: np.ndarray      # 各ブロックの高値
    high_at: np.ndarray   # 高値を付けたバーの位置
    low: np.ndarray       # 各ブロックの安値
    low_at: np.ndarray    # 安値を付けたバーの位置


def block_lengths(n: int) -> Iterator[int]:
    """段 k = 0,1,2,… のブロック単位数 ``floor(n / 2^k)`` を 1 まで返す。"""
    length = n
    while length >= 1:
        yield length
        length //= 2


def _first_extreme(values: np.ndarray, starts: np.ndarray, ufunc: np.ufunc) -> "tuple[np.ndarray, np.ndarray]":
    """各ブロックの極値と、それを最初に付けた位置を返す（1 段 O(N)）。"""
    extreme = ufunc.reduceat(values, starts)
    sizes = np.diff(np.append(starts, len(values)))
    block_of = np.repeat(np.arange(len(starts)), sizes)
    hits = np.flatnonzero(values == extreme[block_of])
    _, first = np.unique(block_of[hits], return_index=True)
    return extreme, hits[first]


def block_extrema(source: BarSource) -> Iterator[LevelExtrema]:
    """段ごとに全ブロックの高値・安値と位置を返す。全体 O(N log N)。"""
    n = len(source)
    if n == 0:
        return
    highs = np.asarray(source.highs())
    lows = np.asarray(source.lows())
    for length in block_lengths(n):
        starts = np.arange(0, n, length)
        bars = np.diff(np.append(starts, n))
        high, high_at = _first_extreme(highs, starts, np.maximum)
        low, low_at = _first_extreme(lows, starts, np.minimum)
        yield LevelExtrema(bars=bars, high=high, high_at=high_at, low=low, low_at=low_at)
