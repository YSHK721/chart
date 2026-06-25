"""TDD: domain/backtest_test_result.py（詳細設計 §3.5 / §9.3）。"""
from __future__ import annotations

from simulator.domain.backtest_test_result import BacktestTestResult, Verdict


class TestVerdict:
    def test_verdict_values(self):
        assert Verdict.ADOPT.value == "adopt"
        assert Verdict.REJECT_STRATEGY.value == "reject_strategy"
        assert Verdict.NOT_ADOPT.value == "not_adopt"
        assert Verdict.INSUFFICIENT_SAMPLE.value == "insufficient_sample"


class TestBacktestTestResult:
    def test_constructs_full(self):
        r = BacktestTestResult(
            verdict=Verdict.ADOPT,
            spa_p=0.01,
            selected_e="E0",
            selected_p_tp=0.50,
            best_f_k=0.002,
            kupiec_p=0.30,
            christoffersen_p=0.40,
            tp_calibration_diff=0.02,
            oos_mean_weekly_net_return=0.001,
            oos_weeks=300,
            oos_stop_hits=35,
        )
        assert r.verdict is Verdict.ADOPT
        assert r.selected_e == "E0"
        assert r.oos_stop_hits == 35

    def test_reject_minimal(self):
        r = BacktestTestResult(
            Verdict.REJECT_STRATEGY, 0.20, None, None, -0.001,
            None, None, None, None, 300, 0,
        )
        assert r.verdict is Verdict.REJECT_STRATEGY
        assert r.spa_p == 0.20
