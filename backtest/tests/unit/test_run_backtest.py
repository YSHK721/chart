"""UC-001 RunBacktestInteractor の単体テスト（PROCESS §2 OnTick A〜I・DESIGN §6）。

スタブ/スパイ Port（StrategyPort/IndicatorPort/TickModelPort）と小さな合成 Bar 列で
Interactor を駆動する。usecase は domain のみ依存。

検証観点（Red→Green）:
    * OnTick A〜I の順序（スパイで呼び出し順を確認）
    * fill_delay=次tick で約定する（発注足では SL/TP 監視しない）
    * 同足 SL/TP 両ヒットで SL 優先（_execution 経由）
    * MarginCallError で Fail-Stop（部分結果破棄）
    * 確定トレード列が compute_stats に渡り BacktestResult.stats が算出される
    * 反対シグナルで reverse 決済（PROCESS §6）
"""
from __future__ import annotations

import numpy as np

import pytest

from backtest.domain.bar import Bar
from backtest.domain.exceptions import MarginCallError
from backtest.domain.order import Order
from backtest.usecase.models import BacktestConfig, BacktestResult, SymbolSpec
from backtest.usecase.run_backtest import RunBacktestInteractor, RunBacktestRequest


# ---- テスト用スタブ/スパイ Port ----

class SpyIndicatorPort:
    def __init__(self, log):
        self._log = log

    def get(self, name):
        self._log.append(("indicator.get", name))
        return None

    def update(self, bar_index):
        self._log.append(("indicator.update", bar_index))


class SpyStrategyPort:
    """on_new_bar が orders_by_bar[bar_index] を返すスパイ。"""

    def __init__(self, log, orders_by_bar=None, close_decision="hold"):
        self._log = log
        self._orders_by_bar = orders_by_bar or {}
        self._close_decision = close_decision

    def on_init(self, config, indicators):
        self._log.append(("strategy.on_init",))

    def on_new_bar(self, bar_index, indicators, account):
        self._log.append(("strategy.on_new_bar", bar_index))
        return list(self._orders_by_bar.get(bar_index, []))

    def on_position_check(self, position, bar_index, indicators):
        self._log.append(("strategy.on_position_check", bar_index))
        return self._close_decision


class StubTickModelPort:
    """各 Bar につき OHLC を内包する 1 ティック（price=close, bid/ask）を返す。"""

    def __init__(self, log=None):
        self._log = log

    def ticks_of(self, bar, prev_close):
        if self._log is not None:
            self._log.append(("tick.ticks_of",))
        return [(bar.close, bar.low, bar.high, bar.time)]


def _bar(t, o, h, l, c):
    return Bar(time=t, open=o, high=h, low=l, close=c, volume=1.0, spread=0)


def _config():
    return BacktestConfig(
        tick_model="ohlc_simulate",
        spread_model="fixed",
        sltp_tie="sl",
        fill_delay="next_tick",
        ohlc_order="auto",
        session_calendar="none",
        digits=5,
        legacy_quirks=False,
        return_basis="equity",
    )


def _symbol_spec():
    return SymbolSpec(
        contract_size=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=0,
        digits=5,
        point_size=0.00001,
        leverage=100.0,
    )


def _request(bars, *, config=None, initial_deposit=10_000.0, stop_out_level=0.0,
             symbol_spec=None):
    return RunBacktestRequest(
        config=config or _config(),
        bars=bars,
        symbol_spec=symbol_spec or _symbol_spec(),
        initial_deposit=initial_deposit,
        stop_out_level=stop_out_level,
    )


# ---- B4: OnTick A〜I 順序 ----

