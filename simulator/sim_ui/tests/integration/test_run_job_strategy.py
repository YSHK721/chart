"""run_job の戦略項目 override 構築（P6-E4）の結合検定（Phase 6 F-8）.

固定する不変条件:
    1. spec.strategy present → `build_interactor(strategy_override=<GenericConditionStrategy>)`。
    2. spec.strategy 不在 → `strategy_override` を渡さない（引数の不在で byte 等価を保証）。
    3. strategy × sizing 併用 → strategy_override と strategy_decorator の両方を渡す（合成の両立）。
    4. 継ぎ目（`_build_strategy_override`）は実物で GenericConditionStrategy を組み立てられる。
    5. 戦略項目の構築失敗（未知 op 等）は失敗の終了コード＋理由の永続化になる。

方式: `run_backtest` を差し替えて**渡された引数**を観測する（重い実データを回さない）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from simulator.sim_ui.main import run_job


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    d = tmp_path / "0123456789abcdef0123456789abcdef"
    d.mkdir()
    return d


def _full_backtest_spec() -> dict:
    return {
        "ea_name": "TC24051901",
        "symbol": "EURUSD",
        "period": "M1",
        "data_path": "/tmp/x.csv",
        "initial_deposit": 100_000.0,
        "contract_size": 1.0,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "stops_level": 0,
        "digits": 5,
        "point_size": 0.0001,
        "leverage": 100.0,
        "ma_period": 2,
        "ma_method": "sma",
        "lot_size": 1.0,
        "stop_loss_points": 500,
        "take_profit_points": 3000,
    }


_STRATEGY = {
    "entry_long": [{"indicator": "close", "shift": 1, "op": ">", "rhs": 1.0}],
    "entry_short": [{"indicator": "madiff", "shift": 0, "op": "<", "rhs": 0.0}],
}


class _Spy:
    def __init__(self, exit_code: int = 0) -> None:
        self.kwargs = None
        self.exit_code = exit_code

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return self.exit_code, None


def _write_spec(job_dir: Path, *, strategy=None, sizing=None) -> None:
    (job_dir / "spec.json").write_text(
        json.dumps(
            {"backtest": _full_backtest_spec(), "sizing": sizing, "strategy": strategy},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


# --- 1. override の受け渡し ------------------------------------------------

def test_strategy_present_passes_strategy_override(job_dir, monkeypatch) -> None:
    # Arrange
    _write_spec(job_dir, strategy=_STRATEGY)
    spy = _Spy()
    sentinel = object()
    monkeypatch.setattr(run_job, "run_backtest", spy)
    monkeypatch.setattr(run_job, "_build_strategy_override", lambda spec: sentinel)
    # Act
    run_job.main(["--job-dir", str(job_dir)])
    # Assert
    assert spy.kwargs["strategy_override"] is sentinel


@pytest.mark.parametrize("strategy", [None, {}])
def test_strategy_absent_does_not_pass_override(job_dir, monkeypatch, strategy) -> None:
    # Arrange: strategy 不在/空は override を渡さない（byte 等価）
    _write_spec(job_dir, strategy=strategy)
    spy = _Spy()
    monkeypatch.setattr(run_job, "run_backtest", spy)
    # Act
    run_job.main(["--job-dir", str(job_dir)])
    # Assert
    assert "strategy_override" not in spy.kwargs


def test_strategy_and_sizing_pass_both(job_dir, monkeypatch) -> None:
    # Arrange: 併用時は両方渡す（override × sizing の合成を保つ）
    _write_spec(job_dir, strategy=_STRATEGY, sizing={"enabled": True})
    spy = _Spy()
    monkeypatch.setattr(run_job, "run_backtest", spy)
    monkeypatch.setattr(run_job, "_build_decorator", lambda spec: object())
    monkeypatch.setattr(run_job, "_build_strategy_override", lambda spec: object())
    # Act
    run_job.main(["--job-dir", str(job_dir)])
    # Assert
    assert spy.kwargs["strategy_override"] is not None
    assert spy.kwargs["strategy_decorator"] is not None


# --- 2. 継ぎ目の実物検証（monkeypatch なし）--------------------------------

def test_build_strategy_override_builds_generic_from_spec() -> None:
    from simulator.adapter.strategy.generic_condition_strategy import (
        GenericConditionStrategy,
    )

    # Arrange
    spec = {"backtest": _full_backtest_spec(), "strategy": _STRATEGY}
    # Act
    strategy = run_job._build_strategy_override(spec)
    # Assert
    assert isinstance(strategy, GenericConditionStrategy)
    assert len(strategy._entry_long) == 1 and len(strategy._entry_short) == 1


def test_build_strategy_override_reflects_entry_price_basis() -> None:
    # Arrange: current_open → 建値系列は "open"
    backtest = _full_backtest_spec()
    backtest["config_overrides"] = {"entry_price_basis": "current_open"}
    spec = {"backtest": backtest, "strategy": _STRATEGY}
    # Act
    strategy = run_job._build_strategy_override(spec)
    # Assert
    assert strategy._price_series == "open"


# --- 3. 構築失敗の扱い -----------------------------------------------------

def test_strategy_build_failure_records_failure(job_dir) -> None:
    # Arrange: 未知 op は loader で ConfigError → 仕様エラー扱い＋理由永続化
    _write_spec(
        job_dir,
        strategy={"entry_long": [{"indicator": "close", "shift": 0, "op": "==", "rhs": 1.0}]},
    )
    # Act
    code = run_job.main(["--job-dir", str(job_dir)])
    # Assert
    assert code != 0
    report = json.loads((job_dir / "failure.json").read_text(encoding="utf-8"))
    assert "戦略" in report["reason"] or "strategy" in report["reason"].lower()
