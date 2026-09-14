"""`ColumnarRunTrace` の `time` 列がミリ秒精度であること（§9.0 の是正）。

なぜ（実測・2026-09-10。憶測ではない）:
    実ティック 1 ヶ月 run（`simulator/tests/confirmation/2026-01_ma-market` の
    JP225 2026-01・tick-store 実体あり）を trace ON で実走した
    trace_points.parquet は **1,036,394 行**あり、そのうち
    **407,745 行（39.3%）が他の行と同じ `time` 値**だった（1 秒を最大 14 行が共有）。
    tick-store の実 dtype は `datetime64[us]`／`datetime64[ms]` で秒未満を持つのに、
    `time` 列が `epoch_seconds()` を通って `.astype("datetime64[s]")` で切り捨てられる
    ためである。「ティック粒度での推移」という要件に対する欠陥である。

    分析面の側で同一秒を代表値へ潰すのは**症状の出る条件を避ける形＝対症療法**であり
    （既存 front `chart.js` の `dedupeCurve` がその形）、同一秒内の equity /
    margin_level の谷が消えて DD 分析が壊れる。原因（出力単位）を除去する。

固定する契約:
  1. `time` 列は epoch **ミリ秒**の `int` である（`bar.time` 由来の点は ×1000）。
  2. 同一秒に複数ティックがある入力で、`time` がティックごとに**異なる**。
  3. 窓判定（epoch 秒）と `time` 列（ミリ秒）は**同じ導出値**から来る＝
     記録された行の `time // 1000` は必ず窓の中にある（§6.5.0 の食い違い禁止）。
  4. 導出点は `ColumnarRunTrace` ただ 1 つ（構文木で固定）。
"""
from __future__ import annotations

import ast
import pathlib

import numpy as np

from simulator.adapter.trace.columnar_run_trace import ColumnarRunTrace
from simulator.adapter.trace.trace_window import TraceWindow
from simulator.tests.unit.test_columnar_run_trace import (
    _Account,
    _bar,
    _point,
    _unbounded,
)

_EPOCH = 1_704_067_200  # 2024-01-01T00:00:00Z
_MS = 1000


# ---- 1: 単位はミリ秒 ----

class TestTheTimeColumnIsInMilliseconds:
    def test_a_tick_time_becomes_epoch_millis(self):
        # Arrange
        bar = _bar(0)
        tick_time = bar.time + np.timedelta64(37_500, "ms")
        trace = ColumnarRunTrace(_unbounded())

        # Act
        trace.observe(_point(bar=bar, tick_time=tick_time), _Account(), (), False)

        # Assert
        assert trace.columns["time"] == [(_EPOCH + 37) * _MS + 500]

    def test_a_point_without_a_tick_time_uses_the_bar_time_times_one_thousand(self):
        """`bar.time` 由来の点（BarSchedule の点・ティック 0 件バーの持ち越し点）。"""
        # Arrange
        bar = _bar(5)
        trace = ColumnarRunTrace(_unbounded())

        # Act
        trace.observe(_point(bar=bar, tick_time=None), _Account(), (), False)

        # Assert
        assert trace.columns["time"] == [(_EPOCH + 300) * _MS]

    def test_the_values_are_plain_ints(self):
        # Arrange
        bar = _bar(0)
        trace = ColumnarRunTrace(_unbounded())

        # Act
        trace.observe(
            _point(bar=bar, tick_time=bar.time + np.timedelta64(12_345, "ms")),
            _Account(), (), False,
        )

        # Assert: parquet の int64 列・ブラウザの `Date` が受けるのは素の整数である。
        assert all(type(v) is int for v in trace.columns["time"])

    def test_sub_millisecond_input_is_truncated_not_rounded(self):
        """`datetime64[us]`（confirmation tick-store の実 dtype）を受ける。"""
        # Arrange
        bar = _bar(0)
        trace = ColumnarRunTrace(_unbounded())

        # Act
        trace.observe(
            _point(bar=bar, tick_time=bar.time + np.timedelta64(1_999, "us")),
            _Account(), (), False,
        )

        # Assert
        assert trace.columns["time"] == [_EPOCH * _MS + 1]


# ---- 2: 同一秒の複数ティックが潰れない（是正の目的そのもの） ----

