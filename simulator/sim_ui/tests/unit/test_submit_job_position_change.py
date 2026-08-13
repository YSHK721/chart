"""SubmitJobInteractor の建玉変更（Phase 7 FR-07/08）受付検証の単体検定。

裁定: strategy.trailing / partial_close サブブロックを受理する（未指定=OFF=byte 等価）。
構造が壊れている（マッピングでない）場合は受付時に明示拒否する。範囲・列挙の意味検証は
run_job の framework loader が fail-stop で担う（受付は構造のみ・二重化しない）。
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

_CATALOG = FakeSeriesCatalog({"TC24051901": frozenset({"madiff", "close"})})


def _interactor(launcher=None):
    return SubmitJobInteractor(
        ledger=FakeLedger(),
        launcher=launcher or FakeLauncher(),
        series_catalog=_CATALOG,
        required_series=required_series,
        stop_loss_catalog=FakeStopLossCatalog(),
        allowed_backtest_keys=allowed_backtest_keys,
        required_backtest_keys=no_required_backtest_keys,
    )


_ENTRY = [{"indicator": "close", "shift": 1, "op": ">", "rhs": 1.0}]


def _sub(strategy):
    return JobSubmission(backtest={"ea_name": "TC24051901"}, strategy=strategy)


def test_trailing_mapping_is_accepted():
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    sub = _sub({"entry_long": _ENTRY,
                "trailing": {"trigger_points": 50, "distance_points": 30}})
    got = sut.execute(sub)
    assert got.status == JobStatus.RUNNING.value
    assert len(launcher.launched) == 1


def test_partial_close_mapping_is_accepted():
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    sub = _sub({"entry_long": _ENTRY,
                "partial_close": {"trigger": {"profit_points": 50}, "close_fraction": 0.5}})
    got = sut.execute(sub)
    assert got.status == JobStatus.RUNNING.value


def test_non_mapping_trailing_is_rejected():
    sut = _interactor()
    sub = _sub({"entry_long": _ENTRY, "trailing": [1, 2, 3]})
    with pytest.raises(JobSubmissionInvalidError):
        sut.execute(sub)


def test_non_mapping_partial_close_is_rejected():
    sut = _interactor()
    sub = _sub({"entry_long": _ENTRY, "partial_close": "nope"})
    with pytest.raises(JobSubmissionInvalidError):
        sut.execute(sub)


# --- 粒度不一致 fail-stop（🟡・無言不作動の防止） ---------------------------

def _sub_with_tickmodel(strategy, tick_model=None):
    overrides = {} if tick_model is None else {"tick_model": tick_model}
    return JobSubmission(
        backtest={"ea_name": "TC24051901", "config_overrides": overrides},
        strategy=strategy,
    )


def test_real_ticks_run_with_bar_trailing_is_rejected():
    # real_ticks 実行（tick 粒度）＋ trailing granularity="bar" → 無言不作動 → 拒否。
    sut = _interactor()
    sub = _sub_with_tickmodel(
        {"entry_long": _ENTRY, "trailing": {"granularity": "bar",
                                            "trigger_points": 50, "distance_points": 30}},
        tick_model="real_ticks",
    )
    with pytest.raises(JobSubmissionInvalidError):
        sut.execute(sub)


def test_real_ticks_run_with_tick_trailing_is_accepted():
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    sub = _sub_with_tickmodel(
        {"entry_long": _ENTRY, "trailing": {"granularity": "tick",
                                            "trigger_points": 50, "distance_points": 30}},
        tick_model="real_ticks",
    )
    got = sut.execute(sub)
    assert got.status == JobStatus.RUNNING.value


def test_bar_run_with_tick_trailing_is_rejected():
    # 既定（every_tick＝bar 経路）＋ trailing granularity="tick" → 無言不作動 → 拒否。
    sut = _interactor()
    sub = _sub_with_tickmodel(
        {"entry_long": _ENTRY, "trailing": {"granularity": "tick",
                                            "trigger_points": 50, "distance_points": 30}},
    )
    with pytest.raises(JobSubmissionInvalidError):
        sut.execute(sub)


def test_bar_run_with_bar_trailing_default_is_accepted():
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    # granularity 省略（既定 bar）＋ bar 実行 → 一致 → 受理。
    sub = _sub_with_tickmodel(
        {"entry_long": _ENTRY, "trailing": {"trigger_points": 50, "distance_points": 30}},
    )
    got = sut.execute(sub)
    assert got.status == JobStatus.RUNNING.value


def test_partial_close_has_no_granularity_gate():
    # partial_close は粒度非依存で常時作動＝ゲート対象外（real_ticks でも受理）。
    launcher = FakeLauncher()
    sut = _interactor(launcher=launcher)
    sub = _sub_with_tickmodel(
        {"entry_long": _ENTRY,
         "partial_close": {"trigger": {"profit_points": 50}, "close_fraction": 0.5}},
        tick_model="real_ticks",
    )
    got = sut.execute(sub)
    assert got.status == JobStatus.RUNNING.value
