"""TDD: usecase/run_weekly_segments.py（詳細設計 §4.3 / §9.3）。

split_into_weeks・slice_week_bars・エントリ規則 E0/E1(θ)・ノートレード分岐。
"""
from __future__ import annotations

import math

from simulator.domain.bar import Bar
from simulator.domain.trading_week import week_id_of
from simulator.domain.variance_forecast import VarianceForecast
from simulator.usecase.run_weekly_segments import (
    RunWeeklySegmentsRequest,
    entry_rule_true,
    run_weekly_segments,
    slice_week_bars,
    split_into_weeks,
)


def _bar(t, c=100.0):
    return Bar(time=t, open=c, high=c + 1, low=c - 1, close=c, volume=1.0, spread=0)


_W1 = 1_707_912_000  # 2024-02-14 (W07)
_W2 = _W1 + 7 * 86_400  # +1 week (W08)


class TestSplitIntoWeeks:
    def test_splits_two_weeks(self):
        bars = [_bar(_W1), _bar(_W1 + 300), _bar(_W2), _bar(_W2 + 300)]
        weeks = split_into_weeks(bars)
        assert len(weeks) == 2
        assert weeks[0].week_id == week_id_of(_W1)
        assert weeks[1].week_id == week_id_of(_W2)

    def test_first_and_last_trading_time(self):
        bars = [_bar(_W1), _bar(_W1 + 300), _bar(_W1 + 600)]
        weeks = split_into_weeks(bars)
        assert weeks[0].first_trading_time == _W1
        assert weeks[0].last_trading_time == _W1 + 600


class TestSliceWeekBars:
    def test_slices_only_target_week(self):
        bars = [_bar(_W1), _bar(_W1 + 300), _bar(_W2)]
        weeks = split_into_weeks(bars)
        wk1_bars = slice_week_bars(bars, weeks[0])
        assert len(wk1_bars) == 2
        assert all(week_id_of(b.time) == weeks[0].week_id for b in wk1_bars)


class TestEntryRule:
    def test_e0_always_true(self):
        weeks = split_into_weeks([_bar(_W1)])
        fc = VarianceForecast(weeks[0].week_id, 0.02, 0.02, 0.03, estimable=True)
        assert entry_rule_true("E0", prev_week_close=None, week=weeks[0], forecast=fc) is True

    def test_e1_no_prev_close_false(self):
        weeks = split_into_weeks([_bar(_W1)])
        fc = VarianceForecast(weeks[0].week_id, 0.02, 0.02, 0.03, estimable=True)
        assert entry_rule_true("E1(1.0)", prev_week_close=None, week=weeks[0], forecast=fc) is False

    def test_e1_threshold_boundary_true(self):
        # 前週 c2c リターン == −θ·σ̂ᵗᵒᵗᵃˡ で境界（<= で真）
        weeks = split_into_weeks([_bar(_W1)])
        sigma_total = 0.03
        theta = 1.0
        # r_prev = log(prev_close/prev2_close) を境界に置く。run_weekly_segments は
        # prev_week_close と forecast.sigma_total_prev から内部で r を作るが、
        # 純関数 entry_rule_true は (prev_week_close=(prev2,prev)) タプルを受ける契約とする。
        prev2 = 100.0
        prev = prev2 * math.exp(-theta * sigma_total)  # r_prev == -θσ
        fc = VarianceForecast(weeks[0].week_id, 0.02, 0.02, sigma_total, estimable=True)
        assert entry_rule_true("E1(1.0)", prev_week_close=(prev2, prev), week=weeks[0], forecast=fc) is True

    def test_e1_above_threshold_false(self):
        weeks = split_into_weeks([_bar(_W1)])
        sigma_total = 0.03
        prev2 = 100.0
        prev = 100.5  # r_prev > 0 > -θσ → false
        fc = VarianceForecast(weeks[0].week_id, 0.02, 0.02, sigma_total, estimable=True)
        assert entry_rule_true("E1(1.0)", prev_week_close=(prev2, prev), week=weeks[0], forecast=fc) is False


class TestRunWeeklySegmentsNoTrade:
    def test_not_estimable_week_skips_segment(self):
        bars = [_bar(_W1), _bar(_W1 + 300)]

        class _Repo:
            def save(self, f): ...
            def save_all(self, fs): ...
            def get(self, w):
                return VarianceForecast.no_trade(w)  # estimable=False
            def all_week_ids(self): return ()

        called = {"n": 0}

        def _runner(week_bars, wid, fc):
            called["n"] += 1
            return None

        req = RunWeeklySegmentsRequest(full_bars=bars, e_rule="E0", p_tp=0.50, capital=1e6)
        outs = run_weekly_segments(request=req, repo=_Repo(), run_segment=_runner)
        assert called["n"] == 0  # ノートレード週は run_segment を呼ばない
        assert len(outs) == 1
        assert outs[0].log.entry_flag is False
