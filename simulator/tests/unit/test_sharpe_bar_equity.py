"""Sharpe Ratio（MT5 の定義・足ごとの有効証拠金・ISSUE-545）の検定。

状態検証: 定義（対数収益・変化の無い足を除く・母標準偏差・√(1 日の足数 × 252)・下限 −5）を
    小さな系列で手計算と突き合わせる。MT5 実レポートとの一致は実 run の検定
    （report_ui の test_export_oracle）が固定する。
計算量: 系列を 1 回だけ読むこと（読んだ要素数 − 系列長 = 0）を長さ 50 / 5000 で表明する。
    観測点は公開の入力（系列・足の列）であり、実装の内部名は差し替えない。
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from types import SimpleNamespace

import numpy as np
import pytest

from simulator.usecase.mt5_parity import (
    SHARPE_FLOOR,
    bar_period_seconds,
    sharpe_ratio_bar_equity,
)


def _by_hand(values, bar_seconds):
    e = np.asarray(values, float)
    a, b = e[:-1], e[1:]
    keep = b != a
    r = np.log(b[keep] / a[keep])
    return r.mean() / r.std() * math.sqrt(86400 / bar_seconds * 252)


def test_matches_the_definition_and_ignores_unchanged_bars():
    values = [10000, 10010, 10010, 10005, 10030, 10030, 10040]
    assert sharpe_ratio_bar_equity(values, 60) == pytest.approx(_by_hand(values, 60))
    # 変化の無い足を足しても値は変わらない（除かれる）。
    padded = [10000, 10000, 10010, 10010, 10010, 10005, 10030, 10040]
    assert sharpe_ratio_bar_equity(padded, 60) == pytest.approx(
        _by_hand([10000, 10010, 10005, 10030, 10040], 60))


def test_the_annualisation_follows_the_bar_length():
    values = [10000, 10010, 10005, 10030, 10040]
    m1 = sharpe_ratio_bar_equity(values, 60)
    m5 = sharpe_ratio_bar_equity(values, 300)
    assert m1 / m5 == pytest.approx(math.sqrt(5))


def test_negative_values_are_floored_at_minus_five():
    falling = [10000, 9990, 9985, 9970, 9960, 9955, 9940]
    assert _by_hand(falling, 60) < SHARPE_FLOOR
    assert sharpe_ratio_bar_equity(falling, 60) == SHARPE_FLOOR == -5.0


def test_degenerate_inputs_yield_zero():
    assert sharpe_ratio_bar_equity([], 60) == 0.0
    assert sharpe_ratio_bar_equity([10000, 10010, 10020], None) == 0.0
    assert sharpe_ratio_bar_equity([10000, 10000, 10000], 60) == 0.0
    # 0 以下の有効証拠金を含む組は使わない（対数が定義されない）。
    assert not math.isnan(sharpe_ratio_bar_equity([10000, 10010, -5, 10020, 10030], 60))


def test_bar_period_is_the_smallest_gap_between_bars():
    bars = [SimpleNamespace(time=t) for t in (0, 60, 120, 3600, 3660)]
    assert bar_period_seconds(bars) == 60.0
    assert bar_period_seconds(bars[:1]) is None


class _CountingSeries(Sequence):
    def __init__(self, values):
        self._values = list(values)
        self.reads = 0

    def __len__(self):
        return len(self._values)

    def __getitem__(self, i):
        if isinstance(i, slice):
            raise TypeError
        self.reads += 1
        return self._values[i]

    def __iter__(self):
        for v in self._values:
            self.reads += 1
            yield v


class _CountingBar:
    reads = 0

    def __init__(self, t):
        self._t = t

    @property
    def time(self):
        _CountingBar.reads += 1
        return self._t


@pytest.mark.parametrize("n", [50, 5000])
def test_each_input_is_read_exactly_once(n):
    series = _CountingSeries(10000.0 + 50.0 * np.sin(np.arange(n) / 7.0) + np.arange(n) * 0.1)
    sharpe_ratio_bar_equity(series, 60)
    assert series.reads - len(series) == 0
    _CountingBar.reads = 0
    bars = [_CountingBar(i * 60) for i in range(n)]
    bar_period_seconds(bars)
    assert _CountingBar.reads - len(bars) == 0
