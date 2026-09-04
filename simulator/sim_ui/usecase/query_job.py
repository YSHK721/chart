"""U-QueryJob: ジョブ状態の照会（FR-11）。

本 Interactor が「台帳の状態」と「子プロセスの生死」を突き合わせる**唯一の点**である
（SRP の分割: 台帳は状態だけ・起動器はプロセスだけを知り、意味づけはここが持つ）。
子プロセスが自分で台帳を書かない設計にしているため、SIGKILL 等で子が何も書けずに
死んだ場合でも「実行中のまま固まる」ことがない。

照合規則:
    * 終端（完了 / 失敗 / 取消）: **再照合しない**。§12.7 の終端確定を守る
      （取消済みが後から拾った終了コード 0 で「完了」へ戻らない）。
    * 受付: 書き換えない（まだ起動していない段階を完了扱いにしない）。
    * 実行中: `poll` が ``None`` なら実行中のまま。終了コード 0 なら完了、
      それ以外は失敗（理由に終了コードを載せる・§4.2 F-3 例外条件）。
"""
from __future__ import annotations

from simulator.sim_ui.domain.simulation_job import (
    JobStatus,
    JobTransitionError,
    SimulationJob,
)
from simulator.sim_ui.usecase.job_models import JobNotFoundError, JobView
from simulator.sim_ui.usecase.job_ports import JobLauncherPort, JobLedgerPort


class QueryJobInteractor:
    """ジョブの現在状態を返す（必要なら子プロセスの終了を状態へ反映する）。"""

    def __init__(self, *, ledger: JobLedgerPort, launcher: JobLauncherPort) -> None:
        self._ledger = ledger
        self._launcher = launcher

    def execute(self, job_id: str) -> JobView:
        return JobView.of(self.resolve(job_id))

    def resolve(self, job_id: str) -> SimulationJob:
        """照合済みの :class:`SimulationJob` を返す（結果取得の判定でも使う）。"""
        job = self._ledger.load(job_id)
        if job is None:
            raise JobNotFoundError(f"未知のジョブ識別子です: {job_id}")
        if job.status is not JobStatus.RUNNING:
            return job

        return_code = self._launcher.poll(job_id)
        if return_code is None:
            return job
        if return_code == 0:
            reconciled = job.to(JobStatus.COMPLETED)
        else:
            reconciled = job.to(
                JobStatus.FAILED,
                failure_reason=self._failure_reason(job_id, return_code),
            )
        try:
            # compare-and-set。読み（load）から書きまでの間に取消等が割り込んでいたら
            # 書かない。無条件に書くと CANCELLED を COMPLETED で上書きし、取消した
            # ジョブの結果が公開される（§12.7 の破れ・実測済み）。
            self._ledger.update(reconciled, expect=JobStatus.RUNNING)
        except JobTransitionError:
            # 競合に敗けた＝他者が既に終端を確定させた。勝者の状態を読み直して返す
            # （照会は状態を返すのが契約であり、競合を呼び出し側へ投げない）。
            winner = self._ledger.load(job_id)
            return winner if winner is not None else job
        return reconciled

    def _failure_reason(self, job_id: str, return_code: int) -> str:
        """失敗理由を組み立てる。子が残した理由があればそれを載せる。

        子プロセスは状態を書かない（`run_job.py:14-17`）が、理由だけは
        `failure.json` に残す。無ければ従来どおり終了コードのみ（SIGKILL 等で
        何も書けずに死んだ場合がこれに当たる）。
        """
        detail = self._ledger.read_failure_report(job_id)
        base = f"バックテストの子プロセスが終了コード {return_code} で終了しました"
        return f"{base}: {detail}" if detail else base
