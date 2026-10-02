"""`ChartBarsSource`（adapter・ISSUE-552/554 段階 2-1）— 実 parquet を位置の区間で読む。

固定する契約:
    1. 書き手（`chart_overlay_writer.write_chart_bars`）が書いた成果物を、位置の半開区間で読む。
    2. 公開可否の関門を**通してから**存在を確かめる（未完了ジョブの成果物を漏らさない）。
    3. 成果物・宣言が無い／読めないは「0 行」ではなく `ChartBarsArtefactMissingError`。

計算量（絶対命令 2026-08-28・検査側の設計 2026-09-25）:
    観測口は読み口が宣言する `parquet_trace_store.set_read_observer` だけを使う（内部名を
    差し替えない）。usecase と実物の source を繋いだ経路で測る。
    - IO 段が組み立てた行 − 返した行 = 0・組み立てた列 − 返した列 = 0
    - 上限で断るときは IO 段の読みが 1 回も起きない
    - run の長さ 2 点（1 万行 / 100 万行）で、同じ区間の読みの数と組み立てる行数が一致する
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from simulator.adapter.trace import parquet_trace_store
from simulator.sim_ui.adapter import chart_overlay_writer
from simulator.sim_ui.adapter.chart_bars_source import ChartBarsSource
from simulator.sim_ui.usecase.chart_bars_ports import ChartBarsArtefactMissingError
from simulator.sim_ui.usecase.job_models import ResultNotAvailableError
from simulator.sim_ui.usecase.query_chart_bars import (
    MAX_RETURNED_BARS,
    ChartBarsRangeTooWideError,
    QueryChartBarsInteractor,
)

_JOB = "b" * 32
_EPOCH = 1_704_067_200


class _Bar:
    __slots__ = ("open", "high", "low", "close")

    def __init__(self, value: float) -> None:
        self.open = self.high = self.low = self.close = value


class _Gate:
    """公開可否の関門の代役（所在を返すか、例外を送出する）。呼ばれた順を記録する。"""

    def __init__(self, directory: Path, *, error: "Exception | None" = None) -> None:
        self._directory = directory
        self._error = error
        self.asked: "list[str]" = []

    def execute(self, job_id: str, filename: str) -> Path:
        self.asked.append(filename)
        if self._error is not None:
            raise self._error
        return self._directory / filename


def _write_run(directory: Path, rows: int) -> Path:
    """書き手の実体で ``rows`` 本の足の成果物を書く（形式をここへ写さない）。"""
    directory.mkdir(parents=True, exist_ok=True)
    values = [float(i) for i in range(rows)]
    account = {name: values for name in chart_overlay_writer.ACCOUNT_COLUMNS}
    chart_overlay_writer.write_chart_bars(
        directory,
        bars=[_Bar(v) for v in values],
        bar_times=[_EPOCH + 60 * i for i in range(rows)],
        account_columns=account,
        series=[{"series": "sma", "placement": "price", "value": values}],
        ea_name="CalcProbe_EA",
        dataset_ref="jp225_mt5_spread",
        stop_out_level=_STOP_OUT_LEVEL,
    )
    return directory


#: 書き手へ渡す水準（台帳の値と違う値）。
_STOP_OUT_LEVEL = 87.5


@pytest.fixture
def io_reads():
    """読み口の観測口へ繋いだ記録（IO 段の読み 1 回につき (行数, 列名)）。"""
    seen: "list[tuple[int, tuple[str, ...]]]" = []
    parquet_trace_store.set_read_observer(lambda rows, columns: seen.append((rows, columns)))
    yield seen
    parquet_trace_store.set_read_observer(None)


class TestItReadsWhatTheWriterWrote:
    def test_the_declaration_comes_from_the_artefact(self, tmp_path: Path):
        # Arrange
        source = ChartBarsSource(result_gate=_Gate(_write_run(tmp_path, 50)))

        # Act
        declared = source.declaration(_JOB)

        # Assert
        assert declared.rows == 50
        assert declared.index_column == chart_overlay_writer.INDEX_COLUMN
        assert declared.columns[: len(chart_overlay_writer.BAR_COLUMNS)] == (
            chart_overlay_writer.BAR_COLUMNS
        )
        assert [dict(i) for i in declared.indicators] == [
            {"series": "sma", "placement": "price", "column": declared.columns[-1]}
        ]
        assert (declared.timeframe, declared.ea_name, declared.dataset_ref, declared.time_unit) == (
            chart_overlay_writer.RUN_TIMEFRAME, "CalcProbe_EA", "jp225_mt5_spread",
            chart_overlay_writer.TIME_UNIT,
        )
        assert declared.stop_out_level == _STOP_OUT_LEVEL

    def test_a_declaration_written_before_the_level_was_declared_has_no_level(self, tmp_path: Path):
        # Arrange: 水準を宣言に書く前に実行したジョブ（実ジョブの chart_bars.json 8 件すべてがこの形・2026-10-02 実測）。
        _write_run(tmp_path, 5)
        path = tmp_path / chart_overlay_writer.CHART_BARS_DECLARATION_FILENAME
        payload = json.loads(path.read_text(encoding="utf-8"))
        del payload["stop_out_level"]
        path.write_text(json.dumps(payload), encoding="utf-8")

        # Act
        declared = ChartBarsSource(result_gate=_Gate(tmp_path)).declaration(_JOB)

        # Assert: 値を発明しない（None＝宣言が名乗らない）。他の宣言は読める。
        assert declared.stop_out_level is None
        assert declared.rows == 5

    @pytest.mark.parametrize("level", ["100", None, float("nan"), True])
    def test_a_level_that_is_not_a_finite_number_is_missing(self, tmp_path: Path, level):
        _write_run(tmp_path, 5)
        path = tmp_path / chart_overlay_writer.CHART_BARS_DECLARATION_FILENAME
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["stop_out_level"] = level
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ChartBarsArtefactMissingError, match="宣言"):
            ChartBarsSource(result_gate=_Gate(tmp_path)).declaration(_JOB)

    def test_the_range_is_half_open_on_the_bar_position(self, tmp_path: Path):
        source = ChartBarsSource(result_gate=_Gate(_write_run(tmp_path, 50)))
        got = source.read(_JOB, columns=["bar_index", "time", "close"], start=10, end=13)
        assert got == {
            "bar_index": [10, 11, 12],
            "time": [_EPOCH + 600, _EPOCH + 660, _EPOCH + 720],
            "close": [10.0, 11.0, 12.0],
        }
        assert source.count(_JOB, start=10, end=13) == 3

    def test_the_count_and_the_read_agree_past_the_end(self, tmp_path: Path):
        source = ChartBarsSource(result_gate=_Gate(_write_run(tmp_path, 50)))
        got = source.read(_JOB, columns=["bar_index"], start=45, end=500)
        assert source.count(_JOB, start=45, end=500) == len(got["bar_index"]) == 5


class TestTheGateComesFirst:
    def test_an_unfinished_job_is_refused_even_if_the_artefact_exists(self, tmp_path: Path):
        # Arrange: 成果物は在るが、関門が公開を拒む。
        _write_run(tmp_path, 5)
        source = ChartBarsSource(
            result_gate=_Gate(tmp_path, error=ResultNotAvailableError("未完了"))
        )

        # Act / Assert
        for ask in (
            lambda: source.declaration(_JOB),
            lambda: source.count(_JOB, start=0, end=1),
            lambda: source.read(_JOB, columns=["bar_index"], start=0, end=1),
        ):
            with pytest.raises(ResultNotAvailableError):
                ask()


class TestAMissingArtefactIsNotZeroRows:
    def test_a_job_without_the_artefact_is_missing(self, tmp_path: Path):
        source = ChartBarsSource(result_gate=_Gate(tmp_path))
        with pytest.raises(ChartBarsArtefactMissingError, match="足の成果物"):
            source.declaration(_JOB)
        with pytest.raises(ChartBarsArtefactMissingError, match="足の成果物"):
            source.read(_JOB, columns=["bar_index"], start=0, end=1)
        with pytest.raises(ChartBarsArtefactMissingError, match="足の成果物"):
            source.count(_JOB, start=0, end=1)

    @pytest.mark.parametrize("text", ["{", "[]", json.dumps({"rows": 3})])
    def test_an_unreadable_declaration_is_missing(self, tmp_path: Path, text: str):
        _write_run(tmp_path, 5)
        (tmp_path / chart_overlay_writer.CHART_BARS_DECLARATION_FILENAME).write_text(
            text, encoding="utf-8"
        )
        source = ChartBarsSource(result_gate=_Gate(tmp_path))
        with pytest.raises(ChartBarsArtefactMissingError, match="宣言"):
            source.declaration(_JOB)


class TestNothingIsBuiltAndDiscarded:
    def _query(self, directory: Path, rows: int) -> QueryChartBarsInteractor:
        return QueryChartBarsInteractor(
            source=ChartBarsSource(result_gate=_Gate(_write_run(directory, rows)))
        )

    def test_the_io_layer_builds_exactly_the_returned_rows_and_columns(
        self, tmp_path: Path, io_reads
    ):
        # Arrange
        query = self._query(tmp_path, 5_000)

        # Act
        got = query.rows(_JOB, start=1_000, end=2_500)

        # Assert: 組み立てた行 − 返した行 = 0・組み立てた列 − 返した列 = 0。
        built_rows = sum(rows for rows, _columns in io_reads)
        built_columns = {name for _rows, columns in io_reads for name in columns}
        assert built_rows - got.rows == 0, (built_rows, got.rows)
        assert built_columns - set(got.columns) == set()
        assert set(got.columns) - built_columns == set()
        # 正の対照: 0 行どうしを比べていない。
        assert got.rows == 1_500 and io_reads

    def test_a_refused_range_reads_nothing(self, tmp_path: Path, io_reads):
        # Arrange
        query = self._query(tmp_path, MAX_RETURNED_BARS + 10)

        # Act / Assert
        with pytest.raises(ChartBarsRangeTooWideError):
            query.rows(_JOB, start=0, end=MAX_RETURNED_BARS + 1)
        assert io_reads == []

    def test_the_extent_reads_nothing(self, tmp_path: Path, io_reads):
        query = self._query(tmp_path, 100)
        assert query.extent(_JOB).rows == 100
        assert io_reads == []

    def test_two_run_lengths_build_the_same_rows_for_the_same_range(
        self, tmp_path: Path, io_reads
    ):
        observed = []
        for run_rows in (10_000, 1_000_000):
            query = self._query(tmp_path / str(run_rows), run_rows)
            del io_reads[:]
            got = query.rows(_JOB, start=2_000, end=3_500)
            observed.append((len(io_reads), sum(rows for rows, _c in io_reads), got.rows))
        # run を 100 倍にしても、同じ区間の読みの数と組み立てる行数は変わらない。
        assert observed[0] == observed[1], observed
        assert observed[0][1] == 1_500
