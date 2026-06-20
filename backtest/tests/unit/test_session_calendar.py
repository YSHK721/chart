"""セッションカレンダー（SessionCalendarPort 実装）と Interactor 適用の単体テスト。

実 MT5 突合（ISSUE-018）で判明した「市場閉鎖時間帯の成行をエンジンが約定してしまい
MT5 と +2 トレード乖離」を是正する SessionCalendar の振る舞いを固定する:
    * NullCalendar は常に空集合（既定・常時開場＝既定経路 byte-identical）
    * Jp225SessionCalendar は日次プレオープン（01:00 以前）と金曜 23:55 以降を閉鎖
    * Interactor は閉鎖バーで新規成行を約定せず、戦略（保有側 level-trigger）が
      次の開場バーで自動再発注する（実 MT5 fail→retry→開場約定の再現）
"""
from __future__ import annotations

import numpy as np
import pytest

from backtest.adapter.calendar.session_calendar import (
    Jp225SessionCalendar,
    NullCalendar,
)
from backtest.domain.bar import Bar
from backtest.domain.order import Order
from backtest.usecase.models import BacktestConfig, SymbolSpec
from backtest.usecase.run_backtest import RunBacktestInteractor, RunBacktestRequest


def _bar(t, *, o=100.0, h=101.0, l=99.0, c=100.0, spread=0):
    return Bar(time=np.datetime64(t), open=o, high=h, low=l, close=c, volume=1.0, spread=spread)


# ---- カレンダー adapter 単体（純関数・高速） ----

class TestNullCalendar:
    def test_returns_empty_set(self):
        bars = [_bar("2026-01-12T01:00"), _bar("2026-01-09T23:59")]
        assert NullCalendar().closed_bar_indices(bars) == set()


class TestJp225SessionCalendar:
    def test_daily_preopen_0100_closed_0101_open(self):
        # 01:00（=60分）は閉鎖、01:01（=61分）は開場。
        bars = [_bar("2026-01-12T01:00"), _bar("2026-01-12T01:01")]
        closed = Jp225SessionCalendar().closed_bar_indices(bars)
        assert closed == {0}

    def test_friday_2355_onward_closed(self):
        # 2026-01-09 は金曜。23:52 は開場、23:55/23:59 は週末クローズ。
        bars = [
            _bar("2026-01-09T23:52"),  # 金 開場
            _bar("2026-01-09T23:55"),  # 金 クローズ境界
            _bar("2026-01-09T23:59"),  # 金 クローズ
        ]
        closed = Jp225SessionCalendar().closed_bar_indices(bars)
        assert closed == {1, 2}

    def test_non_friday_late_night_open(self):
        # 2026-01-06 は火曜。23:59 でも開場（金曜クローズ規則の対象外）。
        bars = [_bar("2026-01-06T23:59")]
        assert Jp225SessionCalendar().closed_bar_indices(bars) == set()

    def test_weekend_boundary_marks_fri_last_and_mon_first(self):
        # 実 MT5 の拒否点（Fri 23:59 と Mon 01:00）が両方閉鎖になることを固定。
        bars = [
            _bar("2026-01-09T23:52"),  # 金 開場
            _bar("2026-01-09T23:59"),  # 金 クローズ（拒否点1）
            _bar("2026-01-12T01:00"),  # 月 プレオープン（拒否点2）
            _bar("2026-01-12T01:01"),  # 月 開場
        ]
        closed = Jp225SessionCalendar().closed_bar_indices(bars)
        assert closed == {1, 2}


# ---- Interactor への適用（bar-mode 経路・閉鎖バーで約定しない） ----

class _SpyIndicator:
    def get(self, name):
        return None

    def update(self, bar_index):
        pass


class _SpyStrategy:
    """保有側 level-trigger を模す: 与えた signals[bar_index] が held と異なれば成行。"""

    def __init__(self, signals):
        self._signals = signals

    def on_init(self, config, indicators):
        pass

    def on_new_bar(self, bar_index, indicators, account):
        signal = self._signals.get(bar_index)
        if signal is None:
            return []
        held = {p.side for p in getattr(account, "open_positions", [])}
        if signal in held:
            return []
        return [Order(side=signal, kind="market", volume=1.0, price=None)]

    def on_position_check(self, position, bar_index, indicators):
        return "hold"


class _FixedCalendar:
    """指定 index を閉鎖扱いにするテスト用カレンダー。"""

    def __init__(self, closed):
        self._closed = set(closed)

    def closed_bar_indices(self, bars):
        return set(self._closed)


def _config(**ov):
    base = dict(
        tick_model="ohlc_expand", spread_model="fixed", sltp_tie="sl",
        fill_delay="next_tick", ohlc_order="auto", session_calendar="none",
        digits=5, legacy_quirks=False, return_basis="equity",
        entry_price_basis="current_open",
    )
    base.update(ov)
    return BacktestConfig(**base)


