"""run_job の建玉変更（Phase 7 FR-07/08）注入の結合検定。

固定する不変条件:
    1. spec.strategy.trailing/partial_close present → build_interactor(position_manager=<PM>)。
    2. trailing/partial_close 不在 → position_manager を渡さない（引数の不在で byte 等価）。
    3. 継ぎ目（_build_position_manager）は実物で PositionManager を組み立てる。
    4. 建玉変更 spec の不正（未知キー/範囲外）は失敗の終了コード＋理由の永続化になる。

方式: `run_backtest` を差し替えて渡された引数を観測する（重い実データを回さない）。
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


def _backtest() -> dict:
    return {
        "ea_name": "TC24051901", "symbol": "EURUSD", "period": "M1",
        "data_path": "/tmp/x.csv", "initial_deposit": 100_000.0, "contract_size": 1.0,
        "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01, "stops_level": 0,
        "digits": 5, "point_size": 0.0001, "leverage": 100.0, "ma_period": 2,
        "ma_method": "sma", "lot_size": 1.0, "stop_loss_points": 500,
        "take_profit_points": 3000,
    }


_ENTRY = {"entry_long": [{"indicator": "close", "shift": 1, "op": ">", "rhs": 1.0}]}
_TRAILING = {"granularity": "tick", "trigger_points": 50, "distance_points": 30, "step_points": 10}
_PARTIAL = {"trigger": {"profit_points": 50}, "close_fraction": 0.5}


class _Spy:
    def __init__(self, exit_code: int = 0) -> None:
        self.kwargs = None
        self.exit_code = exit_code

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return self.exit_code, None


def _write_spec(job_dir: Path, strategy) -> None:
    (job_dir / "spec.json").write_text(
        json.dumps({"backtest": _backtest(), "sizing": None, "strategy": strategy},
                   ensure_ascii=False),
        encoding="utf-8",
    )


def test_trailing_present_passes_position_manager(job_dir, monkeypatch) -> None:
    from simulator.adapter.position_manager.position_manager import PositionManager

    _write_spec(job_dir, {**_ENTRY, "trailing": _TRAILING})
    spy = _Spy()
    monkeypatch.setattr(run_job, "run_backtest", spy)
    monkeypatch.setattr(run_job, "_build_strategy_override", lambda spec: object())
    run_job.main(["--job-dir", str(job_dir)])
    assert isinstance(spy.kwargs["position_manager"], PositionManager)


def test_partial_present_passes_position_manager(job_dir, monkeypatch) -> None:
    from simulator.adapter.position_manager.position_manager import PositionManager

    _write_spec(job_dir, {**_ENTRY, "partial_close": _PARTIAL})
    spy = _Spy()
    monkeypatch.setattr(run_job, "run_backtest", spy)
    monkeypatch.setattr(run_job, "_build_strategy_override", lambda spec: object())
    run_job.main(["--job-dir", str(job_dir)])
    assert isinstance(spy.kwargs["position_manager"], PositionManager)


def test_no_position_change_does_not_pass_manager(job_dir, monkeypatch) -> None:
    _write_spec(job_dir, _ENTRY)  # entry のみ・trailing/partial なし
    spy = _Spy()
    monkeypatch.setattr(run_job, "run_backtest", spy)
    monkeypatch.setattr(run_job, "_build_strategy_override", lambda spec: object())
    run_job.main(["--job-dir", str(job_dir)])
    assert "position_manager" not in spy.kwargs


def test_build_position_manager_builds_real_object() -> None:
    from simulator.adapter.position_manager.position_manager import PositionManager

    spec = {"backtest": _backtest(), "strategy": {**_ENTRY, "trailing": _TRAILING,
                                                  "partial_close": _PARTIAL}}
    pm = run_job._build_position_manager(spec)
    assert isinstance(pm, PositionManager)
    assert pm._trailing_granularity == "tick"
    assert pm._trailing is not None and pm._partial is not None
    assert pm._volume_step == 0.01


def test_bad_trailing_spec_fails_with_reason(job_dir, monkeypatch) -> None:
    _write_spec(job_dir, {**_ENTRY, "trailing": {"trigger_points": 1, "bogus": 9}})
    spy = _Spy()
    monkeypatch.setattr(run_job, "run_backtest", spy)
    monkeypatch.setattr(run_job, "_build_strategy_override", lambda spec: object())
    code = run_job.main(["--job-dir", str(job_dir)])
    assert code != 0
    failure = json.loads((job_dir / "failure.json").read_text(encoding="utf-8"))
    assert "建玉変更" in failure["reason"]