class TestTicksInsideOneSecondStayDistinct:
    #: 実測に基づく標本（confirmation tick-store の 2026-01-29 01:00:57 の実ティック間隔）。
    _OFFSETS_MS = (729, 865, 913, 977, 998)

    def test_five_ticks_in_the_same_second_produce_five_distinct_times(self):
        # Arrange: 秒精度なら 5 行すべてが同値になる入力。
        bar = _bar(0)
        trace = ColumnarRunTrace(_unbounded())

        # Act
        for ordinal, offset in enumerate(self._OFFSETS_MS):
            trace.observe(
                _point(
                    bar=bar,
                    tick_time=bar.time + np.timedelta64(offset, "ms"),
                    tick_ordinal=ordinal,
                ),
                _Account(), (), False,
            )

        # Assert
        times = trace.columns["time"]
        assert len(times) == len(self._OFFSETS_MS)
        assert len(set(times)) == len(times), times
        # 正の対照: 秒へ落とすと**全部が同値**になる入力で測っている
        # （これが偽なら「潰れないこと」を測っていない）。
        assert len({t // _MS for t in times}) == 1

    def test_the_recorded_order_is_strictly_increasing_in_time(self):
        """時間軸として読めること（同値があれば lightweight-charts は系列を描けない）。"""
        # Arrange
        bar = _bar(0)
        trace = ColumnarRunTrace(_unbounded())

        # Act
        for ordinal, offset in enumerate(self._OFFSETS_MS):
            trace.observe(
                _point(bar=bar, tick_time=bar.time + np.timedelta64(offset, "ms"),
                       tick_ordinal=ordinal),
                _Account(), (), False,
            )

        # Assert
        times = trace.columns["time"]
        assert all(b > a for a, b in zip(times, times[1:])), times


# ---- 3: 窓（epoch 秒）と `time` 列（ミリ秒）の一致 ----

class TestTheWindowAndTheColumnCannotDisagree:
    """§6.5.0: 記録された行の秒位は必ず窓の中にある。"""

    def test_every_recorded_row_falls_inside_the_window_in_seconds(self):
        # Arrange: 窓は epoch **秒**で受ける（§6.4・JSON 整数の契約は変えない）。
        start, end = _EPOCH + 120, _EPOCH + 300
        window = TraceWindow.of(start, end)
        trace = ColumnarRunTrace(window)
        bar = _bar(0)
        # 秒未満を持つ点を窓の内外にまたがって並べる。
        offsets_ms = [0, 119_999, 120_000, 120_500, 299_999, 300_000, 400_000]

        # Act
        for ordinal, off in enumerate(offsets_ms):
            trace.observe(
                _point(bar=bar, tick_time=bar.time + np.timedelta64(off, "ms"),
                       tick_ordinal=ordinal),
                _Account(), (), False,
            )

        # Assert
        times = trace.columns["time"]
        assert all(start <= t // _MS < end for t in times), times
        # 期待値は入力から導く（数字を焼き込まない）。
        assert times == [
            _EPOCH * _MS + off for off in offsets_ms if start <= (_EPOCH * _MS + off) // _MS < end
        ]
        # 正の対照: 窓が全部を通しても全部を落としてもいない。
        assert 0 < len(times) < len(offsets_ms), (times, offsets_ms)

    def test_a_sub_second_point_on_the_closing_boundary_is_excluded(self):
        """`end` ちょうどの秒に属する秒未満の点は半開 `[start, end)` の外である。"""
        # Arrange
        start, end = _EPOCH, _EPOCH + 60
        trace = ColumnarRunTrace(TraceWindow.of(start, end))
        bar = _bar(0)

        # Act: 60.500 秒（秒位 = end）は入らない。59.999 秒は入る。
        trace.observe(
            _point(bar=bar, tick_time=bar.time + np.timedelta64(59_999, "ms")),
            _Account(), (), False,
        )
        trace.observe(
            _point(bar=bar, tick_time=bar.time + np.timedelta64(60_500, "ms"),
                   tick_ordinal=1),
            _Account(), (), False,
        )

        # Assert
        assert trace.columns["time"] == [_EPOCH * _MS + 59_999]


# ---- 4: 導出点は 1 箇所（構文木） ----

class TestTheDerivationHappensExactlyOnce:
    """時刻の正規化呼出が `observe` に 1 つだけであること（§6.5.0）。

    2 箇所で導出すると「窓が通した点の `time` 列が窓の外」が**例外を出さずに**起こる。
    単位を増やしたことで導出が 2 回（秒用とミリ秒用）になる形を機械的に禁じる。
    """

    #: 時刻正規化の単一ソースが公開する関数名（`simulator/domain/bar_time.py`）。
    _NORMALISERS = ("epoch_seconds", "epoch_millis")

    def test_the_module_calls_a_time_normaliser_exactly_once(self):
        # Arrange
        source = pathlib.Path(
            "simulator/adapter/trace/columnar_run_trace.py"
        ).read_text(encoding="utf-8")

        # Act
        calls = [
            node.func.id
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in self._NORMALISERS
        ]

        # Assert
        assert calls == ["epoch_millis"], calls
