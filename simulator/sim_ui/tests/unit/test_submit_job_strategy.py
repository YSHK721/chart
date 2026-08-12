"""SubmitJobInteractor の戦略項目 受付検証（P6-E5・E-3 カタログ再利用）の単体検定.

裁定（指示書 P6-E5）: 戦略条件が参照する indicator ⊆ 当該 ea_name の登録系列 を
**受付時に検証**する。満たさなければ明示拒否（無音で誤った実行をさせない）。省略時
（strategy 不在）は検証を巻き込まず既存挙動 byte 等価。実行時 fail-stop
（GenericConditionStrategy の IndicatorBufferError）は最後の砦。
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

# TC24051901 は registry に {madiff, close} を持つ（実測）。
_CATALOG = FakeSeriesCatalog({"TC24051901": frozenset({"madiff", "close"})})


def _interactor(ledger=None, launcher=None, catalog=_CATALOG):
    return SubmitJobInteractor(
        ledger=ledger or FakeLedger(),
        launcher=launcher or FakeLauncher(),
        series_catalog=catalog,
        required_series=required_series,
        stop_loss_catalog=FakeStopLossCatalog(),
        allowed_backtest_keys=allowed_backtest_keys,
        required_backtest_keys=no_required_backtest_keys,
    )


def _sub(strategy):
    return JobSubmission(backtest={"ea_name": "TC24051901"}, strategy=strategy)


def test_strategy_referencing_available_series_is_accepted():
    # Arrange: close は TC の registry にある
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    sub = _sub({"entry_long": [{"indicator": "close", "shift": 1, "op": ">", "rhs": 1.0}]})
    # Act
    got = sut.execute(sub)
    # Assert
    assert got.status == JobStatus.RUNNING.value
    assert len(launcher.launched) == 1


def test_strategy_referencing_missing_series_is_rejected():
    # Arrange: "ema" は TC の registry に無い
    sut = _interactor()
    sub = _sub({"entry_long": [{"indicator": "ema", "shift": 0, "op": ">", "rhs": 1.0}]})
    # Act / Assert
    with pytest.raises(JobSubmissionInvalidError) as exc:
        sut.execute(sub)
    assert "ema" in str(exc.value)


def test_missing_series_in_rhs_ref_is_rejected():
    # Arrange: rhs 参照の指標 "sma" が無い
    sut = _interactor()
    sub = _sub(
        {
            "entry_long": [
                {"indicator": "close", "shift": 0, "op": ">", "rhs": {"indicator": "sma", "shift": 1}}
            ]
        }
    )
    # Act / Assert
    with pytest.raises(JobSubmissionInvalidError) as exc:
        sut.execute(sub)
    assert "sma" in str(exc.value)


def test_rejected_strategy_job_leaves_no_residue():
    # Arrange
    ledger, launcher = FakeLedger(), FakeLauncher()
    sut = _interactor(ledger=ledger, launcher=launcher)
    sub = _sub({"entry_short": [{"indicator": "nope", "shift": 0, "op": "<", "rhs": 1.0}]})
    # Act
    with pytest.raises(JobSubmissionInvalidError):
        sut.execute(sub)
    # Assert
    assert ledger.create_calls == 0
    assert launcher.launched == []


def test_strategy_off_skips_validation():
    # Arrange: strategy None（既定 OFF）は検証を巻き込まない（既存挙動 byte 等価）
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    got = sut.execute(JobSubmission(backtest={"ea_name": "TC24051901"}))
    # Assert
    assert got.status == JobStatus.RUNNING.value


def test_strategy_override_is_not_an_accepted_backtest_key():
    # 注入専用の引数（run_job が spec.strategy から組む）は JSON backtest から渡させない。
    # strategy_decorator と同じ扱い（_INJECTED_ONLY_KEYS）。
    from simulator.sim_ui.main.composition_root_jobs import allowed_backtest_keys

    allowed = allowed_backtest_keys()
    assert "strategy_override" not in allowed
    assert "strategy_decorator" not in allowed


def test_strategy_validated_even_when_sizing_off():
    # Arrange: sizing OFF でも strategy 参照検証は効く（独立した検証）
    sut = _interactor()
    sub = JobSubmission(
        backtest={"ea_name": "TC24051901"},
        sizing=None,
        strategy={"entry_long": [{"indicator": "ema", "shift": 0, "op": ">", "rhs": 1.0}]},
    )
    with pytest.raises(JobSubmissionInvalidError):
        sut.execute(sub)