class TestOnTickOrder:
    def test_per_bar_calls_indicator_update_then_strategy_signal(self):
        # Arrange: 2 本の合成 Bar・発注なし戦略
        log = []
        bars = [
            _bar(np.datetime64("2024-01-01T00:00"), 1.10, 1.11, 1.09, 1.105),
            _bar(np.datetime64("2024-01-01T00:01"), 1.105, 1.115, 1.10, 1.11),
        ]
        interactor = RunBacktestInteractor(
            strategy=SpyStrategyPort(log),
            indicators=SpyIndicatorPort(log),
            tick_model=StubTickModelPort(),
        )
        # Act
        interactor.execute(_request(bars))
        # Assert: on_init が最初・各 bar で indicator.update → strategy.on_new_bar の順
        # （C 指標取得 → E シグナルの順序が両 bar で繰り返される＝呼出順をスパイで固定）
        assert log == [
            ("strategy.on_init",),
            ("indicator.update", 0),
            ("strategy.on_new_bar", 0),
            ("indicator.update", 1),
            ("strategy.on_new_bar", 1),
        ]


# ---- B5: fill_delay=次tick（発注足では SL/TP 監視しない） ----

class TestFillDelayNextTick:
    def test_position_opened_on_bar_is_not_sltp_checked_same_bar(self):
        # Arrange: bar0 で買い発注。bar0 自身の low(1.05) は SL(1.095) を貫くが、
        # fill_delay=next_tick のため発注足では SL ヒットさせない。
        # bar1 の low(1.04) でも SL を貫く → bar1 で初めて SL 決済される。
        log = []
        order = Order(side="buy", kind="market", volume=1.0, price=None,
                      sl=1.095, tp=1.200)
        bars = [
            _bar(np.datetime64("2024-01-01T00:00"), 1.10, 1.11, 1.05, 1.10),
            _bar(np.datetime64("2024-01-01T00:01"), 1.10, 1.11, 1.04, 1.06),
        ]
        strategy = SpyStrategyPort(log, orders_by_bar={0: [order]})
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort(log),
            tick_model=StubTickModelPort(),
        )
        # Act
        result = interactor.execute(_request(bars))
        # Assert: 確定トレードは 1 件・SL 決済・exit は bar1（entry_time != exit_time）
        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.exit_reason == "sl"
        assert trade.entry_time == bars[0].time
        assert trade.exit_time == bars[1].time


# ---- B8: 確定トレード列が compute_stats に渡り BacktestResult.stats が算出される ----

class TestStatsComputedFromTrades:
    def test_confirmed_trades_drive_compute_stats(self):
        # Arrange: bar0 買い建て、bar1 で SL 決済（既知の損失）。
        # entry=1.10(ask=close)・exit=SL=1.095・contract=1.0 → pnl=(1.095-1.10)*1*1=-0.005
        log = []
        order = Order(side="buy", kind="market", volume=1.0, price=None,
                      sl=1.095, tp=1.300)
        bars = [
            _bar(np.datetime64("2024-01-01T00:00"), 1.10, 1.11, 1.10, 1.10),
            _bar(np.datetime64("2024-01-01T00:01"), 1.10, 1.11, 1.09, 1.10),
        ]
        strategy = SpyStrategyPort(log, orders_by_bar={0: [order]})
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort(log),
            tick_model=StubTickModelPort(),
        )
        # Act
        result = interactor.execute(_request(bars))
        # Assert: stats が確定トレードから算出される（スタブ0でない）
        assert isinstance(result, BacktestResult)
        assert result.stats.trades == 1
        assert result.stats.loss_trades == 1
        assert result.stats.profit == pytest.approx(result.trades[0].pnl())
        assert result.stats.profit == pytest.approx(-0.005)
        # 決済 deal が deals 列に記録される（決済明細の追跡可能性）
        assert len(result.deals) == 1
        assert result.deals[0].direction == "out"
        assert result.deals[0].profit == pytest.approx(-0.005)


# ---- B9: 同足 SL/TP 両ヒットで SL 優先（Interactor 結線レベル・PROCESS §5 決定論 #3） ----

