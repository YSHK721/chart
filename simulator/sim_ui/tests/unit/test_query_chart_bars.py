"""`query_chart_bars`（usecase・ISSUE-552/554 段階 2-1）。

固定する契約:
    1. 位置の半開区間 `[start, end)` の行を、宣言された列で**間引かず**返す。
    2. 上限を超える区間は**読まずに**断る（数えるのが先・読みは通ったときだけ）。
    3. 区間の形が不正なら数えも読みもしない。位置の上界（`MAX_POSITION`）を超える区間も同じ。
    4. 宣言（行数・列・指標）は読み口からそのまま返す（行を読まない）。

計算量（絶対命令 2026-08-28）: Port の代役が発行を記録する（Port は宣言された境界である）。
    - 読みの発行 − 返した読み = 0（読んで捨てる読みが無い）
    - 断るときの読みの発行 = 0
    - run の長さ 2 点で、同じ区間の読みの発行と読む行数が一致する
"""
from __future__ import annotations

import pytest

from simulator.sim_ui.usecase import query_chart_bars
from simulator.sim_ui.usecase.chart_bars_ports import ChartBarsDeclaration, ChartBarsPort
from simulator.sim_ui.usecase.query_chart_bars import (
    MAX_RETURNED_BARS,
    ChartBarsRangeTooWideError,
    QueryChartBarsInteractor,
)

_JOB = "a" * 32
_COLUMNS = ("bar_index", "time", "close", "equity", "indicator_0")


class _RecordingSource(ChartBarsPort):
    """``rows`` 行の run を持つ読み口の代役。発行（数え・読み）を記録する。"""

    def __init__(self, rows: int) -> None:
        self._rows = rows
        self.counts: "list[tuple[int, int]]" = []
        self.reads: "list[tuple[tuple[str, ...], int, int]]" = []
        self.rows_read = 0

    def declaration(self, job_id: str) -> ChartBarsDeclaration:
        return ChartBarsDeclaration(
            rows=self._rows, index_column="bar_index", columns=_COLUMNS,
            indicators=({"series": "sma", "placement": "price", "column": "indicator_0"},),
            timeframe="1m", ea_name="CalcProbe_EA", dataset_ref=None, time_unit="epoch_seconds",
        )

    def _inside(self, start: int, end: int) -> range:
        return range(max(start, 0), min(end, self._rows))

    def count(self, job_id: str, *, start: int, end: int) -> int:
        self.counts.append((start, end))
        return len(self._inside(start, end))

    def read(self, job_id: str, *, columns, start: int, end: int) -> "dict[str, list]":
        self.reads.append((tuple(columns), start, end))
        inside = list(self._inside(start, end))
        self.rows_read += len(inside)
        return {name: [float(i) if name != "bar_index" else i for i in inside] for name in columns}


def _query(rows: int) -> "tuple[QueryChartBarsInteractor, _RecordingSource]":
    source = _RecordingSource(rows)
    return QueryChartBarsInteractor(source=source), source


class TestTheRangeIsReturnedWhole:
    def test_the_half_open_range_comes_back_in_the_declared_columns(self):
        # Arrange
        query, _source = _query(100)

        # Act
        got = query.rows(_JOB, start=10, end=13)

        # Assert
        assert (got.start, got.end, got.rows) == (10, 13, 3)
        assert tuple(got.columns) == _COLUMNS
        assert got.columns["bar_index"] == [10, 11, 12]

    def test_a_range_past_the_end_returns_the_rows_that_exist(self):
        query, _source = _query(100)
        got = query.rows(_JOB, start=95, end=200)
        assert got.rows == 5 and got.columns["bar_index"] == [95, 96, 97, 98, 99]

    def test_an_empty_range_returns_zero_rows(self):
        query, _source = _query(100)
        got = query.rows(_JOB, start=7, end=7)
        assert got.rows == 0 and got.columns["bar_index"] == []

    def test_a_range_at_the_cap_is_accepted(self):
        query, _source = _query(MAX_RETURNED_BARS + 5)
        assert query.rows(_JOB, start=0, end=MAX_RETURNED_BARS).rows == MAX_RETURNED_BARS


