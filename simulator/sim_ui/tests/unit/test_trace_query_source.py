"""`trace_query_source`（adapter・RUN_TRACE_BASIC_DESIGN §9.3）。

`TracePointsPort` の実装。**store への束縛**と、公開可否の関門（完了判定・部分結果の
非公開）を通すことだけを負う。

公開可否の規則を**書き直さない**（§9.4「借りるもの」）:
    「完了したジョブに限り結果を公開する」は `usecase/fetch_job_result.py` が唯一持ち、
    `/data/{job}/{file}` 配信口も同じ関門を通っている。ここで別の判定を書くと、
    同じ問いに 2 つの答えが生まれる（片方だけ緩む形で必ず食い違う）。
"""
from __future__ import annotations

import json

import pytest

from simulator.adapter.trace import parquet_trace_store as store
from simulator.adapter.trace.columnar_run_trace import COLUMNS
from simulator.sim_ui.adapter.trace_query_source import TraceQuerySource
from simulator.sim_ui.adapter.trace_writer import META_FILENAME, POINTS_FILENAME
from simulator.sim_ui.usecase.trace_query_ports import (
    TraceArtefactMissingError,
    TracePointsPort,
)

_T0 = 1_704_067_200_000
_STEP = 1000
_JOB = "0123456789abcdef01234567"
_DEPOSIT = 25_000.0
_FLOOR = 99.95


class _Gate:
    """公開可否の関門の代役（FetchJobResultInteractor と同じ面）。"""

    def __init__(self, path, *, error=None):
        self._path = path
        self._error = error
        self.calls: "list[tuple]" = []

    def execute(self, job_id, filename):
        self.calls.append((job_id, filename))
        if self._error is not None:
            raise self._error
        return self._path / filename


def _write_job(tmp_path, *, rows=40, deposit=_DEPOSIT, floor=_FLOOR):
    job_dir = tmp_path / _JOB
    job_dir.mkdir()
    columns = {name: [] for name in COLUMNS}
    for i in range(rows):
        columns["time"].append(_T0 + i * _STEP)
        columns["bar_index"].append(i)
        columns["tick_ordinal"].append(0)
        columns["granularity"].append("tick")
        columns["is_synthetic"].append(False)
        columns["eval_bid"].append(1.0)
        columns["eval_ask"].append(1.1)
        columns["balance"].append(deposit)
        columns["equity"].append(deposit - i)
        columns["floating_pnl"].append(-float(i))
        columns["margin"].append(10.0)
        columns["margin_level"].append(1_000.0)
        columns["swap"].append(0.0)
        columns["commission"].append(0.0)
        columns["open_count"].append(1)
        columns["open_volume_buy"].append(1.0)
        columns["open_volume_sell"].append(0.0)
        columns["halted"].append(False)
    store.write_columns(job_dir / POINTS_FILENAME, columns)
    (job_dir / META_FILENAME).write_text(
        json.dumps({"job_id": _JOB, "rows": {"points": rows}}), encoding="utf-8"
    )
    spec = {
        "backtest": {"initial_deposit": deposit, "stop_out_level": floor},
        "trace": {"enabled": True},
    }
    (job_dir / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    return job_dir


@pytest.fixture
def source(tmp_path):
    job_dir = _write_job(tmp_path)
    gate = _Gate(job_dir)
    return TraceQuerySource(result_gate=gate), gate


# ---- 1: Port の面を満たす ----

class TestItImplementsThePort:
    def test_it_is_a_trace_points_port(self, source):
        assert isinstance(source[0], TracePointsPort)


# ---- 2: 範囲と run の設定値 ----

class TestTheExtentComesFromTheArtefactAndTheSpec:
    def test_it_reports_the_rows_and_bounds_from_the_parquet_footer(self, source):
        # Arrange
        query, _gate = source

        # Act
        got = query.extent(_JOB)

        # Assert
        assert got.rows == 40
        assert (got.first_time, got.last_time) == (_T0, _T0 + 39 * _STEP)

    def test_the_margin_floor_is_the_runs_own_stop_out_level(self, source):
        """閾値を発明しない——run が設定した値（stop_out_level）を読む。"""
        # Arrange
        query, _gate = source

        # Act / Assert
        assert query.extent(_JOB).margin_level_floor == _FLOOR

    def test_the_initial_deposit_is_the_runs_own(self, source):
        # Arrange
        query, _gate = source

        # Act / Assert
        assert query.extent(_JOB).initial_deposit == _DEPOSIT

    def test_a_run_without_a_stop_out_level_has_no_floor(self, tmp_path):
        """設定が無ければ閾値は無い（0.0 を「割れない閾値」として持ち回らない）。"""
        # Arrange
        job_dir = tmp_path / _JOB
        _write_job(tmp_path)
        spec = json.loads((job_dir / "spec.json").read_text(encoding="utf-8"))
        del spec["backtest"]["stop_out_level"]
        (job_dir / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        query = TraceQuerySource(result_gate=_Gate(job_dir))

        # Act / Assert
        assert query.extent(_JOB).margin_level_floor is None


# ---- 3: 読みと件数 ----

class TestItDelegatesTheReadingToTheStore:
    def test_it_returns_the_requested_columns_inside_the_window(self, source):
        # Arrange
        query, _gate = source

        # Act
        got = query.read(
            _JOB, columns=("time", "equity"), start=_T0 + 10 * _STEP,
            end=_T0 + 15 * _STEP,
        )

        # Assert
        assert list(got) == ["time", "equity"]
        assert len(got["time"]) == 5

    def test_it_counts_without_reading(self, source, monkeypatch):
        # Arrange
        query, _gate = source
        calls = []
        real = store.read_columns
        monkeypatch.setattr(
            store, "read_columns",
            lambda *a, **k: (calls.append(k), real(*a, **k))[1],
        )

        # Act
        got = query.count(_JOB, start=_T0 + 10 * _STEP, end=_T0 + 15 * _STEP)

        # Assert
        assert got == 5
        assert calls == [], calls


# ---- 4: 公開可否の関門は借りる（書き直さない） ----

class TestThePublicationGateIsBorrowed:
    def test_every_access_goes_through_the_gate(self, source):
        # Arrange
        query, gate = source

        # Act
        query.extent(_JOB)
        query.count(_JOB)
        query.read(_JOB, columns=("time",))

        # Assert: 所在を得るたびに関門を通っている（迂回路を作っていない）。
        assert len(gate.calls) >= 3
        assert {job for job, _f in gate.calls} == {_JOB}

    def test_an_incomplete_job_is_refused_by_the_gate(self, tmp_path):
        # Arrange
        from simulator.sim_ui.usecase.job_models import ResultNotAvailableError

        job_dir = _write_job(tmp_path)
        gate = _Gate(job_dir, error=ResultNotAvailableError("未完了"))
        query = TraceQuerySource(result_gate=gate)

        # Act / Assert: 関門の例外をそのまま通す（握って 0 行にしない）。
        with pytest.raises(ResultNotAvailableError):
            query.extent(_JOB)

    def test_a_run_without_a_trace_artefact_is_reported_as_missing(self, tmp_path):
        """記録 OFF の run を「0 行だった」と読ませない（別の事実である）。"""
        # Arrange
        job_dir = tmp_path / _JOB
        job_dir.mkdir()
        (job_dir / "spec.json").write_text("{}", encoding="utf-8")
        query = TraceQuerySource(result_gate=_Gate(job_dir))

        # Act / Assert
        with pytest.raises(TraceArtefactMissingError):
            query.extent(_JOB)


# ---- 5: 層の規律 ----

class TestTheLayerDisciplineHolds:
    def test_the_usecase_module_does_not_import_pandas_or_pyarrow(self):
        """`sim_ui/usecase` は素の列だけを受ける（pandas の到達点は store 1 つ）。"""
        # Arrange
        import ast
        import pathlib

        # Act / Assert
        for name in ("query_trace", "derive_trace_events", "trace_query_ports"):
            source = pathlib.Path(
                f"simulator/sim_ui/usecase/{name}.py"
            ).read_text(encoding="utf-8")
            imported = {
                alias.name.split(".")[0]
                for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.Import)
                for alias in node.names
            } | {
                (node.module or "").split(".")[0]
                for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.ImportFrom)
            }
            assert "pandas" not in imported, name
            assert "pyarrow" not in imported, name

    def test_the_returned_columns_carry_no_pandas_types(self, source):
        # Arrange
        query, _gate = source

        # Act
        got = query.read(_JOB, columns=("time", "equity", "halted"))

        # Assert
        assert all(type(t) is int for t in got["time"])
        assert all(type(v) is float for v in got["equity"])
        assert all(type(v) is bool for v in got["halted"])


