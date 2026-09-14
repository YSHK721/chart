"""`GET /jobs/{id}` 応答の ``terminal`` フラグの検定（Phase 9 段階 3 S4b・§19.6 R1）。

なぜ要るか: front は「実行が終わったか」を判定して監視を止める必要がある。判定に必要な
終端集合（完了 / 失敗 / 取消）は :class:`JobStatus` の不変条件（domain）であり、front が
同じ集合を書けば **domain 規則の第 2 実装**になる（列挙が増えた・取消の扱いが変わった
ときに UI だけ古くなる）。したがって終端判定はサーバが配り、front はそれを読むだけにする。

固定する不変条件:
    1. ``JobView.terminal`` は全状態で :attr:`JobStatus.is_terminal` と一致する
       （状態を 1 つずつ走査する＝終端集合をこの検定へ写さない）。
    2. HTTP 応答 payload に ``terminal`` が載る。
    3. **既存キーは 1 つも変わらない**（追加のみ・job_id / status / failure_reason）。
"""
from __future__ import annotations

from simulator.sim_ui.adapter.job_api_controller import JobApiController
from simulator.sim_ui.domain.simulation_job import JobStatus, SimulationJob
from simulator.sim_ui.usecase.job_models import JobView

#: 追加前から配っていたキー（このどれかが欠けたら後方互換の破れ）。
_EXISTING_KEYS = {"job_id", "status", "failure_reason"}


def _job(status: JobStatus) -> SimulationJob:
    """当該状態の :class:`SimulationJob` を遷移規則を通さずに組む（照会の写しだけを見る）。"""
    reason = "boom" if status is JobStatus.FAILED else None
    return SimulationJob(job_id="j1", status=status, failure_reason=reason)


class _StubQuery:
    def __init__(self, view: JobView) -> None:
        self._view = view

    def execute(self, job_id: str) -> JobView:  # noqa: ARG002 - 識別子は使わない
        return self._view


# --- 1. domain の終端規則がそのまま届く -------------------------------------

def test_JobViewのterminalは全状態でis_terminalと一致する() -> None:
    # Arrange / Act / Assert（終端集合をここへ写さず、domain の判定と 1 対 1 で突き合わせる）
    for status in JobStatus:
        view = JobView.of(_job(status))
        assert view.terminal is status.is_terminal, status


# --- 2. HTTP 応答に載る -----------------------------------------------------

def test_照会応答のpayloadにterminalが載る() -> None:
    # Arrange
    view = JobView.of(_job(JobStatus.COMPLETED))
    controller = JobApiController(
        submit=None, query=_StubQuery(view), cancel=None, fetch_result=None
    )
    # Act
    response = controller.query("j1")
    # Assert
    assert response.status == 200
    assert response.payload["terminal"] is True


def test_実行中の照会応答はterminalが偽である() -> None:
    # Arrange
    view = JobView.of(_job(JobStatus.RUNNING))
    controller = JobApiController(
        submit=None, query=_StubQuery(view), cancel=None, fetch_result=None
    )
    # Act
    response = controller.query("j1")
    # Assert
    assert response.payload["terminal"] is False


# --- 3. 追加のみ（既存キーは 1 つも変わらない）-------------------------------

def test_照会応答の既存キーは1つも失われていない() -> None:
    # Arrange
    view = JobView.of(_job(JobStatus.FAILED))
    controller = JobApiController(
        submit=None, query=_StubQuery(view), cancel=None, fetch_result=None
    )
    # Act
    payload = controller.query("j1").payload
    # Assert
    assert _EXISTING_KEYS <= set(payload), payload
    assert payload["job_id"] == "j1"
    assert payload["status"] == JobStatus.FAILED.value
    assert payload["failure_reason"] == "boom"
