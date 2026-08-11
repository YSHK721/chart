"""ジョブ実行系の Port フェイク（検定用の差替実装）。

usecase の Interactor は Port にのみ依存する（DIP）。ここでは FS・子プロセスという
偶有的技術を持たないフェイクを与え、**usecase の規則だけ**を検定対象にする。

置き場所: 単体（`tests/unit`）と結合（`tests/integration`）の双方から使うため
`tests/integration` に置き、`simulator.sim_ui.tests.integration._fake_ports` として import する。
"""
from __future__ import annotations

from typing import Any

from simulator.sim_ui.domain.simulation_job import (
    JobStatus,
    JobTransitionError,
    SimulationJob,
)
from simulator.sim_ui.usecase.job_models import JobSubmission
from simulator.sim_ui.usecase.job_ports import (
    IndicatorSeriesCatalogPort,
    JobLauncherPort,
    JobLedgerPort,
    StopLossParamCatalogPort,
)


class FakeLedger(JobLedgerPort):
    """メモリ上のジョブ台帳。FS を持たない。"""

    def __init__(self, *, next_ids: "list[str] | None" = None) -> None:
        self.jobs: "dict[str, SimulationJob]" = {}
        self.submissions: "dict[str, JobSubmission]" = {}
        self._next_ids = list(next_ids or [])
        self._seq = 0
        self.create_calls = 0
        self.update_calls: "list[SimulationJob]" = []
        # 子プロセスが残す失敗理由（`read_failure_report` の返り値）。
        self.failure_reports: "dict[str, str]" = {}

    def create(self, submission: JobSubmission) -> SimulationJob:
        self.create_calls += 1
        if self._next_ids:
            job_id = self._next_ids.pop(0)
        else:
            self._seq += 1
            job_id = f"job-{self._seq}"
        job = SimulationJob.received(job_id)
        self.jobs[job_id] = job
        self.submissions[job_id] = submission
        return job

    def load(self, job_id: str) -> "SimulationJob | None":
        return self.jobs.get(job_id)

    def update(self, job: SimulationJob, *, expect: JobStatus) -> None:
        """本物と同じ compare-and-set 契約（期待と違えば書かずに例外）。"""
        current = self.jobs.get(job.job_id)
        if current is None:
            raise JobTransitionError(f"未登録のジョブは更新できません: {job.job_id}")
        if current.status is not expect:
            raise JobTransitionError(
                f"期待={expect.value} 実際={current.status.value}"
            )
        self.update_calls.append(job)
        self.jobs[job.job_id] = job

    def read_failure_report(self, job_id: str) -> "str | None":
        return self.failure_reports.get(job_id)

    def result_path(self, job_id: str, filename: str) -> Any:
        return f"/fake/{job_id}/{filename}"


class FakeLauncher(JobLauncherPort):
    """子プロセスを持たないフェイク。起動・終了・poll の呼ばれ方だけを記録する。"""

    def __init__(self, *, fail_on_launch: "str | None" = None) -> None:
        self.launched: "list[str]" = []
        self.terminated: "list[str]" = []
        self.return_codes: "dict[str, int | None]" = {}
        self._fail_on_launch = fail_on_launch

    def launch(self, job_id: str) -> None:
        if self._fail_on_launch is not None:
            raise OSError(self._fail_on_launch)
        self.launched.append(job_id)
        self.return_codes.setdefault(job_id, None)

    def terminate(self, job_id: str) -> None:
        self.terminated.append(job_id)

    def poll(self, job_id: str) -> "int | None":
        return self.return_codes.get(job_id)

    # --- 検定用のヘルパ（Port の一部ではない） ---
    def finish(self, job_id: str, return_code: int = 0) -> None:
        self.return_codes[job_id] = return_code


class FakeSeriesCatalog(IndicatorSeriesCatalogPort):
    """ea_name → 指標レジストリの登録系列名（E-3 判定用）。"""

    def __init__(self, table: "dict[str, frozenset[str]]") -> None:
        self._table = table

    def series_for(self, ea_name: str) -> "frozenset[str]":
        return self._table.get(ea_name, frozenset())


def submission(
    ea_name: str = "PRO_fit_Band_EA",
    *,
    sizing: "dict[str, Any] | None" = None,
    entry_price_basis: "str | None" = None,
) -> JobSubmission:
    """検定用の投入要求を組み立てる。"""
    backtest: "dict[str, Any]" = {"ea_name": ea_name, "symbol": "JP225", "period": "M5"}
    if entry_price_basis is not None:
        backtest["config_overrides"] = {"entry_price_basis": entry_price_basis}
    return JobSubmission(backtest=backtest, sizing=sizing)


def terminal(job_id: str, status: JobStatus) -> SimulationJob:
    """終端状態のジョブを作る（domain の遷移表を通す）。"""
    running = SimulationJob.received(job_id).to(JobStatus.RUNNING)
    if status is JobStatus.FAILED:
        return running.to(JobStatus.FAILED, failure_reason="setup")
    return running.to(status)


class FakeStopLossCatalog(StopLossParamCatalogPort):
    """ea_name → SL を決める設定パラメータ名の集合（§12.8 受付時検証の代役）。

    既定（空マッピング）は「**SL は設定パラメータでは決まらない**」＝受付時に判定しない。
    SL 検証を対象にしない検定はこの既定を使い、検証内容を従来どおりに保つ。
    """

    def __init__(self, mapping: "dict[str, frozenset[str]] | None" = None) -> None:
        self._mapping = mapping or {}

    def stop_loss_params(self, ea_name: str) -> "frozenset[str]":
        return self._mapping.get(ea_name, frozenset())


def required_series(entry_price_basis: str) -> str:
    """Group B の `simulator.usecase.sizing_ports.required_price_series` と同一契約。

    合成根が実物を注入する（DIP）。検定側は規則だけを固定する。
    """
    return "open" if entry_price_basis == "current_open" else "close"


def allowed_backtest_keys() -> "frozenset[str]":
    """`build_interactor` の受理キー集合（本番と同一ソースから導出）。

    検定側で手書きの集合を作ると、本番の導出規則が変わったときに追随せず
    「テストは緑だが本番は拒否する」を作る。合成根の実装をそのまま使う。
    """
    from simulator.sim_ui.main.composition_root_jobs import (
        allowed_backtest_keys as _real,
    )

    return _real()


def required_backtest_keys() -> "frozenset[str]":
    """必須キー集合（本番と同一ソース）。検定側で手書きしない。

    ただし多くの検定は最小の `backtest` で投入するため、必須検査を対象にしない
    検定は :func:`no_required_backtest_keys` を使う。
    """
    from simulator.sim_ui.main.composition_root_jobs import (
        required_backtest_keys as _real,
    )

    return _real()


def no_required_backtest_keys() -> "frozenset[str]":
    """必須キーを課さない（必須検査を対象にしない検定用）。"""
    return frozenset()
