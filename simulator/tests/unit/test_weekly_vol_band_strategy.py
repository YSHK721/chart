"""TDD: adapter/strategy/weekly_vol_band.py（詳細設計 §5.1）。

先頭バー(bar_index=0)でのみ market buy(N, sl=S, tp=T)。以降は []。
on_position_check は常に "hold"（D1：金曜引けは end_of_test 担当）。
"""
from __future__ import annotations

import pandas as pd

from simulator.adapter.strategy.weekly_vol_band import WeeklyVolBand
from simulator.domain.variance_forecast import VarianceForecast


def _config():
    return {"digits": 0, "volume_step": 0.01}


def _indicators(open_at_0=39000.0):
    return {"open": pd.Series([open_at_0, 39100.0, 39200.0])}


def _fc():
    return VarianceForecast("2024-W07", sigma_plus=0.025, sigma_minus=0.020,
                            sigma_total_prev=0.018, estimable=True)


class TestWeeklyVolBandEntry:
    def test_first_bar_emits_market_buy_with_sltp(self):
        strat = WeeklyVolBand(forecast=_fc(), p_tp=0.50, capital=1_000_000.0, f_risk=0.01)
        strat.on_init(_config(), _indicators())
        orders = strat.on_new_bar(0, _indicators(), account=None)
        assert len(orders) == 1
        o = orders[0]
        assert o.side == "buy" and o.kind == "market"
        assert o.sl is not None and o.tp is not None
        # S=O·exp(-1.96·0.02)=37501, T=O·exp(0.674·0.025)=39663（digits=0）
        assert round(o.sl) == 37501
        assert round(o.tp) == 39663

    def test_only_arms_once(self):
        strat = WeeklyVolBand(forecast=_fc(), p_tp=0.50, capital=1_000_000.0, f_risk=0.01)
        strat.on_init(_config(), _indicators())
        assert len(strat.on_new_bar(0, _indicators(), None)) == 1
        assert strat.on_new_bar(1, _indicators(), None) == []
        assert strat.on_new_bar(2, _indicators(), None) == []

    def test_non_first_bar_no_order(self):
        strat = WeeklyVolBand(forecast=_fc(), p_tp=0.50, capital=1_000_000.0, f_risk=0.01)
        strat.on_init(_config(), _indicators())
        assert strat.on_new_bar(3, _indicators(), None) == []


class TestWeeklyVolBandPositionCheck:
    def test_on_position_check_always_hold(self):
        strat = WeeklyVolBand(forecast=_fc(), p_tp=0.50, capital=1_000_000.0, f_risk=0.01)
        assert strat.on_position_check(position=None, bar_index=0, indicators=None) == "hold"
        assert strat.on_position_check(position=None, bar_index=99, indicators=None) == "hold"
