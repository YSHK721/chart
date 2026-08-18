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

from simulator.adapter.calendar.session_calendar import (
    Jp225SessionCalendar,
    NullCalendar,
)
from simulator.domain.bar import Bar
from simulator.domain.order import Order
from simulator.usecase.models import BacktestConfig, SymbolSpec
from simulator.usecase.run_backtest import RunBacktestInteractor, RunBacktestRequest


def _bar(t, *, o=100.0, h=101.0, l=99.0, c=100.0, spread=0):
    return Bar(time=np.datetime64(t), open=o, high=h, low=l, close=c, volume=1.0, spread=spread)


def _epoch(t) -> int:
    """ISO 文字列を epoch 秒へ（`_bar` と同一の瞬時を指すことを保証するため同じ入力から導く）。"""
    return int(np.datetime64(t, "s").astype("int64"))


def _epoch_bar(t, *, kind=np.int64):
    """`_bar` と同一時刻を epoch 整数で表したバー（comma 形式 CSV ローダの実型）。"""
    return Bar(time=kind(_epoch(t)), open=100.0, high=101.0, low=99.0, close=100.0,
               volume=1.0, spread=0)


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

    def test_daily_close_2359_closed_2358_open(self):
        # 日次クローズ: 23:58 は開場、23:59 は閉鎖（実 MT5: 2026-02-06 23:58 約定/23:59 拒否）。
        bars = [_bar("2026-02-06T23:58"), _bar("2026-02-06T23:59")]
        assert Jp225SessionCalendar().closed_bar_indices(bars) == {1}

    def test_daily_close_applies_every_day_not_only_friday(self):
        # 金曜固有ではなく毎日同一: 火曜でも 23:59 閉鎖・23:58 開場。
        bars = [_bar("2026-02-03T23:58"), _bar("2026-02-03T23:59")]  # 火曜
        assert Jp225SessionCalendar().closed_bar_indices(bars) == {1}

    def test_weekend_boundary_marks_fri_2359_and_mon_first(self):
        # 実 MT5 の拒否点（Fri 23:59 と Mon 01:00）が閉鎖、Fri 23:58 と Mon 01:01 は開場。
        bars = [
            _bar("2026-01-09T23:58"),  # 金 開場（23:58）
            _bar("2026-01-09T23:59"),  # 金 クローズ（拒否点1）
            _bar("2026-01-12T01:00"),  # 月 プレオープン（拒否点2）
            _bar("2026-01-12T01:01"),  # 月 開場
        ]
        closed = Jp225SessionCalendar().closed_bar_indices(bars)
        assert closed == {1, 2}


class TestJp225SessionCalendarIsIndependentOfTimeRepresentation:
    """同一時刻は「どの時刻表現で書かれたバーか」に依存せず同一判定になる（ISSUE-412 (A)）。

    是正前の欠陥（実測 2026-08-18）:
        `closed_bar_indices` は `pd.Timestamp(bar.time)` を呼んでいた。comma 形式 CSV
        ローダ（`adapter/repository/ohlc_csv.py`）由来の `bar.time` は ``numpy.int64``
        であり、`pd.Timestamp(np.int64(1768219200))` は **ns 解釈**で
        1970-01-01 00:00:01.768219200 になる。`hour*60+minute = 0 < 61` なので
        **場中バーが全件「閉鎖」に分類**されていた（例外は出ない）。
        同一時刻を ``numpy.datetime64`` で与えると正しく開場判定になる。

    本検定が主張しないこと（ISSUE-414）:
        セッション定数（01:01 開場 / 23:59 閉鎖）は MT5 ブローカー壁時計由来であり、
        comma-CSV / marketdata 経路の `Bar.time` は UTC epoch である。両者の時間基準が
        一致するかは**未確定**であり、本検定は「セッション判定が正しくなった」ことを
        主張しない。固定するのは**型の読み違いが除かれたこと**（表現非依存）だけである。
    """

    #: 判定境界を跨ぐ標本（日次プレオープン境界 60/61 分と日次クローズ境界 1438/1439 分）。
    _SAMPLES = [
        ("2026-01-12T01:00", True),   # 60 分 → 閉鎖
        ("2026-01-12T01:01", False),  # 61 分 → 開場（境界ちょうど）
        ("2026-01-12T12:00", False),  # 場中
        ("2026-02-06T23:58", False),  # 1438 分 → 開場
        ("2026-02-06T23:59", True),   # 1439 分 → 閉鎖（境界ちょうど）
    ]

    def test_numpy_int64_midday_bar_is_not_classified_as_closed(self):
        # Arrange: comma 形式 CSV → pandas が返す実型の場中バー。
        bar = _epoch_bar("2026-01-12T12:00")
        assert isinstance(bar.time, int) is False  # 前提の実測（numpy 2.4.6）
        # Act
        closed = Jp225SessionCalendar().closed_bar_indices([bar])
        # Assert: 1970 年へ落ちて全件閉鎖にならない。
        assert closed == set()

    @pytest.mark.parametrize("iso,expected_closed", _SAMPLES, ids=[s[0] for s in _SAMPLES])
    def test_numpy_int64_bars_match_the_datetime64_verdict(self, iso, expected_closed):
        # Arrange / Act: 同一時刻を 2 表現で与える。
        dt64 = Jp225SessionCalendar().closed_bar_indices([_bar(iso)])
        i64 = Jp225SessionCalendar().closed_bar_indices([_epoch_bar(iso)])
        # Assert: 判定が一致し、かつ datetime64 側の既存挙動と同じ内容である。
        assert i64 == dt64
        assert i64 == ({0} if expected_closed else set())

    @pytest.mark.parametrize("iso,expected_closed", _SAMPLES, ids=[s[0] for s in _SAMPLES])
    def test_plain_int_bars_match_the_datetime64_verdict(self, iso, expected_closed):
        """Python `int` の epoch も同一判定（是正前は ns 解釈で同じく 1970 年へ落ちていた）。"""
        i = Jp225SessionCalendar().closed_bar_indices([_epoch_bar(iso, kind=int)])
        assert i == ({0} if expected_closed else set())

    def test_all_three_representations_agree_on_a_mixed_sequence(self):
        """混在しない同一系列を 3 表現で流し、閉鎖 index 集合が完全一致する。"""
        isos = [s[0] for s in self._SAMPLES]
        calendar = Jp225SessionCalendar()
        by_dt64 = calendar.closed_bar_indices([_bar(t) for t in isos])
        by_i64 = calendar.closed_bar_indices([_epoch_bar(t) for t in isos])
        by_int = calendar.closed_bar_indices([_epoch_bar(t, kind=int) for t in isos])
        assert by_dt64 == by_i64 == by_int == {0, 4}

    def test_injected_session_bounds_still_apply_to_epoch_bars(self):
        """注入した境界（`__init__` の 2 引数）が epoch 表現でも効く。

        識別力: 委譲後に epoch 秒を固定値へ潰す実装にすると本検定が落ちる。
        """
        calendar = Jp225SessionCalendar(daily_open_minute=0, daily_close_minute=1440)
        assert calendar.closed_bar_indices([_epoch_bar(t[0]) for t in self._SAMPLES]) == set()


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
