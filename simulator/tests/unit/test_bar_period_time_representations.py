"""`main._bar_period` の時刻表現非依存性（ISSUE-403 スライス 2）。

固定する事実:
    P-1: `bar.time` が ``numpy.int64``（comma 形式 CSV を pandas が読むと**必ず**この型に
         なる）のとき、実ティック読込窓は当該バーの epoch 秒でなければならない。
         是正前は ``isinstance(np.int64, int)`` が **False**（実測・numpy 2.4.6）である
         ため epoch 分岐に入らず ``pd.Timestamp(np.int64(1704067200))`` ＝
         ``1970-01-01 00:00:01.704067200`` へ落ちていた（例外なしの桁ずれ）。
    P-2: 窓は「どの時刻表現で書かれたバーか」に依存しない。epoch int / ``numpy.int64`` /
         ``numpy.datetime64`` の同一時刻は同一窓を与える。表現ごとの分岐を
         `_bar_period` が持たない（規則の実体は `simulator.domain.bar_time.epoch_seconds`
         の 1 つだけ）ことの行動固定である。

測り方: `_bar_period` を直接叩く（実経路での到達は
`simulator/tests/integration/test_bar_period_empty_bars.py` /
`test_composition_real_ticks.py` が担う）。
"""
from __future__ import annotations

import numpy as np

from simulator.domain.bar import Bar
from simulator.domain.bar_time import epoch_seconds
from simulator.main import _bar_period

#: 2024-01-01T00:00:00Z。
_E = 1_704_067_200


def _bar(time):
    """`time` だけが判定に効くバー（OHLC/volume/spread は窓算定に無関係の定数）。"""
    return Bar(time=time, open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0, spread=0)


class TestBarPeriodIsIndependentOfTimeRepresentation:
    def test_numpy_int64_bars_yield_the_epoch_window_of_those_bars(self):
        # Arrange: comma 形式 CSV → pandas が返す実型（numpy.int64）の 3 本。
        bars = [_bar(np.int64(_E + 60 * i)) for i in range(3)]
        # Act
        start, end = _bar_period(bars)
        # Assert: [first, last+60s) の epoch 秒。1970 年へ落ちない。
        assert (epoch_seconds(start), epoch_seconds(end)) == (_E, _E + 120 + 60)

    def test_python_int_bars_yield_the_same_window_as_numpy_int64_bars(self):
        # Arrange: 同一時刻を Python int と numpy.int64 で表現した 2 系列。
        py_bars = [_bar(int(_E + 60 * i)) for i in range(3)]
        np_bars = [_bar(np.int64(_E + 60 * i)) for i in range(3)]
        # Act
        py_window = tuple(epoch_seconds(v) for v in _bar_period(py_bars))
        np_window = tuple(epoch_seconds(v) for v in _bar_period(np_bars))
        # Assert: 整数の「種類」で窓が変わらない。
        assert py_window == np_window

    def test_datetime64_bars_yield_the_same_window_as_epoch_int_bars(self):
        # Arrange: MT5 タブ形式ローダの実型（numpy.datetime64）と epoch int の同一 3 本。
        #   既存挙動（是正前は pd.Timestamp 窓）の保全を epoch 秒として固定する。
        #   分は必ず 2 桁で書く（`0{i}` 形式は i>=10 で不正な ISO 文字列になる。同型の
        #   取り違えを `test_ea_factory_registry.py` の合成 CSV で是正済み）。
        dt64_bars = [
            _bar(np.datetime64(f"2024-01-01T00:{i:02d}:00")) for i in range(3)
        ]
        int_bars = [_bar(int(_E + 60 * i)) for i in range(3)]
        # Act
        dt64_window = tuple(epoch_seconds(v) for v in _bar_period(dt64_bars))
        int_window = tuple(epoch_seconds(v) for v in _bar_period(int_bars))
        # Assert
        assert dt64_window == int_window == (_E, _E + 120 + 60)