class TestSameBarSlTpTieSlPriorityViaInteractor:
    def test_same_bar_both_hit_resolves_to_sl_through_interactor_wiring(self):
        # Arrange: bar0 で買い建て（fill_delay=next_tick のため bar0 は監視外）。
        # bar1 は high(1.30)>=tp(1.20) かつ low(1.00)<=sl(1.10) で SL/TP 同足両ヒット。
        # config.sltp_tie="sl" が Interactor 経由で check_sltp_hit へ渡る結線を固定する。
        log = []
        order = Order(side="buy", kind="market", volume=1.0, price=None,
                      sl=1.10, tp=1.20)
        bars = [
            _bar(np.datetime64("2024-01-01T00:00"), 1.15, 1.16, 1.14, 1.15),
            _bar(np.datetime64("2024-01-01T00:01"), 1.15, 1.30, 1.00, 1.15),
        ]
        strategy = SpyStrategyPort(log, orders_by_bar={0: [order]})
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort(log),
            tick_model=StubTickModelPort(),
        )
        # Act
        result = interactor.execute(_request(bars))
        # Assert: 両ヒット足で確定したトレードは SL 優先（TP ではない）
        assert len(result.trades) == 1
        assert result.trades[0].exit_reason == "sl"
        assert result.trades[0].exit_time == bars[1].time


# ---- B6: MarginCallError で Fail-Stop（部分結果破棄・DESIGN §6.1/§6.2） ----

class TestFailStopOnMarginCall:
    def test_margin_call_raises_and_returns_no_partial_result(self):
        # Arrange: 余剰証拠金を食い潰す含み損で margin_level < stop_out にする。
        # 1 lot 買い@1.10、contract=100000、leverage=100 → 必要証拠金=1100。
        # bar1 で close=1.00 → 含み損 -10000 → equity=0 → margin_level=0% < 50%。
        log = []
        order = Order(side="buy", kind="market", volume=1.0, price=None)
        bars = [
            _bar(np.datetime64("2024-01-01T00:00"), 1.10, 1.10, 1.10, 1.10),
            _bar(np.datetime64("2024-01-01T00:01"), 1.10, 1.10, 1.00, 1.00),
        ]
        spec = SymbolSpec(
            contract_size=100_000.0, volume_min=0.01, volume_max=100.0,
            volume_step=0.01, stops_level=0, digits=5, point_size=0.00001,
            leverage=100.0,
        )
        strategy = SpyStrategyPort(log, orders_by_bar={0: [order]})
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort(log),
            tick_model=StubTickModelPort(),
        )
        req = _request(bars, initial_deposit=10_000.0, stop_out_level=50.0,
                       symbol_spec=spec)
        # Act / Assert: MarginCallError を送出し、BacktestResult を返さない（部分結果破棄）
        with pytest.raises(MarginCallError):
            interactor.execute(req)


# ---- B7: 反対シグナルで reverse 決済（PROCESS §6） ----

class TestReverseClose:
    def test_opposite_signal_closes_existing_position_as_reverse(self):
        # Arrange: bar0 で買い建て、bar1 で反対（売り）シグナル → 既存買いを reverse 決済
        log = []
        buy = Order(side="buy", kind="market", volume=1.0, price=None)
        sell = Order(side="sell", kind="market", volume=1.0, price=None)
        bars = [
            _bar(np.datetime64("2024-01-01T00:00"), 1.10, 1.11, 1.09, 1.10),
            _bar(np.datetime64("2024-01-01T00:01"), 1.10, 1.13, 1.10, 1.12),
        ]
        strategy = SpyStrategyPort(log, orders_by_bar={0: [buy], 1: [sell]})
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort(log),
            tick_model=StubTickModelPort(),
        )
        # Act
        result = interactor.execute(_request(bars))
        # Assert: 買いが reverse 決済される（exit_reason=reverse・bar1 終値で決済）
        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.side == "buy"
        assert trade.exit_reason == "reverse"
        assert trade.entry_time == bars[0].time
        assert trade.exit_time == bars[1].time