class TestAnOversizedRangeIsRefusedWithoutReading:
    def test_one_row_over_the_cap_is_refused(self):
        # Arrange
        query, source = _query(MAX_RETURNED_BARS + 5)

        # Act / Assert
        with pytest.raises(ChartBarsRangeTooWideError, match=str(MAX_RETURNED_BARS)):
            query.rows(_JOB, start=0, end=MAX_RETURNED_BARS + 1)
        # 断るときの読みの発行 = 0（読んでから捨てない）。数えは行っている（正の対照）。
        assert source.reads == []
        assert source.counts == [(0, MAX_RETURNED_BARS + 1)]


class TestAMalformedRangeIsRefusedBeforeAnyQuestion:
    @pytest.mark.parametrize("start, end", [(5, 4), (-1, 3), (0, -2)])
    def test_it_neither_counts_nor_reads(self, start, end):
        query, source = _query(100)
        with pytest.raises(ValueError):
            query.rows(_JOB, start=start, end=end)
        assert (source.counts, source.reads) == ([], [])


class TestAPositionPastTheBoundIsRefusedBeforeAnyQuestion:
    """位置の上界は `MAX_POSITION` の宣言から導く（値を検定へ書き写さない）。"""

    @pytest.mark.parametrize("over_start, over_end", [(None, 1), (1, 2), (None, 10**30)])
    @pytest.mark.parametrize("run_rows", [100, 1_000_000])
    def test_it_neither_counts_nor_reads(self, over_start, over_end, run_rows):
        # Arrange: 上界からの超過ぶんで区間を作る（``None`` は上界の内側の 0）。
        bound = query_chart_bars.MAX_POSITION
        start = 0 if over_start is None else bound + over_start
        end = bound + over_end
        query, source = _query(run_rows)

        # Act / Assert
        with pytest.raises(ValueError, match=str(bound)):
            query.rows(_JOB, start=start, end=end)
        # 発行した問い（数え・読み）= 0。run の長さ 2 点で変わらない。
        assert len(source.counts) + len(source.reads) == 0

    def test_the_bound_itself_is_accepted_and_clipped_to_the_run(self):
        """上界は「run の末尾を越えた区間は在る行だけ返す」を狭めない（正の対照）。"""
        bound = query_chart_bars.MAX_POSITION
        query, source = _query(100)
        got = query.rows(_JOB, start=95, end=bound)
        assert got.rows == 5 and source.rows_read - got.rows == 0
        assert query.rows(_JOB, start=bound, end=bound).rows == 0


class TestTheExtentIsTheDeclaration:
    def test_it_reads_no_row(self):
        query, source = _query(100)
        extent = query.extent(_JOB)
        assert (extent.rows, extent.columns) == (100, _COLUMNS)
        assert (source.counts, source.reads) == ([], [])


class TestNothingIsReadAndDiscarded:
    def test_every_issued_read_is_returned(self):
        # Arrange
        query, source = _query(1_000)

        # Act
        got = query.rows(_JOB, start=100, end=400)

        # Assert: 読みの発行 − 返した読み = 0・読んだ行 − 返した行 = 0・読んだ列 − 返した列 = 0。
        assert len(source.reads) - 1 == 0
        assert source.rows_read - got.rows == 0
        assert set(source.reads[0][0]) - set(got.columns) == set()
        assert got.rows == 300  # 正の対照: 0 行どうしを比べていない。

    def test_the_cost_is_set_by_the_range_not_by_the_run_length(self):
        observed = []
        for run_rows in (10_000, 1_000_000):
            query, source = _query(run_rows)
            got = query.rows(_JOB, start=2_000, end=3_500)
            observed.append((len(source.counts), len(source.reads), source.rows_read, got.rows))
        # run を 100 倍にしても、同じ区間の発行と読む行数は変わらない。
        assert observed[0] == observed[1], observed
        assert observed[0][2] == 1_500
