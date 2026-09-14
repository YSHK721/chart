"""QueryJobInteractor / FetchJobResultInteractor の単体検定。

固定する不変条件:
    1. 照会時に**台帳の状態と子プロセスの生死を突き合わせる**（本 Phase の SRP 分割）。
       台帳は状態だけ、起動器はプロセスだけを知る。両者を照合して遷移を確定するのは
       usecase の仕事であり、その照合点はこの 1 箇所に閉じる。
       - 実行中 かつ 子プロセス継続      → 実行中のまま
       - 実行中 かつ 終了コード 0        → 完了
       - 実行中 かつ 終了コード ≠ 0      → 失敗（終了コードを理由に含める）
    2. 終端状態は再照合しない（完了したジョブを poll の結果で書き換えない）。
    3. **部分結果は非公開**（§12.7 fail-stop）。結果を返すのは完了状態のときだけで、
       実行中・失敗・取消では :class:`ResultNotAvailableError`。
    4. 未知の識別子は :class:`JobNotFoundError`。
"""
from __future__ import annotations

import pytest

from simulator.sim_ui.domain.simulation_job import JobStatus, SimulationJob
from simulator.sim_ui.tests.integration._fake_ports import (
    FakeLauncher,
    FakeLedger,
    terminal,
)
from simulator.sim_ui.usecase.fetch_job_result import FetchJobResultInteractor
from simulator.sim_ui.usecase.job_models import (
    JobNotFoundError,
    ResultNotAvailableError,
)
from simulator.sim_ui.usecase.query_job import QueryJobInteractor


def _running(ledger: FakeLedger, job_id: str = "j1") -> None:
    ledger.jobs[job_id] = SimulationJob.received(job_id).to(JobStatus.RUNNING)


# --- 1. 状態照合 -----------------------------------------------------------

def test_子プロセスが継続中なら実行中のまま() -> None:
    # Arrange
    ledger, launcher = FakeLedger(), FakeLauncher()
    _running(ledger)
    launcher.return_codes["j1"] = None
    # Act
    got = QueryJobInteractor(ledger=ledger, launcher=launcher).execute("j1")
    # Assert
    assert got.status == JobStatus.RUNNING.value


def test_子プロセスが正常終了したら完了になる() -> None:
    # Arrange
    ledger, launcher = FakeLedger(), FakeLauncher()
    _running(ledger)
    launcher.finish("j1", 0)
    # Act
    got = QueryJobInteractor(ledger=ledger, launcher=launcher).execute("j1")
    # Assert
    assert got.status == JobStatus.COMPLETED.value
    assert ledger.load("j1").status is JobStatus.COMPLETED


@pytest.mark.parametrize("rc", [1, 2, -15])
def test_子プロセスが異常終了したら失敗になり理由に終了コードが載る(rc: int) -> None:
    """§4.2 F-3 例外条件: 異常終了は「失敗」へ遷移させ、部分結果を公開しない。"""
    # Arrange
    ledger, launcher = FakeLedger(), FakeLauncher()
    _running(ledger)
    launcher.finish("j1", rc)
    # Act
    got = QueryJobInteractor(ledger=ledger, launcher=launcher).execute("j1")
    # Assert
    assert got.status == JobStatus.FAILED.value
    assert str(rc) in got.failure_reason


def test_受付状態は子プロセスの生死で書き換えない() -> None:
    """まだ起動していない（＝poll が None を返す）段階を完了扱いにしない。"""
    # Arrange
    ledger, launcher = FakeLedger(), FakeLauncher()
    ledger.jobs["j1"] = SimulationJob.received("j1")
    # Act
    got = QueryJobInteractor(ledger=ledger, launcher=launcher).execute("j1")
    # Assert
    assert got.status == JobStatus.RECEIVED.value


@pytest.mark.parametrize(
    "status", [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]
)
def test_終端状態は再照合しない(status: JobStatus) -> None:
    """取消済みのジョブが、後から拾った終了コード 0 で「完了」に戻らないこと。"""
    # Arrange
    ledger, launcher = FakeLedger(), FakeLauncher()
    ledger.jobs["j1"] = terminal("j1", status)
    launcher.return_codes["j1"] = 0
    # Act
    got = QueryJobInteractor(ledger=ledger, launcher=launcher).execute("j1")
    # Assert
    assert got.status == status.value
    assert ledger.update_calls == [], "終端状態を書き換えている"


def test_並列の2ジョブは互いの状態を混ぜない() -> None:
    """§12.7 並列実行。ジョブごとに独立して照合されること。"""
    # Arrange
    ledger, launcher = FakeLedger(), FakeLauncher()
    _running(ledger, "j1")
    _running(ledger, "j2")
    launcher.finish("j1", 0)
    launcher.return_codes["j2"] = None
    sut = QueryJobInteractor(ledger=ledger, launcher=launcher)
    # Act
    a = sut.execute("j1")
    b = sut.execute("j2")
    # Assert
    assert a.status == JobStatus.COMPLETED.value
    assert b.status == JobStatus.RUNNING.value


def test_未知のジョブ識別子はJobNotFoundError() -> None:
    with pytest.raises(JobNotFoundError):
        QueryJobInteractor(ledger=FakeLedger(), launcher=FakeLauncher()).execute("x")


# --- 2. 結果取得（部分結果の非公開・§12.7）--------------------------------

def _fetch(ledger, launcher):
    return FetchJobResultInteractor(
        ledger=ledger,
        query=QueryJobInteractor(ledger=ledger, launcher=launcher),
    )


def test_完了ジョブの結果は所在が返る() -> None:
    # Arrange
    ledger, launcher = FakeLedger(), FakeLauncher()
    _running(ledger)
    launcher.finish("j1", 0)
    # Act
    got = _fetch(ledger, launcher).execute("j1", "stats.json")
    # Assert
    assert got == "/fake/j1/stats.json"


@pytest.mark.parametrize(
    "status", [JobStatus.FAILED, JobStatus.CANCELLED]
)
def test_失敗と取消のジョブは結果を公開しない(status: JobStatus) -> None:
    """§12.7 fail-stop: 部分的な結果ペイロードを公開しない。"""
    # Arrange
    ledger, launcher = FakeLedger(), FakeLauncher()
    ledger.jobs["j1"] = terminal("j1", status)
    # Act / Assert
    with pytest.raises(ResultNotAvailableError):
        _fetch(ledger, launcher).execute("j1", "stats.json")


def test_実行中のジョブは結果を公開しない() -> None:
    """途中まで書かれた stats.json を掴ませない。"""
    # Arrange
    ledger, launcher = FakeLedger(), FakeLauncher()
    _running(ledger)
    launcher.return_codes["j1"] = None
    # Act / Assert
    with pytest.raises(ResultNotAvailableError):
        _fetch(ledger, launcher).execute("j1", "stats.json")


def test_取消の直後に完了へ化けて結果が出ることはない() -> None:
    """取消は終端確定（§12.7）。後から終了コード 0 を拾っても公開されない。"""
    # Arrange
    ledger, launcher = FakeLedger(), FakeLauncher()
    ledger.jobs["j1"] = SimulationJob.received("j1").to(JobStatus.RUNNING).to(
        JobStatus.CANCELLED
    )
    launcher.return_codes["j1"] = 0
    # Act / Assert
    with pytest.raises(ResultNotAvailableError):
        _fetch(ledger, launcher).execute("j1", "stats.json")


def test_結果取得の未知識別子はJobNotFoundError() -> None:
    with pytest.raises(JobNotFoundError):
        _fetch(FakeLedger(), FakeLauncher()).execute("x", "stats.json")