# ---- 6: 値を発明しない（pre-mortem で見つかった欠陥） ----

class TestTheRunSettingsAreNeverInvented:
    """DD の基準（`initial_deposit`）を黙って 0.0 で埋めない。

    なぜ（自分の pre-mortem で見つかった）:
        当初は spec.json が読めないとき空 dict を返し、`initial_deposit` が 0.0 に
        倒れていた。equity_dd_absolute は `B_0 - min(equity)` なので、基準が 0 に
        なると **DD の金額が黙って誤る**（例外も掲示も出ない）。「既定値で黙って
        埋めない」（§7）に反する。

        stop_out_level は別扱いでよい——不在は「閾値が無い」という**定義された
        意味**を持ち、そのとき割れ事象は存在しない（値の発明ではない）。
    """

    def test_an_unreadable_spec_is_reported_not_defaulted(self, tmp_path):
        # Arrange
        job_dir = _write_job(tmp_path)
        (job_dir / "spec.json").write_text("{ これは JSON ではない", encoding="utf-8")
        query = TraceQuerySource(result_gate=_Gate(job_dir))

        # Act / Assert
        with pytest.raises(TraceArtefactMissingError):
            query.extent(_JOB)

    def test_a_missing_spec_is_reported_not_defaulted(self, tmp_path):
        # Arrange
        job_dir = _write_job(tmp_path)
        (job_dir / "spec.json").unlink()
        query = TraceQuerySource(result_gate=_Gate(job_dir))

        # Act / Assert
        with pytest.raises(TraceArtefactMissingError):
            query.extent(_JOB)

    def test_a_missing_initial_deposit_is_reported_not_defaulted(self, tmp_path):
        """DD の基準が無い run を「基準 0」として黙って分析しない。"""
        # Arrange
        job_dir = _write_job(tmp_path)
        spec = json.loads((job_dir / "spec.json").read_text(encoding="utf-8"))
        del spec["backtest"]["initial_deposit"]
        (job_dir / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        query = TraceQuerySource(result_gate=_Gate(job_dir))

        # Act / Assert
        with pytest.raises(TraceArtefactMissingError) as caught:
            query.extent(_JOB)
        assert "initial_deposit" in str(caught.value)

    def test_a_well_formed_spec_still_works(self, source):
        """正の対照: 何でも例外にしているわけではない。"""
        # Arrange
        query, _gate = source

        # Act / Assert
        assert query.extent(_JOB).initial_deposit == _DEPOSIT
