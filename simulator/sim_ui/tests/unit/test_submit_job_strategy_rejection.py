"""段階 2（§19.5）: `strategy` ブロックを受付で明示拒否する不変条件の検定。

裁定: sim の受付面（`POST /sim/jobs`）は MT5 の Settings タブと同じ範囲だけを受け取る。
条件・トレーリング・部分決済は **MT5 Settings タブに対応物が無い**項目であり、EA 側
（Expert）の実装で指定するものだから、受付面では受け取らない。

固定する不変条件:
    1. `strategy` が **`None` でない**（`dict` でも空 `{}` でも）投入は受付で拒否する。
       空 `{}` を黙って OFF に倒さないのは「渡したのに無視された」を作らないため。
    2. `strategy` が `null` の投入、および `strategy` を持たない投入は従来どおり受理する。
       `null` は台帳の書式（`FileJobLedger` が不在を `"strategy": null` と書く）そのもの
       であり、保存済みジョブの再投入という正当な操作を落とさないため受理する。
    3. 拒否した投入は台帳に 1 件も書かず、子プロセスを 1 度も起こさない
       （実行到達 0＝`test_job_execution_reachability.py` の不変条件と対になる）。
    4. 拒否の文言は「なぜ受け取らないか」と「どこで指定するか」を述べる。

エンジン側の戦略資産は撤去していない（`run_job --job-dir` 直投入から到達可能）。
到達不能化するのは受付 API 経由の入口だけである。
"""
from __future__ import annotations

import pytest

from simulator.sim_ui.domain.simulation_job import JobStatus
from simulator.sim_ui.tests.integration._fake_ports import (
    FakeLauncher,
    FakeLedger,
    FakeSeriesCatalog,
    FakeStopLossCatalog,
    allowed_backtest_keys,
    no_required_backtest_keys,
    required_series,
)
from simulator.sim_ui.usecase.job_models import JobSubmission, JobSubmissionInvalidError
from simulator.sim_ui.usecase.submit_job import SubmitJobInteractor

_EA = "TC24051901"
_CATALOG = FakeSeriesCatalog({_EA: frozenset({"madiff", "close"})})
#: 参照指標が registry に**在る**条件（拒否が「指標が無い」からではないことを示す）。
_ENTRY = [{"indicator": "close", "shift": 1, "op": ">", "rhs": 1.0}]


def _interactor(ledger=None, launcher=None) -> SubmitJobInteractor:
    return SubmitJobInteractor(
        ledger=ledger or FakeLedger(),
        launcher=launcher or FakeLauncher(),
        series_catalog=_CATALOG,
        required_series=required_series,
        stop_loss_catalog=FakeStopLossCatalog(),
        allowed_backtest_keys=allowed_backtest_keys,
        required_backtest_keys=no_required_backtest_keys,
    )


# --- 1. strategy 非 None は拒否 ------------------------------------------------

def test_条件を持つstrategyブロックは受付で拒否される() -> None:
    # Arrange: 参照指標は registry に在る（従来なら受理されていた本文）
    sut = _interactor()
    sub = JobSubmission(backtest={"ea_name": _EA}, strategy={"entry_long": _ENTRY})
    # Act / Assert
    with pytest.raises(JobSubmissionInvalidError):
        sut.execute(sub)


def test_空のstrategyブロックも受付で拒否される() -> None:
    """`{}` は「渡した」意思表示である。黙って OFF に倒さない。"""
    # Arrange
    sut = _interactor()
    sub = JobSubmission(backtest={"ea_name": _EA}, strategy={})
    # Act / Assert
    with pytest.raises(JobSubmissionInvalidError):
        sut.execute(sub)


def test_建玉変更だけのstrategyブロックも受付で拒否される() -> None:
    # Arrange: trailing / partial_close も同じ扱い（条件の有無で分けない）
    sut = _interactor()
    sub = JobSubmission(
        backtest={"ea_name": _EA},
        strategy={"trailing": {"trigger_points": 50, "distance_points": 30}},
    )
    # Act / Assert
    with pytest.raises(JobSubmissionInvalidError):
        sut.execute(sub)


# --- 2. null / 不在は従来どおり受理 --------------------------------------------

def test_strategyがnullの投入は受理される() -> None:
    """台帳書式 `"strategy": null` の再投入を落とさない。"""
    # Arrange
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    # Act
    got = sut.execute(JobSubmission(backtest={"ea_name": _EA}, strategy=None))
    # Assert
    assert got.status == JobStatus.RUNNING.value
    assert len(launcher.launched) == 1


def test_strategyを持たない投入は受理される() -> None:
    # Arrange
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    # Act
    got = sut.execute(JobSubmission(backtest={"ea_name": _EA}))
    # Assert
    assert got.status == JobStatus.RUNNING.value
    assert len(launcher.launched) == 1


# --- 3. 拒否時の実行到達 0 ------------------------------------------------------

def test_拒否した投入は台帳へ書かず子も起こさない() -> None:
    # Arrange
    ledger, launcher = FakeLedger(), FakeLauncher()
    sut = _interactor(ledger=ledger, launcher=launcher)
    sub = JobSubmission(backtest={"ea_name": _EA}, strategy={"entry_long": _ENTRY})
    # Act
    with pytest.raises(JobSubmissionInvalidError):
        sut.execute(sub)
    # Assert
    assert ledger.create_calls == 0
    assert launcher.launched == []


# --- 4. 文言が理由と代替手段を述べる --------------------------------------------

def test_拒否の文言は理由と指定先を述べる() -> None:
    # Arrange
    sut = _interactor()
    sub = JobSubmission(backtest={"ea_name": _EA}, strategy={"entry_long": _ENTRY})
    # Act
    with pytest.raises(JobSubmissionInvalidError) as caught:
        sut.execute(sub)
    message = str(caught.value)
    # Assert: なぜ受け取らないか／どこで指定するか／エンジン資産の到達手段
    assert "MT5 Settings" in message, f"受け取らない理由が文言に無い: {message}"
    assert "Expert" in message, f"指定先（EA 側）が文言に無い: {message}"
    assert "run_job" in message, f"エンジン資産への到達手段が文言に無い: {message}"
