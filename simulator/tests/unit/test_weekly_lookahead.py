"""TDD: usecase look-ahead 依存検証（詳細設計 §6 / §9.6・NFR-D4）。

assert_no_lookahead: σ̂/S/T/N の入力 week_id が全て target 週 w 未満であること。
混入注入（当週 RS を右辺へ）で LookaheadViolationError。
"""
from __future__ import annotations

import pytest

from simulator.domain.bar import Bar
from simulator.usecase import estimate_weekly_band as ewb
from simulator.usecase.estimate_weekly_band import (
    EstimateWeeklyBandRequest,
    estimate_weekly_band,
)
from simulator.usecase.run_weekly_segments import (
    LookaheadViolationError,
    assert_no_lookahead,
)


class TestAssertNoLookahead:
    def test_all_inputs_strictly_before_target_ok(self):
        # target "2024-W07" に対し入力は W05/W06（全て手前）
        assert_no_lookahead("2024-W07", ["2024-W05", "2024-W06"])

    def test_empty_inputs_ok(self):
        assert_no_lookahead("2024-W07", [])

    def test_current_week_input_raises(self):
        # 当週 RS を右辺に混入 → 違反
        with pytest.raises(LookaheadViolationError):
            assert_no_lookahead("2024-W07", ["2024-W06", "2024-W07"])

    def test_future_week_input_raises(self):
        with pytest.raises(LookaheadViolationError):
            assert_no_lookahead("2024-W07", ["2024-W08"])


# 2024-02-14 (Wed) 内の epoch（UTC）。週またぎは +7 日。
_BASE = 1_707_912_000


def _bar(t, o, h, l, c):
    return Bar(time=t, open=o, high=h, low=l, close=c, volume=1.0, spread=0)


def _build_request(n_weeks: int, window: int) -> EstimateWeeklyBandRequest:
    five: list[Bar] = []
    daily: list[Bar] = []
    for wk in range(n_weeks):
        t = _BASE + wk * 7 * 86_400
        five += [_bar(t, 100, 101, 99, 100), _bar(t + 300, 100, 102, 99, 101)]
        daily += [_bar(t, 100, 110, 95, 105)]
    return EstimateWeeklyBandRequest(five_min_bars=five, daily_bars=daily, window=window)


class _StubEstimator:
    def forecast(self, p, m, *, window=260, nw_lag=4):
        return (0.02, 0.02)


class _StubRepo:
    def save(self, f): ...
    def save_all(self, fs): ...
    def get(self, w): return None
    def all_week_ids(self): return ()


class TestEstimateWeeklyBandLookaheadWiring:
    """assert_no_lookahead が本番パス（estimate_weekly_band ループ内）で呼ばれる回帰テスト。

    memory: bugfix-pair-with-regression-test。設計 §9.6「混入注入」: 故意に当週 RS を
    HAR 右辺の history に滑り込ませた場合、本番パス経由で LookaheadViolationError。
    """

    def test_clean_history_does_not_raise(self):
        # 正常: history week_ids は全て当週より手前 → 違反なし（estimable で完走）。
        req = _build_request(n_weeks=4, window=2)
        res = estimate_weekly_band(
            request=req, estimator=_StubEstimator(), repo=_StubRepo()
        )
        assert len(res.forecasts) == 4

    def test_injected_current_week_raises_via_production_path(self, monkeypatch):
        # 混入注入: history week_ids 抽出に当週 wid を滑り込ませる → 本番 assert が捕捉。
        orig = ewb._history_week_ids

        def _tampered(week_ids, i):
            base = list(orig(week_ids, i))
            return base + [week_ids[i]]  # 当週 RS を右辺へ混入（look-ahead 違反）

        monkeypatch.setattr(ewb, "_history_week_ids", _tampered)
        req = _build_request(n_weeks=4, window=2)
        with pytest.raises(LookaheadViolationError):
            estimate_weekly_band(
                request=req, estimator=_StubEstimator(), repo=_StubRepo()
            )
