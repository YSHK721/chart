"""JobSubmission の strategy ブロック（P6-E2）の単体検定（Phase 6 F-8）.

追加のみ: `strategy: Mapping|None = None`（既定 None＝OFF＝byte 等価）。
`strategy_enabled` と `strategy_indicator_names()`（E-5 受付検証で参照する指標名の集合）を提供する。
"""
from __future__ import annotations

from simulator.sim_ui.usecase.job_models import JobSubmission


def test_strategy_defaults_to_none_off():
    # Arrange / Act: 既定 None（既存の 2 引数構築が壊れないこと＝byte 等価）
    sub = JobSubmission(backtest={"ea_name": "TC24051901"})
    # Assert
    assert sub.strategy is None
    assert sub.strategy_enabled is False


def test_empty_strategy_is_off():
    sub = JobSubmission(backtest={"ea_name": "X"}, strategy={})
    assert sub.strategy_enabled is False


def test_non_empty_strategy_is_on():
    sub = JobSubmission(
        backtest={"ea_name": "X"},
        strategy={"entry_long": [{"indicator": "ema", "shift": 0, "op": ">", "rhs": 1.0}]},
    )
    assert sub.strategy_enabled is True


def test_strategy_indicator_names_collects_lhs():
    # Arrange: entry_long/entry_short の lhs indicator を集める
    sub = JobSubmission(
        backtest={"ea_name": "X"},
        strategy={
            "entry_long": [{"indicator": "ema", "shift": 0, "op": ">", "rhs": 1.0}],
            "entry_short": [{"indicator": "rsi", "shift": 0, "op": "<", "rhs": 30.0}],
        },
    )
    # Act / Assert
    assert sub.strategy_indicator_names() == frozenset({"ema", "rsi"})


def test_strategy_indicator_names_includes_rhs_ref():
    # Arrange: rhs が指標参照なら rhs.indicator も含める
    sub = JobSubmission(
        backtest={"ea_name": "X"},
        strategy={
            "entry_long": [
                {"indicator": "ema", "shift": 0, "op": ">", "rhs": {"indicator": "close", "shift": 1}}
            ]
        },
    )
    # Act / Assert
    assert sub.strategy_indicator_names() == frozenset({"ema", "close"})


def test_strategy_indicator_names_ignores_constant_rhs():
    sub = JobSubmission(
        backtest={"ea_name": "X"},
        strategy={"entry_long": [{"indicator": "ema", "shift": 0, "op": ">", "rhs": 5.0}]},
    )
    assert sub.strategy_indicator_names() == frozenset({"ema"})


def test_strategy_indicator_names_empty_when_off():
    sub = JobSubmission(backtest={"ea_name": "X"})
    assert sub.strategy_indicator_names() == frozenset()
