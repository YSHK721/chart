"""足ごとの口座記録（AccountCurveRecorder）— 値の対応・Fan-out・計算量。

観測の境界:
    `RunTracePort.observe` をエンジンと同じ引数で直接呼ぶ（宣言された境界）。

計算量（回数をリテラルで焼き込まない）:
    行数は「観測した相異なる足の数」から導き、1 足あたりの評価点数を変えても行数が
    増えないこと（評価点ごとに行を作って捨てない）を 2 点以上で表明する。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pytest

from simulator.adapter.trace.account_curve import AccountCurveRecorder
from simulator.usecase.run_trace_ports import FanOutRunTrace

_T0 = 1_704_067_200


@dataclass(frozen=True)
class _Bar:
    time: int


@dataclass(frozen=True)
class _Point:
    bar_index: int
    bar: _Bar


@dataclass
class _Account:
    balance: float
    equity: float
    margin: float
    open_positions: list = field(default_factory=list)

    def margin_level(self) -> float:
        return math.inf if self.margin == 0 else self.equity / self.margin * 100.0


def _drive(recorder, bars: int, points_per_bar: int) -> None:
    for i in range(bars):
        for k in range(points_per_bar):
            # 足の中で値が動く（最後の点の値が残ることを見分けられるように）。
            account = _Account(balance=1000.0 + i, equity=1000.0 + i + k, margin=100.0 * (i % 2))
            recorder.observe(_Point(i, _Bar(_T0 + 60 * i)), account, [], False)


class TestValues:
    def test_each_bar_keeps_the_values_of_its_last_point(self) -> None:
        recorder = AccountCurveRecorder()
        _drive(recorder, bars=3, points_per_bar=4)
        assert recorder.times == [_T0, _T0 + 60, _T0 + 120]
        assert recorder.equity == [1003.0, 1004.0, 1005.0]
        assert recorder.balance == [1000.0, 1001.0, 1002.0]

    def test_margin_level_is_none_without_margin_and_a_percentage_with_it(self) -> None:
        recorder = AccountCurveRecorder()
        _drive(recorder, bars=2, points_per_bar=1)
        assert recorder.margin == [0.0, 100.0]
        assert recorder.margin_level == [None, pytest.approx(1001.0 / 100.0 * 100.0)]

    def test_fan_out_delivers_every_observation_to_every_tracer(self) -> None:
        a, b = AccountCurveRecorder(), AccountCurveRecorder()
        _drive(FanOutRunTrace(a, b), bars=3, points_per_bar=2)
        assert a.times == b.times and a.equity == b.equity
        assert len(a.times) == 3


class TestComplexity:
    @pytest.mark.parametrize(("bars", "points_per_bar"), [(10, 1), (10, 7), (500, 3)])
    def test_rows_are_bars_not_points(self, bars: int, points_per_bar: int) -> None:
        recorder = AccountCurveRecorder()
        _drive(recorder, bars=bars, points_per_bar=points_per_bar)
        columns = (recorder.times, recorder.balance, recorder.equity, recorder.margin, recorder.margin_level)
        assert {len(c) for c in columns} == {bars}
