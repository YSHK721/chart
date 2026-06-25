"""TDD: usecase/estimate_weekly_band.py 純関数と UC（詳細設計 §4.2 / §9.3）。

aggregate_weekly_rs（同一立会日隣接5分のみ・符号別二乗和）・aggregate_weekly_gk
（GK 週次・床0）・estimate_weekly_band（窓・算出不可→no_trade）。
"""
from __future__ import annotations

import math

from simulator.domain.bar import Bar
from simulator.domain.variance_forecast import VarianceForecast
from simulator.usecase.estimate_weekly_band import (
    EstimateWeeklyBandRequest,
    aggregate_weekly_gk,
    aggregate_weekly_rs,
    estimate_weekly_band,
)
from simulator.domain.trading_week import week_id_of


def _bar(t, o, h, l, c):
    return Bar(time=t, open=o, high=h, low=l, close=c, volume=1.0, spread=0)


# 2024-02-14 (Wed) 内の5分足 epoch（UTC）
_BASE = 1_707_912_000  # 2024-02-14T12:00:00Z


class TestAggregateWeeklyRs:
    def test_adjacent_5min_same_day_split_by_sign(self):
        # 3 本：r1>0（up）, r2<0（down）。隣接5分=300秒。
        bars = [
            _bar(_BASE, 100.0, 101.0, 99.0, 100.0),
            _bar(_BASE + 300, 100.0, 102.0, 99.0, 101.0),  # r=log(101/100)>0
            _bar(_BASE + 600, 101.0, 101.5, 99.0, 100.0),  # r=log(100/101)<0
        ]
        wid = week_id_of(_BASE)
        rs = aggregate_weekly_rs(bars)
        rp, rm = rs[wid]
        assert rp == math.log(101.0 / 100.0) ** 2
        assert rm == math.log(100.0 / 101.0) ** 2

    def test_overnight_gap_excluded(self):
        # 隣接でない（翌日跨ぎ）→寄与しない。
        next_day = _BASE + 86_400
        bars = [
            _bar(_BASE, 100.0, 101.0, 99.0, 100.0),
            _bar(next_day, 100.0, 102.0, 99.0, 101.5),  # 別日跨ぎ除外
        ]
        rs = aggregate_weekly_rs(bars)
        # 同一立会日内の隣接ペアが 0 件 → どの週にも RS なし
        assert all(rp == 0.0 and rm == 0.0 for rp, rm in rs.values()) or rs == {}

    def test_non_adjacent_gap_excluded(self):
        # 同一日だが 600 秒間隔（欠落跨ぎ）→寄与しない。
        bars = [
            _bar(_BASE, 100.0, 101.0, 99.0, 100.0),
            _bar(_BASE + 600, 100.0, 102.0, 99.0, 101.5),
        ]
        rs = aggregate_weekly_rs(bars)
        assert rs == {} or all(rp == 0.0 and rm == 0.0 for rp, rm in rs.values())

    def test_zero_return_no_contribution(self):
        bars = [
            _bar(_BASE, 100.0, 101.0, 99.0, 100.0),
            _bar(_BASE + 300, 100.0, 101.0, 99.0, 100.0),  # r=0
        ]
        wid = week_id_of(_BASE)
        rs = aggregate_weekly_rs(bars)
        if wid in rs:
            assert rs[wid] == (0.0, 0.0)


class TestAggregateWeeklyGk:
    def test_gk_known_value(self):
        # GK_d = 0.5*(ln(H/L))² − (2ln2−1)*(ln(C/O))²
        d = _bar(_BASE, 100.0, 110.0, 95.0, 105.0)
        gk = 0.5 * math.log(110.0 / 95.0) ** 2 - (2 * math.log(2) - 1) * math.log(105.0 / 100.0) ** 2
        expected = math.sqrt(max(gk, 0.0))
        wid = week_id_of(_BASE)
        out = aggregate_weekly_gk([d])
        assert math.isclose(out[wid], expected, rel_tol=1e-12)

    def test_negative_variance_floored_to_zero(self):
        # C/O 大・H/L 小 → gk_d 負 → 床 0 → sqrt(0)=0
        d = _bar(_BASE, 100.0, 100.5, 99.5, 100.5)  # large C/O move, tiny range
        wid = week_id_of(_BASE)
        out = aggregate_weekly_gk([d])
        assert out[wid] >= 0.0


class TestEstimateWeeklyBandWarmup:
    def test_window_not_reached_yields_no_trade(self):
        # window=5 で 3 週ぶんしか RS がない → 全週 estimable=False
        class _StubEstimator:
            def forecast(self, p, m, *, window=260, nw_lag=4):
                return (0.02, 0.02)

        class _StubRepo:
            def __init__(self):
                self.saved = None
            def save(self, f): ...
            def save_all(self, fs): self.saved = list(fs)
            def get(self, w): return None
            def all_week_ids(self): return ()

        # 3 週ぶんの 5分・日足を最小構成で。各週 1 隣接ペア。
        five = []
        daily = []
        for wk in range(3):
            t = _BASE + wk * 7 * 86_400
            five += [_bar(t, 100, 101, 99, 100), _bar(t + 300, 100, 102, 99, 101)]
            daily += [_bar(t, 100, 110, 95, 105)]
        repo = _StubRepo()
        req = EstimateWeeklyBandRequest(five_min_bars=five, daily_bars=daily, window=5)
        res = estimate_weekly_band(request=req, estimator=_StubEstimator(), repo=repo)
        assert all(not f.estimable for f in res.forecasts)
        assert repo.saved is not None

    def test_estimator_none_yields_no_trade(self):
        class _NoneEstimator:
            def forecast(self, p, m, *, window=260, nw_lag=4):
                return (None, None)

        class _Repo:
            def save(self, f): ...
            def save_all(self, fs): ...
            def get(self, w): return None
            def all_week_ids(self): return ()

        five = []
        daily = []
        for wk in range(2):
            t = _BASE + wk * 7 * 86_400
            five += [_bar(t, 100, 101, 99, 100), _bar(t + 300, 100, 102, 99, 101)]
            daily += [_bar(t, 100, 110, 95, 105)]
        req = EstimateWeeklyBandRequest(five_min_bars=five, daily_bars=daily, window=0)
        res = estimate_weekly_band(request=req, estimator=_NoneEstimator(), repo=_Repo())
        assert all(not f.estimable for f in res.forecasts)
