"""U-FetchJobResult: 完了ジョブの結果ペイロードの所在を返す（§6.1）。

**部分結果の非公開（§12.7 fail-stop）は、ここ 1 箇所で保証する。**
「取消・失敗のときに書きかけのファイルを消して回る」方式は採らない。消し漏れ・
競合・子プロセスが後から書き足す経路が残り、「消したつもりの部分結果が見える」に
なりうるため。公開の可否は**状態の検査**という単一の関門に閉じる。

結果ペイロードは `run_backtest` の既存出力（stats.json / report.md）に限る。
report_ui 形の report.json は Phase 4 の範囲（§8.1）であり、ここでは作らない。
"""
from __future__ import annotations

from typing import Any

from simulator.sim_ui.domain.simulation_job import JobStatus
from simulator.sim_ui.usecase.job_models import ResultNotAvailableError
from simulator.sim_ui.usecase.job_ports import JobLedgerPort
from simulator.sim_ui.usecase.query_job import QueryJobInteractor


class FetchJobResultInteractor:
    """完了したジョブに限り、結果ファイルの所在を返す。"""

    def __init__(self, *, ledger: JobLedgerPort, query: QueryJobInteractor) -> None:
        self._ledger = ledger
        self._query = query

    def execute(self, job_id: str, filename: str) -> Any:
        """所在を返す。完了していなければ :class:`ResultNotAvailableError`。"""
        job = self._query.resolve(job_id)
        if job.status is not JobStatus.COMPLETED:
            raise ResultNotAvailableError(
                f"ジョブ {job_id} は完了していないため結果を公開しません"
                f"（状態={job.status.value}）"
            )
        return self._ledger.result_path(job_id, filename)
