"""TDD 単体（回帰）: 決定論再現（同一入力 -> 同一 best・trials・除外内訳）（詳細設計 §6.4・
NFR-OD1）。grid 列挙順・tie 先勝ち・非有限除外の決定論を固定する。

user memory「bugfix-pair-with-regression-test」: 退行（grid 順非決定・tie 非決定・非有限
argmax 混入）を禁止する回帰として 2 回実行の同値を assert する。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from simulator.usecase.optimize import OptimizeRequest, optimize
from simulator.usecase.optimize_strategies import GridSearch, NetProfitObjective


@dataclass
class _Bar:
    time: int


@dataclass
class _Stats:
    profit: float = 0.0
    profit_factor: float = 0.0
    recovery_factor: float = 0.0
    expected_payoff: float = 0.0
    sharpe_ratio: float = 0.0
    trades: int = 0


def _factory(stats_map):
    def make(params):
        key = params["x"]

        def run_segment(bars, trading_start):
            return stats_map[key]

        return make_seg(stats_map, key)

    def make_seg(m, key):
        def run_segment(bars, trading_start):
            return m[key]

        return run_segment

    return make


def _digest(result) -> str:
    payload = {
        "best_params": dict(result.best_params),
        "best_is_score": result.best_is_score,
        "excluded_count": result.excluded_count,
        "total_candidates": result.total_candidates,
        "finite_candidates": result.finite_candidates,
        "trials": [
            {"params": dict(t.params), "is_score": t.is_score,
             "is_finite": t.is_finite, "failed": t.failed, "is_best": t.is_best}
            for t in result.trials
        ],
    }
    return json.dumps(payload, sort_keys=True)


def test_optimize_is_deterministic_byte_identical_across_two_runs():
    # Arrange: 同一入力で 2 回実行（grid 列挙順・tie・非有限除外を含む）
    stats = {
        1: _Stats(profit=100.0),
        2: _Stats(profit=float("inf")),  # 非有限除外
        3: _Stats(profit=300.0),
        4: _Stats(profit=300.0),  # tie（3 と同値・先出 3 が勝つ）
    }
    bars = [_Bar(time=i) for i in range(10)]
    req = OptimizeRequest(search_space={"x": [1, 2, 3, 4]}, split=5, is_trading_start=0)

    def run(_unused):
        return optimize(
            request=req, full_bars=bars,
            make_run_segment=_factory(stats),
            search_port=GridSearch(max_candidates=99),
            objective_port=NetProfitObjective(),
        )

    # Act
    r1 = run(None)
    r2 = run(None)

    # Assert: byte 同一（決定論）・tie は先出（x:3）
    assert _digest(r1) == _digest(r2)
    assert r1.best_params == {"x": 3}
