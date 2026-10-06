"""21EMA 上方乖離ショート規則の計算量検定（絶対命令 2026-08-28）。

観測の境界: 宣言された注入点 ``tools.ema_deviation_short.rules.BarSource`` を Test Spy で包み、
列の読み出し（バー数）を数える（内部名の差し替えはしない）。

表明:
    1. 読んだバー − 判定に使ったバー（open/high/close/spread の 4 列 × 全バー） = 0。
       足ごとに列を読み直さない（EMA を足ごとに作り直さない）。
    2. 入力の長さ・保有本数を 2 点ずつ変えても、1 バーあたりの読み出しは増えない。
       回数そのものは焼き込まない。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tools.ema_deviation_short.rules import simulate
from tools.ema_deviation_short.source import FrameBarSource

_COLUMNS = ("opens", "highs", "closes", "spreads")


class _SourceSpy:
    def __init__(self, inner: FrameBarSource) -> None:
        self._inner = inner
        self.bars_read = 0

    def __len__(self) -> int:
        return len(self._inner)

    def __getattr__(self, name):
        method = getattr(self._inner, name)
        if name not in _COLUMNS:
            return method

        def read():
            values = method()
            self.bars_read += len(values)
            return values

        return read


def _run(n: int, hold: int) -> _SourceSpy:
    rng = np.random.default_rng(n)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.03, n)))
    frame = pd.DataFrame({"date": range(n), "open": closes, "high": closes * 1.05, "close": closes, "spread": 50.0})
    spy = _SourceSpy(FrameBarSource(frame))
    simulate(spy, period=21, deviation=0.08, hold_bars=hold, point=0.1, digits=1, warmup=63)
    return spy


def test_reads_each_column_once():
    for n in (200, 2000):
        for hold in (1, 20):
            spy = _run(n, hold)
            assert spy.bars_read - len(_COLUMNS) * n == 0


def test_reads_per_bar_do_not_grow_with_input_or_hold():
    for hold in (1, 20):
        small, large = _run(200, hold), _run(2000, hold)
        assert large.bars_read / 2000 <= small.bars_read / 200
    for n in (200, 2000):
        assert _run(n, 20).bars_read <= _run(n, 1).bars_read
