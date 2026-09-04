"""U-CancelJob: ジョブ取消（FR-12・§12.7 依頼者裁定）。

規則:
    1. 状態遷移の妥当性を**先に**確かめる。終端（完了 / 失敗 / 取消）なら
       :class:`JobTransitionError` を投げ、**SIGTERM を送らない**。
       順序が逆だと、既に終わったジョブの PID（OS に再利用され得る）を撃つ。
    2. 遷移が成立してから子プロセスへ SIGTERM を送り、取消状態を保存する。
    3. 部分結果は非公開（§12.7 fail-stop）。これは「消す」のではなく
       「完了以外は結果を出さない」規則（`fetch_job_result`）で保証する。
"""
from __future__ import annotations

from simulator.sim_ui.domain.simulation_job import JobStatus
from simulator.sim_ui.usecase.job_models import JobNotFoundError, JobView
from simulator.sim_ui.usecase.job_ports import JobLauncherPort, JobLedgerPort


class CancelJobInteractor:
    """実行中／受付中のジョブを取り消して終端確定させる。"""

    def __init__(self, *, ledger: JobLedgerPort, launcher: JobLauncherPort) -> None:
        self._ledger = ledger
        self._launcher = launcher

    def execute(self, job_id: str) -> JobView:
        """取消して現在状態を返す。未知は JobNotFoundError／終端は JobTransitionError。"""
        job = self._ledger.load(job_id)
        if job is None:
            raise JobNotFoundError(f"未知のジョブ識別子です: {job_id}")

        # 先に遷移を確定させる（終端ならここで送出され、SIGTERM は送られない）。
        cancelled = job.to(JobStatus.CANCELLED)

        self._launcher.terminate(job_id)
        # compare-and-set。load してから書くまでに子の終了が反映されていたら書かない
        # （後勝ちで終端を塗り替えない）。
        self._ledger.update(cancelled, expect=job.status)
        return JobView.of(cancelled)
