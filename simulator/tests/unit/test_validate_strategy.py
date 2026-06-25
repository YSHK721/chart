"""TDD: usecase/validate_strategy.py 検証 S1〜S6（詳細設計 §4.4 / §9.3）。

S1 split floor・S4 REJECT/選択・S6 ADOPT/NOT_ADOPT 分岐・INSUFFICIENT_SAMPLE。
"""
from __future__ import annotations

from dataclasses import dataclass

from simulator.domain.backtest_test_result import Verdict
from simulator.domain.bar import Bar
from simulator.usecase.run_weekly_segments import WeeklyLogRecord, WeeklySegmentOutcome
from simulator.usecase.validate_strategy import (
    ValidateStrategyRequest,
    unique_week_ids,
    validate_strategy,
)
from simulator.domain.trading_week import week_id_of


def _bar(t, c=100.0):
    return Bar(time=t, open=c, high=c + 1, low=c - 1, close=c, volume=1.0, spread=0)


# 10 週ぶんの bars（各週 1 本）
_W0 = 1_707_912_000
_WEEK = 7 * 86_400
_BARS_10 = [_bar(_W0 + i * _WEEK) for i in range(10)]


def _outcome(wid, *, net_pnl=0.0, exit_type="none", entry=False):
    log = WeeklyLogRecord(
        week_id=wid, O=100.0, sigma_plus=0.02, sigma_minus=0.02,
        S=90.0, T=110.0, N=1.0, entry_flag=entry, exit_type=exit_type,
        holding_days=1.0, gross_pnl=net_pnl, cost=0.0, net_pnl=net_pnl,
        event_flag=False,
    )
    return WeeklySegmentOutcome(week_id=wid, log=log, stats=None)


class TestUniqueWeekIds:
    def test_returns_sorted_unique(self):
        ids = unique_week_ids(_BARS_10)
        assert ids == sorted(set(ids))
        assert len(ids) == 10


class TestSplitFloor:
    def test_split_floor_07_of_10_is_7(self):
        # W=10 → floor(7)=7。IS=[0,7) を 7 週検出し f_k を IS 週数で平均。
        seen = {"is_n": None}

        def _run_candidate(bars, e, p):
            ids = unique_week_ids(bars)
            seen["is_n"] = len(ids) if seen["is_n"] is None else seen["is_n"]
            return [_outcome(w) for w in ids]

        class _Spa:
            def spa_pvalue(self, fm, *, seed, B=5000): return 0.5  # ≥0.05 → REJECT

        class _Tests:
            def kupiec(self, h, alpha=0.05): return 1.0
            def christoffersen_independence(self, h): return 1.0

        req = ValidateStrategyRequest(full_bars=_BARS_10, capital=1e6, min_weeks=1, min_stop_hits=0)
        res = validate_strategy(request=req, run_candidate=_run_candidate, spa=_Spa(), tests=_Tests())
        assert seen["is_n"] == 7  # IS 週数 = floor(10*0.7)


class TestVerdicts:
    def _spa(self, p):
        class _S:
            def spa_pvalue(self, fm, *, seed, B=5000): return p
        return _S()

    def _tests(self, kp, cp):
        class _T:
            def kupiec(self, h, alpha=0.05): return kp
            def christoffersen_independence(self, h): return cp
        return _T()

    def _run_pos(self, *, oos_net, oos_exit):
        # IS: 各候補 正リターン → best>0。OOS: 指定の net/exit。
        def _rc(bars, e, p):
            ids = unique_week_ids(bars)
            if len(ids) >= 7:  # IS
                return [_outcome(w, net_pnl=10.0, exit_type="tp", entry=True) for w in ids]
            return [_outcome(w, net_pnl=oos_net, exit_type=oos_exit, entry=True) for w in ids]
        return _rc

    def test_spa_pge005_rejects(self):
        req = ValidateStrategyRequest(full_bars=_BARS_10, capital=1e6, min_weeks=1, min_stop_hits=0)
        res = validate_strategy(request=req, run_candidate=self._run_pos(oos_net=1.0, oos_exit="stop"),
                                spa=self._spa(0.10), tests=self._tests(1.0, 1.0))
        assert res.verdict is Verdict.REJECT_STRATEGY
        assert res.best_f_k is not None

    def test_best_le_zero_rejects(self):
        def _rc(bars, e, p):
            ids = unique_week_ids(bars)
            return [_outcome(w, net_pnl=-10.0, exit_type="stop", entry=True) for w in ids]
        req = ValidateStrategyRequest(full_bars=_BARS_10, capital=1e6, min_weeks=1, min_stop_hits=0)
        res = validate_strategy(request=req, run_candidate=_rc, spa=self._spa(0.01), tests=self._tests(1.0, 1.0))
        assert res.verdict is Verdict.REJECT_STRATEGY

    def test_all_conditions_met_adopts(self):
        req = ValidateStrategyRequest(full_bars=_BARS_10, capital=1e6, min_weeks=1, min_stop_hits=0)
        res = validate_strategy(request=req, run_candidate=self._run_pos(oos_net=5.0, oos_exit="stop"),
                                spa=self._spa(0.01), tests=self._tests(0.30, 0.40))
        assert res.verdict is Verdict.ADOPT

    def test_kupiec_fail_not_adopt(self):
        req = ValidateStrategyRequest(full_bars=_BARS_10, capital=1e6, min_weeks=1, min_stop_hits=0)
        res = validate_strategy(request=req, run_candidate=self._run_pos(oos_net=5.0, oos_exit="stop"),
                                spa=self._spa(0.01), tests=self._tests(0.01, 0.40))
        assert res.verdict is Verdict.NOT_ADOPT

    def test_oos_mean_le_zero_not_adopt(self):
        req = ValidateStrategyRequest(full_bars=_BARS_10, capital=1e6, min_weeks=1, min_stop_hits=0)
        res = validate_strategy(request=req, run_candidate=self._run_pos(oos_net=-5.0, oos_exit="stop"),
                                spa=self._spa(0.01), tests=self._tests(0.30, 0.40))
        assert res.verdict is Verdict.NOT_ADOPT


class TestInsufficientSample:
    def test_W_below_min_weeks_insufficient(self):
        def _rc(bars, e, p):
            ids = unique_week_ids(bars)
            return [_outcome(w, net_pnl=10.0, exit_type="tp", entry=True) for w in ids]

        class _Spa:
            def spa_pvalue(self, fm, *, seed, B=5000): return 0.01
        class _Tests:
            def kupiec(self, h, alpha=0.05): return 1.0
            def christoffersen_independence(self, h): return 1.0

        req = ValidateStrategyRequest(full_bars=_BARS_10, capital=1e6, min_weeks=260, min_stop_hits=30)
        res = validate_strategy(request=req, run_candidate=_rc, spa=_Spa(), tests=_Tests())
        assert res.verdict is Verdict.INSUFFICIENT_SAMPLE
