"""TDD: tools/run_weekly_vol_band_cli.py の合成器（詳細設計 §4.3 D1 配線）。

make_segment_runner: forecast を週セグメントへ注入し WeeklyVolBand+実 engine を 1 回
回す run_segment コールバックを返す（tools=Composition Root・pandas 許容）。
"""
from __future__ import annotations

from simulator.domain.bar import Bar
from simulator.domain.trading_week import week_id_of
from simulator.domain.variance_forecast import VarianceForecast
from simulator.tools.run_weekly_vol_band_cli import make_segment_runner

_BASE = 1_707_912_000


def _bar(t, o, h, l, c):
    return Bar(time=t, open=o, high=h, low=l, close=c, volume=1.0, spread=0)


def _fc():
    return VarianceForecast(week_id_of(_BASE), sigma_plus=0.05, sigma_minus=0.05,
                            sigma_total_prev=0.04, estimable=True)


class TestMakeSegmentRunner:
    def test_runner_returns_outcome_with_exit_type(self):
        bars = [
            _bar(_BASE, 100.0, 100.5, 99.5, 100.0),
            _bar(_BASE + 300, 100.0, 104.0, 99.8, 103.5),  # T 突破 → tp
            _bar(_BASE + 600, 103.5, 104.0, 103.0, 103.8),
        ]
        runner = make_segment_runner(p_tp=0.50, capital=100_000.0, f_risk=0.01)
        outcome = runner(bars, week_id_of(_BASE), _fc())
        assert outcome.week_id == week_id_of(_BASE)
        assert outcome.log.entry_flag is True
        assert outcome.log.exit_type == "tp"

    def test_runner_timeout_maps_to_timeout(self):
        bars = [
            _bar(_BASE, 100.0, 100.5, 99.5, 100.0),
            _bar(_BASE + 300, 100.0, 101.0, 99.0, 100.5),
            _bar(_BASE + 600, 100.5, 101.0, 99.5, 100.0),
        ]
        runner = make_segment_runner(p_tp=0.50, capital=100_000.0, f_risk=0.01)
        outcome = runner(bars, week_id_of(_BASE), _fc())
        assert outcome.log.exit_type == "timeout"
