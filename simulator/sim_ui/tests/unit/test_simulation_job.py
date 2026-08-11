"""SimulationJob（ジョブ状態遷移の domain 規則）の単体検定。

固定する不変条件（基本設計書 §4.2 F-3・§12.7）:
    1. 状態集合は 受付 / 実行中 / 完了 / 失敗 / 取消 の 5 値。
    2. 終端状態（完了・失敗・取消）からの遷移は**不変条件違反**（例外）。
       ※ 「取消は終端確定・部分結果非公開」（§12.7）を状態機械の側で構造的に保証する。
    3. 取消は 受付・実行中 の双方から可能（子プロセス起動前の取消も成立する）。
    4. 失敗は理由を伴い、状態から失敗理由が読める（§4.2 F-3 後条件）。
    5. SimulationJob は不変オブジェクトであり、遷移は新インスタンスを返す
       （既存インスタンスは書き換わらない＝並列実行時に共有しても壊れない）。

方式: domain 単体（I/O なし・F.I.R.S.T の Fast/Independent/Repeatable を満たす）。
"""
from __future__ import annotations

import dataclasses

import pytest

from simulator.sim_ui.domain.simulation_job import (
    JobStatus,
    JobTransitionError,
    SimulationJob,
)


# --- 1. 状態集合 -----------------------------------------------------------

def test_状態集合は5値である() -> None:
    # Arrange / Act
    values = {s.value for s in JobStatus}
    # Assert
    assert values == {"received", "running", "completed", "failed", "cancelled"}


def test_終端状態は完了_失敗_取消の3つである() -> None:
    assert JobStatus.COMPLETED.is_terminal
    assert JobStatus.FAILED.is_terminal
    assert JobStatus.CANCELLED.is_terminal
    assert not JobStatus.RECEIVED.is_terminal
    assert not JobStatus.RUNNING.is_terminal


# --- 2. 正常系の遷移 -------------------------------------------------------

def test_新規ジョブは受付状態で生成される() -> None:
    # Arrange / Act
    job = SimulationJob.received("job-1")
    # Assert
    assert job.job_id == "job-1"
    assert job.status is JobStatus.RECEIVED
    assert job.failure_reason is None


def test_受付から実行中へ遷移できる() -> None:
    # Arrange
    job = SimulationJob.received("job-1")
    # Act
    started = job.to(JobStatus.RUNNING)
    # Assert
    assert started.status is JobStatus.RUNNING


def test_実行中から完了へ遷移できる() -> None:
    # Arrange
    job = SimulationJob.received("job-1").to(JobStatus.RUNNING)
    # Act
    done = job.to(JobStatus.COMPLETED)
    # Assert
    assert done.status is JobStatus.COMPLETED


@pytest.mark.parametrize("origin", [JobStatus.RECEIVED, JobStatus.RUNNING])
def test_取消は受付と実行中の双方から可能(origin: JobStatus) -> None:
    """§12.7: 取消は子プロセス起動前（受付）でも成立させる。"""
    # Arrange
    job = SimulationJob.received("job-1")
    if origin is JobStatus.RUNNING:
        job = job.to(JobStatus.RUNNING)
    # Act
    cancelled = job.to(JobStatus.CANCELLED)
    # Assert
    assert cancelled.status is JobStatus.CANCELLED


@pytest.mark.parametrize("origin", [JobStatus.RECEIVED, JobStatus.RUNNING])
def test_失敗は受付と実行中の双方から可能で理由を伴う(origin: JobStatus) -> None:
    # Arrange
    job = SimulationJob.received("job-1")
    if origin is JobStatus.RUNNING:
        job = job.to(JobStatus.RUNNING)
    # Act
    failed = job.to(JobStatus.FAILED, failure_reason="子プロセスが異常終了した")
    # Assert
    assert failed.status is JobStatus.FAILED
    assert failed.failure_reason == "子プロセスが異常終了した"


# --- 3. 不変条件違反（終端状態からの遷移） ---------------------------------

@pytest.mark.parametrize(
    "terminal", [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]
)
@pytest.mark.parametrize(
    "target",
    [
        JobStatus.RECEIVED,
        JobStatus.RUNNING,
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    ],
)
def test_終端状態からの遷移は不変条件違反(
    terminal: JobStatus, target: JobStatus
) -> None:
    """§12.7: 終端（完了/失敗/取消）は確定。取消後に完了へ戻る等を構造的に禁じる。"""
    # Arrange（FAILED への到達には理由が要る＝不変条件 4）
    running = SimulationJob.received("job-1").to(JobStatus.RUNNING)
    job = (
        running.to(JobStatus.FAILED, failure_reason="setup")
        if terminal is JobStatus.FAILED
        else running.to(terminal)
    )
    assert job.status is terminal
    # Act / Assert
    with pytest.raises(JobTransitionError):
        job.to(target)


def test_受付から完了へ直接遷移できない() -> None:
    """実行中を経ない完了は「走らせずに結果がある」＝台帳の嘘になる。"""
    # Arrange
    job = SimulationJob.received("job-1")
    # Act / Assert
    with pytest.raises(JobTransitionError):
        job.to(JobStatus.COMPLETED)


def test_実行中から受付へ戻れない() -> None:
    # Arrange
    job = SimulationJob.received("job-1").to(JobStatus.RUNNING)
    # Act / Assert
    with pytest.raises(JobTransitionError):
        job.to(JobStatus.RECEIVED)


def test_同一状態への自己遷移は不変条件違反() -> None:
    # Arrange
    job = SimulationJob.received("job-1")
    # Act / Assert
    with pytest.raises(JobTransitionError):
        job.to(JobStatus.RECEIVED)


def test_不変条件違反の例外は遷移元と遷移先を含む() -> None:
    """状態遷移の失敗が「どこからどこへ」で読めること（無音の誤動作を作らない）。"""
    # Arrange
    job = SimulationJob.received("job-1").to(JobStatus.CANCELLED)
    # Act
    with pytest.raises(JobTransitionError) as exc:
        job.to(JobStatus.COMPLETED)
    # Assert
    message = str(exc.value)
    assert "cancelled" in message
    assert "completed" in message


# --- 4. 不変オブジェクト ---------------------------------------------------

def test_遷移は新インスタンスを返し元インスタンスを書き換えない() -> None:
    # Arrange
    job = SimulationJob.received("job-1")
    # Act
    started = job.to(JobStatus.RUNNING)
    # Assert
    assert job.status is JobStatus.RECEIVED
    assert started is not job


def test_SimulationJobは凍結データクラスである() -> None:
    # Arrange
    job = SimulationJob.received("job-1")
    # Act / Assert
    assert dataclasses.is_dataclass(job)
    with pytest.raises(dataclasses.FrozenInstanceError):
        job.status = JobStatus.RUNNING  # type: ignore[misc]


# --- 5. 失敗理由の規則 -----------------------------------------------------

def test_失敗以外の遷移に失敗理由を与えるのは不変条件違反() -> None:
    """失敗理由は失敗状態にのみ結び付く（完了なのに理由が残る状態を作らない）。"""
    # Arrange
    job = SimulationJob.received("job-1")
    # Act / Assert
    with pytest.raises(JobTransitionError):
        job.to(JobStatus.RUNNING, failure_reason="なにか")


def test_失敗遷移で理由を省略するのは不変条件違反() -> None:
    """§4.2 F-3「失敗時は失敗理由が状態に含まれる」を状態機械側で強制する。"""
    # Arrange
    job = SimulationJob.received("job-1")
    # Act / Assert
    with pytest.raises(JobTransitionError):
        job.to(JobStatus.FAILED)
