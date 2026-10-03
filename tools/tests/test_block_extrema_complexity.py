"""ブロック高値・安値ログの計算量検定（絶対命令 2026-08-28）。

観測の境界: 宣言された注入点 ``BarSource``（tools.block_extrema.extrema）を Test Spy で包み、
バーの読み出しと時刻の引き当てを数える（内部名の差し替えはしない）。

表明:
    1. 読んだバー − 出力に使ったバー（全段で全バーを覆う＝N） = 0。段の数だけ読み直さない。
    2. 引いた時刻 − 出力した行 = 0。全バーの時刻を作ってから捨てない。
    3. 入力 N を 2 点で変えても、1 バーあたりの読み出しは増えない。回数そのものは焼き込まない。
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd

from tools.block_extrema.extrema import block_extrema
from tools.block_extrema.report import write_log
from tools.block_extrema.source import FrameBarSource


class _SourceSpy:
    """BarSource の読み出しを数える Test Spy。"""

    def __init__(self, inner: FrameBarSource) -> None:
        self._inner = inner
        self.bars_read = 0
        self.times_read = 0

    def __len__(self) -> int:
        return len(self._inner)

    def highs(self) -> np.ndarray:
        values = self._inner.highs()
        self.bars_read += len(values)
        return values

    def lows(self) -> np.ndarray:
        values = self._inner.lows()
        self.bars_read += len(values)
        return values

    def times(self, indices: np.ndarray) -> np.ndarray:
        self.times_read += len(indices)
        return self._inner.times(indices)


def _run(n: int) -> "tuple[_SourceSpy, int]":
    rng = np.random.default_rng(n)
    highs = rng.normal(100, 5, n)
    index = pd.date_range("2024-01-01", periods=n, freq="min")
    spy = _SourceSpy(FrameBarSource(pd.DataFrame({"high": highs, "low": highs - 1}, index=index)))
    out = io.StringIO()
    write_log(block_extrema(spy), spy, out)
    return spy, len(out.getvalue().splitlines())


def test_reads_each_bar_once_and_looks_up_only_emitted_times():
    for n in (100, 1000):
        spy, rows = _run(n)
        used_bars = 2 * n  # 高値列・安値列とも、出力ブロックが全バーを覆う
        assert spy.bars_read - used_bars == 0
        assert spy.times_read - rows == 0


def test_reads_per_bar_do_not_grow_with_input():
    small, _ = _run(100)
    large, _ = _run(1000)
    assert large.bars_read / 1000 <= small.bars_read / 100
