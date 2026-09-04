"""CancelJobInteractor（ジョブ取消・FR-12 / §12.7）の単体検定。

固定する不変条件（§12.7 依頼者裁定）:
    1. 取消は子プロセスへ **SIGTERM** を送る（`JobLauncherPort.terminate`）。
    2. 状態は「取消」で**終端確定**する。以後どの遷移も不変条件違反。
    3. **部分結果は非公開**（fail-stop）。取消後に結果取得を試みても公開されない。
    4. 終端状態（完了 / 失敗 / 取消）への取消要求は不変条件違反として弾き、
       **SIGTERM を送らない**（既に終わったジョブの後継 PID を撃たない）。
    5. 未知の識別子は :class:`JobNotFoundError`。
"""
from __future__ import annotations

import pytest

from simulator.sim_ui.domain.simulation_job import (
    JobStatus,
    JobTransitionError,
    SimulationJob,
)
from simulator.sim_ui.tests.integration._fake_ports import (
    FakeLauncher,
    FakeLedger,
    terminal,
)
from simulator.sim_ui.usecase.cancel_job import CancelJobInteractor
from simulator.sim_ui.usecase.job_models import JobNotFoundError


def _sut(ledger, launcher):
    return CancelJobInteractor(ledger=ledger, launcher=launcher)


def _running(ledger: FakeLedger, job_id: str = "j1") -> None:
    ledger.jobs[job_id] = SimulationJob.received(job_id).to(JobStatus.RUNNING)


# --- 1. 正常系 -------------------------------------------------------------

def test_実行中のジョブを取消できる() -> None:
    # Arrange
    ledger, launcher = FakeLedger(), FakeLauncher()
    _running(ledger)
    # Act
    got = _sut(ledger, launcher).execute("j1")
    # Assert
    assert got.status == JobStatus.CANCELLED.value


def test_取消は子プロセスへSIGTERMを送る() -> None:
    # Arrange
    ledger, launcher = FakeLedger(), FakeLauncher()
    _running(ledger)
    # Act
    _sut(ledger, launcher).execute("j1")
    # Assert
    assert launcher.terminated == ["j1"]


def test_取消状態は台帳へ保存される() -> None:
    # Arrange
    ledger, launcher = FakeLedger(), FakeLauncher()
    _running(ledger)
    # Act
    _sut(ledger, launcher).execute("j1")
    # Assert
    assert ledger.load("j1").status is JobStatus.CANCELLED


def test_受付直後のジョブも取消できる() -> None:
    """子プロセス起動前の取消も成立する（§12.7）。"""
    # Arrange
    ledger, launcher = FakeLedger(), FakeLauncher()
    ledger.jobs["j1"] = SimulationJob.received("j1")
    # Act
    got = _sut(ledger, launcher).execute("j1")
    # Assert
    assert got.status == JobStatus.CANCELLED.value


# --- 2. 終端確定（§12.7）--------------------------------------------------

@pytest.mark.parametrize(
    "status", [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]
)
def test_終端状態への取消要求は不変条件違反(status: JobStatus) -> None:
    # Arrange
    ledger, launcher = FakeLedger(), FakeLauncher()
    ledger.jobs["j1"] = terminal("j1", status)
    # Act / Assert
    with pytest.raises(JobTransitionError):
        _sut(ledger, launcher).execute("j1")


@pytest.mark.parametrize(
    "status", [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]
)
def test_終端状態への取消要求ではSIGTERMを送らない(status: JobStatus) -> None:
    """既に終わったジョブの PID を撃つと、再利用された無関係のプロセスを殺しうる。"""
    # Arrange
    ledger, launcher = FakeLedger(), FakeLauncher()
    ledger.jobs["j1"] = terminal("j1", status)
    # Act
    with pytest.raises(JobTransitionError):
        _sut(ledger, launcher).execute("j1")
    # Assert
    assert launcher.terminated == []


def test_二重取消は二度目が不変条件違反になる() -> None:
    # Arrange
    ledger, launcher = FakeLedger(), FakeLauncher()
    _running(ledger)
    sut = _sut(ledger, launcher)
    # Act
    sut.execute("j1")
    # Assert
    with pytest.raises(JobTransitionError):
        sut.execute("j1")
    assert launcher.terminated == ["j1"], "2 度目に SIGTERM を重ねて送っている"


# --- 3. 未知の識別子 -------------------------------------------------------

def test_未知のジョブ識別子はJobNotFoundError() -> None:
    # Arrange
    ledger, launcher = FakeLedger(), FakeLauncher()
    # Act / Assert
    with pytest.raises(JobNotFoundError):
        _sut(ledger, launcher).execute("no-such-job")
    assert launcher.terminated == []