def _spec():
    return SymbolSpec(contract_size=1.0, volume_min=0.01, volume_max=100.0,
                      volume_step=0.01, stops_level=0, digits=5,
                      point_size=0.00001, leverage=100.0)


def _request(bars, **ov):
    base = dict(config=_config(), bars=bars, symbol_spec=_spec(),
                initial_deposit=10_000.0, stop_out_level=0.0)
    base.update(ov)
    return RunBacktestRequest(**base)


class TestInteractorRejectsClosedBarOrders:
    def _bars(self):
        # bar0 開場 / bar1 閉鎖 / bar2 開場。spread=0 で建値=open。
        return [
            _bar("2026-01-12T01:01", o=100.0, h=100.5, l=99.5, c=100.0),
            _bar("2026-01-12T01:02", o=110.0, h=110.5, l=109.5, c=110.0),  # 閉鎖（成行拒否）
            _bar("2026-01-12T01:03", o=120.0, h=120.5, l=119.5, c=120.0),
        ]

    def test_order_on_closed_bar_is_dropped_and_reissued_next_open_bar(self):
        # bar1 で sell シグナル → 閉鎖のため約定しない（保有なしのまま）。
        # bar2 でも sell シグナル → 開場のため約定（entry=bar2.open=120.0）。
        bars = self._bars()
        strategy = _SpyStrategy(signals={1: "sell", 2: "sell"})
        interactor = RunBacktestInteractor(
            strategy=strategy, indicators=_SpyIndicator(),
            tick_model=None, session_calendar=_FixedCalendar({1}),
        )
        result = interactor.execute(_request(bars))
        # bar1 では建たず、bar2 開場で初約定。終了時は1ポジ保有（決済イベントなし）。
        # 確定トレードは reverse/SL/TP/stop-out が無いため 0 件、建玉は bar2 で1件。
        assert len(result.trades) == 0  # 反対シグナル無し→決済が起きない
        # equity_curve は全 trading バー分（bar-mode は 1 バー1点）記録される。
        assert len(result.equity_curve) == 3

    def test_every_tick_path_also_skips_closed_bar_order(self):
        # 回帰防止（レビュー🟡）: every-tick(real_ticks) 経路でも閉鎖バーは約定しない。
        # bar1 で sell シグナル→閉鎖のため建たず、bar2 開場で初約定（保有不変→再発注）。
        bars = self._bars()
        ticks = {b.time: [(b.close, b.close, b.close, b.time)] for b in bars}

        class _ListTick:
            def ticks_of(self, bar, prev_close):
                return list(ticks.get(bar.time, []))

        strategy = _SpyStrategy(signals={1: "sell", 2: "sell"})
        interactor = RunBacktestInteractor(
            strategy=strategy, indicators=_SpyIndicator(),
            tick_model=_ListTick(), session_calendar=_FixedCalendar({1}),
        )
        result = interactor.execute(_request(bars, config=_config(tick_model="real_ticks")))
        # 反対シグナル無し→決済0件。閉鎖 bar1 で建たず bar2 で建玉（every-tick も抑止）。
        assert len(result.trades) == 0

    def test_every_tick_without_calendar_fills_on_that_bar(self):
        # 対照: every-tick でカレンダー未注入なら bar1 で約定（閉鎖ガードが効いていない確認）。
        bars = self._bars()
        ticks = {b.time: [(b.close, b.close, b.close, b.time)] for b in bars}

        class _ListTick:
            def ticks_of(self, bar, prev_close):
                return list(ticks.get(bar.time, []))

        strategy = _SpyStrategy(signals={1: "sell", 2: "buy"})
        interactor = RunBacktestInteractor(
            strategy=strategy, indicators=_SpyIndicator(), tick_model=_ListTick(),
        )
        result = interactor.execute(_request(bars, config=_config(tick_model="real_ticks")))
        # bar1 sell 建て → bar2 buy ドテン → reverse 決済1件。entry=bar1.open=110.0。
        assert len(result.trades) == 1
        assert result.trades[0].side == "sell"
        assert result.trades[0].entry_price == pytest.approx(110.0)

    def test_without_calendar_order_fills_on_that_bar(self):
        # カレンダー未注入（None）＝既定経路: bar1 の sell が当該バーで約定する。
        bars = self._bars()
        strategy = _SpyStrategy(signals={1: "sell", 2: "buy"})
        interactor = RunBacktestInteractor(
            strategy=strategy, indicators=_SpyIndicator(), tick_model=None,
        )
        result = interactor.execute(_request(bars))
        # bar1 で sell 建て → bar2 で buy（ドテン）→ reverse 決済1件確定。
        assert len(result.trades) == 1
        assert result.trades[0].side == "sell"
        # entry は bar1.open=110.0（閉鎖されず約定）。
        assert result.trades[0].entry_price == pytest.approx(110.0)
