"""TDD 統合: run_weekly_segments × 実 engine（詳細設計 §9.7・D1/ISSUE-027）。

WeeklyVolBand 戦略を実 RunBacktestInteractor で週セグメント実行し:
    * S 到達週 → exit_reason="sl"（ストップ）
    * T 到達週 → exit_reason="tp"（利確）
    * 未到達週 → exit_reason="end_of_test"（金曜引け＝時間切れ）
を観測する。D1「market+SL/TP 単玉＋pending_lifecycle で OCO・金曜引け」を実証。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from simulator.adapter.strategy.weekly_vol_band import WeeklyVolBand
from simulator.domain.bar import Bar
from simulator.domain.trading_week import week_id_of
from simulator.domain.variance_forecast import VarianceForecast
from simulator.usecase.models import BacktestConfig, SymbolSpec
from simulator.usecase.run_backtest import RunBacktestInteractor, RunBacktestRequest

# 1 週内の 5 本（同一週・epoch int 5分足）。O≈100, σ̂⁻=σ̂⁺=0.05 で
# S=100·exp(-1.96·0.05)=90.66, T=100·exp(0.674·0.05)=103.43。
_BASE = 1_707_912_000  # 2024-02-14 (W07)


def _bar(t, o, h, l, c):
    return Bar(time=t, open=o, high=h, low=l, close=c, volume=1.0, spread=0)


def _config():
    return BacktestConfig(
        tick_model="real_ticks", spread_model="fixed", sltp_tie="sl",
        fill_delay="next_tick", ohlc_order="auto", session_calendar="none",
        digits=2, legacy_quirks=False, return_basis="equity",
        entry_price_basis="current_open", pending_lifecycle=True,
    )


def _spec():
    return SymbolSpec(
        contract_size=1.0, volume_min=0.0, volume_max=1_000_000.0, volume_step=0.0,
        stops_level=0, digits=2, point_size=0.01, leverage=10.0,
    )


class _OpenIndicators:
    """セグメント bars の open 系列を "open" 指標として供給する registry。"""

    def __init__(self, bars):
        self._series = {"open": pd.Series([b.open for b in bars])}

    def get(self, name):
        return self._series[name]

    def update(self, bar_index):
        pass


class _TickModel:
    def __init__(self, ticks_by_t):
        self._t = ticks_by_t

    def ticks_of(self, bar, prev_close):
        return list(self._t.get(bar.time, []))


def _exit_reason_of_segment(bars, fc, *, spread=0):
    """1 週セグメントを実 engine で実行し、確定トレードの exit_reason を返す。

    spread>0 のとき各ティックの ask=bid+spread*point となり（engine 規約）、1 ティックが
    bid-ask 帯で両バリアを跨ぐ同一ティック両到達を構成できる（sltp_tie 検証用）。
    """
    if spread:
        bars = [
            Bar(time=b.time, open=b.open, high=b.high, low=b.low, close=b.close,
                volume=b.volume, spread=spread)
            for b in bars
        ]
    strat = WeeklyVolBand(forecast=fc, p_tp=0.50, capital=100_000.0, f_risk=0.01)
    strat.on_init({"digits": 2, "volume_step": 0.0}, None)
    ticks = {}
    for b in bars:
        ticks[b.time] = [
            (b.open, b.open, b.open, b.time),
            (b.high, b.high, b.high, b.time),
            (b.low, b.low, b.low, b.time),
            (b.close, b.close, b.close, b.time),
        ]
    interactor = RunBacktestInteractor(
        strategy=strat, indicators=_OpenIndicators(bars),
        tick_model=_TickModel(ticks),
    )
    req = RunBacktestRequest(
        config=_config(), bars=bars, symbol_spec=_spec(),
        initial_deposit=100_000.0, stop_out_level=0.0,
    )
    res = interactor.execute(req)
    assert len(res.trades) == 1, f"想定外トレード数: {len(res.trades)}"
    return res.trades[0].exit_reason


def _fc():
    wid = week_id_of(_BASE)
    return VarianceForecast(wid, sigma_plus=0.05, sigma_minus=0.05,
                            sigma_total_prev=0.04, estimable=True)


class TestWeekSegmentExitReasons:
    def test_tp_week(self):
        # 価格が T(≈103.4) を突破 → tp
        bars = [
            _bar(_BASE, 100.0, 100.5, 99.5, 100.0),
            _bar(_BASE + 300, 100.0, 104.0, 99.8, 103.5),  # high 104 > T
            _bar(_BASE + 600, 103.5, 104.0, 103.0, 103.8),
        ]
        assert _exit_reason_of_segment(bars, _fc()) == "tp"

    def test_stop_week(self):
        # 価格が S(≈90.7) を割る → sl
        bars = [
            _bar(_BASE, 100.0, 100.5, 99.5, 100.0),
            _bar(_BASE + 300, 100.0, 100.5, 90.0, 92.0),  # low 90 < S
            _bar(_BASE + 600, 92.0, 93.0, 91.0, 92.0),
        ]
        assert _exit_reason_of_segment(bars, _fc()) == "sl"

    def test_timeout_week_end_of_test(self):
        # S も T も未到達 → end_of_test（金曜引け＝時間切れ）
        bars = [
            _bar(_BASE, 100.0, 100.5, 99.5, 100.0),
            _bar(_BASE + 300, 100.0, 101.0, 99.0, 100.5),
            _bar(_BASE + 600, 100.5, 101.0, 99.5, 100.0),
        ]
        assert _exit_reason_of_segment(bars, _fc()) == "end_of_test"

    def test_stop_priority_when_low_precedes_in_tick_order(self):
        # 仕様 §2.6「同一バー両到達=ストップ優先」: engine の pending_lifecycle 経路は
        # ティック逐次判定（check_sltp_hit_at_tick・buy は q_bid=price 1 点）であり、
        # 1 ティックが S と T を同時成立させることは原理的に不可能（1 価格が S 以下かつ
        # T 以上にならない）。よって「同一バー両到達」はバー内ティック列で S が先に出れば
        # sl、T が先なら tp になる。S を先（Low→High より前）に置けばストップが選ばれる
        # ことを確認する（残存リスク: O→H→L→C 順では T が先＝spec §2.6 完全一致は
        # bar-mode check_sltp_hit を要する。報告の残存リスク参照）。
        base = _bar(_BASE, 100.0, 100.5, 99.5, 100.0)
        # 2 本目: ティック列を L(=89, sl) → H(=105, tp) の順で供給する特製足。
        wide = _bar(_BASE + 300, 100.0, 105.0, 89.0, 100.0)
        strat = WeeklyVolBand(forecast=_fc(), p_tp=0.50, capital=100_000.0, f_risk=0.01)
        strat.on_init({"digits": 2, "volume_step": 0.0}, None)
        bars = [base, wide, _bar(_BASE + 600, 100.0, 101.0, 99.0, 100.0)]
        ticks = {
            base.time: [(100.0, 100.0, 100.0, base.time)],
            wide.time: [
                (89.0, 89.0, 89.0, wide.time),   # SL を先に到達させる
                (105.0, 105.0, 105.0, wide.time),
            ],
            bars[2].time: [(100.0, 100.0, 100.0, bars[2].time)],
        }
        interactor = RunBacktestInteractor(
            strategy=strat, indicators=_OpenIndicators(bars), tick_model=_TickModel(ticks),
        )
        req = RunBacktestRequest(
            config=_config(), bars=bars, symbol_spec=_spec(),
            initial_deposit=100_000.0, stop_out_level=0.0,
        )
        res = interactor.execute(req)
        assert len(res.trades) == 1
        assert res.trades[0].exit_reason == "sl"
