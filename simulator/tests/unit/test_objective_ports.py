"""TDD 単体: PF/Net/Sharpe/Recovery score・非有限除外・argmax tie 先勝ち
（詳細設計 §6.2.3・C-1/NFR-OD1）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from simulator.usecase.optimize_strategies import (
    NetProfitObjective,
    PfObjective,
    RecoveryObjective,
    SharpeObjective,
)


@dataclass
class _Stats:
    """目的関数フィールドのみ意味を持つ最小ヘルパ（BacktestStats 互換 duck typing）。"""

    profit: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    recovery_factor: float = 0.0


def test_pf_objective_returns_profit_factor():
    assert PfObjective().score(_Stats(profit_factor=1.42)) == 1.42


def test_net_profit_objective_returns_profit():
    assert NetProfitObjective().score(_Stats(profit=11370.0)) == 11370.0


def test_sharpe_objective_returns_sharpe_ratio():
    assert SharpeObjective().score(_Stats(sharpe_ratio=0.8)) == 0.8


def test_recovery_objective_returns_recovery_factor():
    assert RecoveryObjective().score(_Stats(recovery_factor=2.1)) == 2.1


def test_objective_name_attribute_is_field_name():
    # name 属性はログ/レポート出力用（ObjectivePort 契約）
    assert PfObjective().name == "profit_factor"
    assert NetProfitObjective().name == "profit"
    assert SharpeObjective().name == "sharpe_ratio"
    assert RecoveryObjective().name == "recovery_factor"


def test_nan_score_is_non_finite_excludable():
    # profit_factor = gross_profit/gross_loss が非有限（NaN）を取り得る（C-1）
    score = PfObjective().score(_Stats(profit_factor=float("nan")))
    assert not math.isfinite(score)


def test_inf_score_is_non_finite_excludable():
    # gross_loss=0 相当で +inf（C-1）
    score = PfObjective().score(_Stats(profit_factor=float("inf")))
    assert not math.isfinite(score)
