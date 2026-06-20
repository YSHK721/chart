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

from simulator.domain.bar import Bar
from simulator.domain.exceptions import MarginCallError
from simulator.domain.order import Order
from simulator.usecase.models import BacktestConfig, BacktestResult, SymbolSpec
from simulator.usecase.run_backtest import RunBacktestInteractor, RunBacktestRequest


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


def _bar_sp(t, o, h, l, c, *, spread):
    """spread を指定できる Bar 生成ヘルパ（層2 の Ask 評価テスト用）。"""
    return Bar(time=t, open=o, high=h, low=l, close=c, volume=1.0, spread=spread)


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


# ---- cycle2-2a: account 伝播（Interactor が on_new_bar に実 Account を渡す） ----

class CapturingStrategyPort:
    """on_new_bar に渡された account を bar_index ごとに記録するスパイ。

    既存 SpyStrategyPort（account を捨てる）と独立。account 伝播の結線を
    検証するために account 引数を保持する。
    """

    def __init__(self, orders_by_bar=None):
        self._orders_by_bar = orders_by_bar or {}
        self.received_accounts = []  # [(bar_index, account)]

    def on_init(self, config, indicators):
        pass

    def on_new_bar(self, bar_index, indicators, account):
        self.received_accounts.append((bar_index, account))
        return list(self._orders_by_bar.get(bar_index, []))

    def on_position_check(self, position, bar_index, indicators):
        return "hold"


class TestAccountPropagation:
    def test_on_new_bar_receives_real_account_not_none(self):
        # Arrange: 発注なし 2 本。account 引数を捕捉する。
        from simulator.domain.account import Account

        bars = [
            _bar(np.datetime64("2024-01-01T00:00"), 1.10, 1.11, 1.09, 1.105),
            _bar(np.datetime64("2024-01-01T00:01"), 1.105, 1.115, 1.10, 1.11),
        ]
        strategy = CapturingStrategyPort()
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort([]),
            tick_model=StubTickModelPort(),
        )
        # Act
        interactor.execute(_request(bars))
        # Assert: 全 bar で実 Account が渡る（None でない・同一インスタンス）
        assert len(strategy.received_accounts) == 2
        for _, acct in strategy.received_accounts:
            assert acct is not None
            assert isinstance(acct, Account)
        # 同一 run 内で同じ Account インスタンスが伝播する
        assert strategy.received_accounts[0][1] is strategy.received_accounts[1][1]

    def test_account_open_positions_reflects_held_position_on_next_bar(self):
        # Arrange: bar0 で買い建て。bar1 の on_new_bar 時点で
        # account.open_positions に保有が反映されている（同方向抑止/ドテンの本番前提）。
        buy = Order(side="buy", kind="market", volume=1.0, price=None)
        bars = [
            _bar(np.datetime64("2024-01-01T00:00"), 1.10, 1.11, 1.09, 1.10),
            _bar(np.datetime64("2024-01-01T00:01"), 1.10, 1.13, 1.10, 1.12),
        ]
        strategy = CapturingStrategyPort(orders_by_bar={0: [buy]})
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort([]),
            tick_model=StubTickModelPort(),
        )
        # Act
        interactor.execute(_request(bars))
        # Assert: bar1 の on_new_bar 受領時に保有 1 件（買い）が見える
        bar1_account = strategy.received_accounts[1][1]
        assert len(bar1_account.open_positions) == 1
        assert bar1_account.open_positions[0].side == "buy"


# ---- cycle2-2b: config駆動 spread + 現バー open 約定 ----

def _bar_s(t, o, h, l, c, spread):
    return Bar(time=t, open=o, high=h, low=l, close=c, volume=1.0, spread=spread)


def _config_open_fill():
    """現バー open 基準 + spread 適用モードの config（cycle2 で追加するフィールド）。"""
    return BacktestConfig(
        tick_model="ohlc_simulate",
        spread_model="fixed",
        sltp_tie="sl",
        fill_delay="next_tick",
        ohlc_order="auto",
        session_calendar="none",
        digits=1,
        legacy_quirks=False,
        return_basis="equity",
        entry_price_basis="current_open",
    )


def _jp225_spec():
    """JP225 相当（point_size=0.1・contract=10・leverage=10）。"""
    return SymbolSpec(
        contract_size=10.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=0,
        digits=1,
        point_size=0.1,
        leverage=10.0,
    )


class TestConfigDrivenSpreadOpenFill:
    # bar0 で建て、bar1 で反対シグナル → reverse 決済で確定トレードを生成し
    # entry_price を観測する（建玉のままだと result.trades が空になる）。
    _BARS = [
        _bar_s(np.datetime64("2025-01-02T01:01"), 39402.0, 39450.0, 39400.0, 39440.0, 100),
        _bar_s(np.datetime64("2025-01-02T01:02"), 39440.0, 39460.0, 39430.0, 39450.0, 100),
    ]

    def test_buy_fills_at_open_plus_spread_times_point_when_enabled(self):
        # Arrange: 実 MT5 fixture アンカー（39412 = open 39402 + spread100×point0.1）の小データ再現。
        # entry_price_basis="current_open" + spread=100 + point_size=0.1。
        buy = Order(side="buy", kind="market", volume=1.0, price=None)
        sell = Order(side="sell", kind="market", volume=1.0, price=None)
        strategy = SpyStrategyPort([], orders_by_bar={0: [buy], 1: [sell]})
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort([]),
            tick_model=StubTickModelPort(),
        )
        req = _request(self._BARS, config=_config_open_fill(), symbol_spec=_jp225_spec())
        # Act
        result = interactor.execute(req)
        # Assert: buy entry_price == open(39402) + spread(100) * point(0.1) == 39412.0
        assert result.trades[0].side == "buy"
        assert result.trades[0].entry_price == pytest.approx(39412.0)

    def test_sell_fills_at_open_bid_when_enabled(self):
        # Arrange: sell は bid（=現バー open）で約定（spread 寄与 0）。
        sell = Order(side="sell", kind="market", volume=1.0, price=None)
        buy = Order(side="buy", kind="market", volume=1.0, price=None)
        strategy = SpyStrategyPort([], orders_by_bar={0: [sell], 1: [buy]})
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort([]),
            tick_model=StubTickModelPort(),
        )
        req = _request(self._BARS, config=_config_open_fill(), symbol_spec=_jp225_spec())
        # Act
        result = interactor.execute(req)
        # Assert: sell entry_price == open(39402)（bid 基準・spread 寄与 0）
        assert result.trades[0].side == "sell"
        assert result.trades[0].entry_price == pytest.approx(39402.0)

    def test_default_config_keeps_close_fill_zero_spread(self):
        # 後方互換特性化: 新フィールド既定（"close"）では従来どおり buy=close・spread 無視。
        buy = Order(side="buy", kind="market", volume=1.0, price=None)
        sell = Order(side="sell", kind="market", volume=1.0, price=None)
        strategy = SpyStrategyPort([], orders_by_bar={0: [buy], 1: [sell]})
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort([]),
            tick_model=StubTickModelPort(),
        )
        # 既定 config（entry_price_basis 未指定）。spread=100 だが従来は無視。
        req = _request(self._BARS, symbol_spec=_jp225_spec())
        # Act
        result = interactor.execute(req)
        # Assert: 従来どおり close(39440) で約定（open でも open+spread でもない）
        assert result.trades[0].side == "buy"
        assert result.trades[0].entry_price == pytest.approx(39440.0)


# ---- cycle2-2c: equity カーブが毎バー floating 込みで記録される（特性化・既実装の退行防止） ----

class TestPerBarEquityCurve:
    def test_equity_curve_has_one_entry_per_bar_including_floating_pnl(self):
        # Arrange: bar0 で買い建て（決済しない）。各バーで equity が記録される。
        # contract=1.0 / leverage=100 / 1 lot 買い@close=1.10。
        # bar1 close=1.20 → 含み益 (1.20-1.10)*1*1 = +0.10 が equity に乗る。
        order = Order(side="buy", kind="market", volume=1.0, price=None)
        bars = [
            _bar(np.datetime64("2024-01-01T00:00"), 1.10, 1.11, 1.09, 1.10),
            _bar(np.datetime64("2024-01-01T00:01"), 1.10, 1.21, 1.10, 1.20),
        ]
        strategy = SpyStrategyPort([], orders_by_bar={0: [order]})
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort([]),
            tick_model=StubTickModelPort(),
        )
        # Act
        result = interactor.execute(_request(bars, initial_deposit=10_000.0))
        # Assert: トレード時点でなく毎バー記録（len == bar 数）
        assert len(result.equity_curve) == 2
        # bar0: 建値直後 close=entry → floating 0 → equity == balance == 10000
        assert result.equity_curve[0] == pytest.approx(10_000.0)
        # bar1: 含み益 +0.10（floating 込み）が equity に反映される（balance のみではない）
        assert result.equity_curve[1] == pytest.approx(10_000.10)


# ---- 層2: floating_pnl_basis を engine の Account に結線する（config-gated・既定 close） ----

class TestFloatingPnlBasisWiring:
    """config.floating_pnl_basis を engine が Account へ伝播し、保有ポジの含み損益を
    決済価格基準（売り=Ask=close+spread×point）で評価することを固定する。

    既定 "close" は従来どおり close 固定評価＝後方互換（別テストで固定）。
    """

    @staticmethod
    def _bars():
        # bar0 で sell 建て（current_open: bid=open で約定）、bar1 を保有のまま評価。
        return [
            _bar_sp(np.datetime64("2024-01-01T00:00"), 100.0, 100.0, 100.0, 100.0, spread=10),
            _bar_sp(np.datetime64("2024-01-01T00:01"), 101.0, 101.0, 101.0, 101.0, spread=10),
        ]

    def _config(self, basis):
        c = _config()
        c.entry_price_basis = "current_open"
        c.floating_pnl_basis = basis
        return c

    def test_sell_floating_uses_ask_when_bid_ask(self):
        # Arrange: floating_pnl_basis="bid_ask"・point=0.1・spread=10。
        #   sell 建て@bar0 open=100（bid）。bar1 close=101・Ask=101+10*0.1=102。
        order = Order(side="sell", kind="market", volume=1.0, price=None)
        bars = self._bars()
        strategy = SpyStrategyPort([], orders_by_bar={0: [order]})
        interactor = RunBacktestInteractor(
            strategy=strategy, indicators=SpyIndicatorPort([]),
            tick_model=StubTickModelPort(),
        )
        spec = SymbolSpec(
            contract_size=1.0, volume_min=0.01, volume_max=100.0, volume_step=0.01,
            stops_level=0, digits=1, point_size=0.1, leverage=100.0,
        )
        req = RunBacktestRequest(
            config=self._config("bid_ask"), bars=bars, symbol_spec=spec,
            initial_deposit=10_000.0, stop_out_level=0.0,
        )
        # Act
        result = interactor.execute(req)
        # Assert: bar1 の含み損 = (Ask 102 - entry 100)*1*1*(-1) = -2 が equity に反映。
        #   close 評価なら (101-100)*-1 = -1。Ask 評価で -2＝より悲観的（spread 加算）。
        assert result.equity_curve[1] == pytest.approx(10_000.0 - 2.0)

    def test_default_close_basis_ignores_spread_in_engine(self):
        # 後方互換: 既定 "close" では engine の Account も close 固定評価（spread 無視）。
        order = Order(side="sell", kind="market", volume=1.0, price=None)
        bars = self._bars()
        strategy = SpyStrategyPort([], orders_by_bar={0: [order]})
        interactor = RunBacktestInteractor(
            strategy=strategy, indicators=SpyIndicatorPort([]),
            tick_model=StubTickModelPort(),
        )
        spec = SymbolSpec(
            contract_size=1.0, volume_min=0.01, volume_max=100.0, volume_step=0.01,
            stops_level=0, digits=1, point_size=0.1, leverage=100.0,
        )
        req = RunBacktestRequest(
            config=self._config("close"), bars=bars, symbol_spec=spec,
            initial_deposit=10_000.0, stop_out_level=0.0,
        )
        # Act
        result = interactor.execute(req)
        # Assert: bar1 close 評価 = (101-100)*-1 = -1（spread=10 を無視・従来不変）。
        assert result.equity_curve[1] == pytest.approx(10_000.0 - 1.0)


# ---- cycle2-2d: 証拠金 stop_out（既存 TestFailStopOnMarginCall で raise を固定済） ----
#   stop_out の Fail-Stop（raise）契約は既実装かつ既存テストで固定済のため新規追加なし。
#   「強制決済して継続」セマンティクスへの変更は既存 raise 契約を破壊するため本 cycle 対象外
#   （判断点として報告）。


# ---- cycle4-①: reverse 決済（short→buy 約定）の spread 加算（current_open） ----

class TestReverseShortCloseSpread:
    def test_short_reverse_close_fills_at_open_plus_spread_times_point(self):
        # Arrange: current_open + spread=100 + point=0.1。
        # bar0 で sell 建て（bid=open で約定）、bar1 で反対 buy シグナル →
        # short を reverse 決済する。short 決済は buy 約定なので
        # ask = open + spread×point で決済されるべき（現状は raw open でバグ）。
        # bar1: open=39440, spread=100, point=0.1 → 決済 ask = 39440 + 10 = 39450。
        sell = Order(side="sell", kind="market", volume=1.0, price=None)
        buy = Order(side="buy", kind="market", volume=1.0, price=None)
        strategy = SpyStrategyPort([], orders_by_bar={0: [sell], 1: [buy]})
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort([]),
            tick_model=StubTickModelPort(),
        )
        req = _request(
            TestConfigDrivenSpreadOpenFill._BARS,
            config=_config_open_fill(),
            symbol_spec=_jp225_spec(),
        )
        # Act
        result = interactor.execute(req)
        # Assert: reverse 決済された short の exit_price == bar1.open + spread×point
        reverse_trade = next(t for t in result.trades if t.exit_reason == "reverse")
        assert reverse_trade.side == "sell"
        assert reverse_trade.exit_price == pytest.approx(39450.0)


# ---- cycle4-②: stop_out_action config（fail_stop 既定 / close_and_halt） ----

def _margin_call_setup(*, stop_out_action=None, orders_by_bar=None, bars=None,
                       entry_price_basis=None, floating_pnl_basis=None,
                       stop_out_at_open=None):
    """margin_level < stop_out を bar1 で発生させる共通セットアップ。

    1 lot 買い@1.10、contract=100000、leverage=100 → 必要証拠金=1100。
    bar1 close=1.00 → 含み損 -10000 → equity=0 → margin_level=0% < 50%。
    """
    cfg_kwargs = {}
    if stop_out_action is not None:
        cfg_kwargs["stop_out_action"] = stop_out_action
    if entry_price_basis is not None:
        cfg_kwargs["entry_price_basis"] = entry_price_basis
    if floating_pnl_basis is not None:
        cfg_kwargs["floating_pnl_basis"] = floating_pnl_basis
    if stop_out_at_open is not None:
        cfg_kwargs["stop_out_at_open"] = stop_out_at_open
    config = BacktestConfig(
        tick_model="ohlc_simulate",
        spread_model="fixed",
        sltp_tie="sl",
        fill_delay="next_tick",
        ohlc_order="auto",
        session_calendar="none",
        digits=5,
        legacy_quirks=False,
        return_basis="equity",
        **cfg_kwargs,
    )
    if bars is None:
        bars = [
            _bar(np.datetime64("2024-01-01T00:00"), 1.10, 1.10, 1.10, 1.10),
            _bar(np.datetime64("2024-01-01T00:01"), 1.10, 1.10, 1.00, 1.00),
        ]
    spec = SymbolSpec(
        contract_size=100_000.0, volume_min=0.01, volume_max=100.0,
        volume_step=0.01, stops_level=0, digits=5, point_size=0.00001,
        leverage=100.0,
    )
    order = Order(side="buy", kind="market", volume=1.0, price=None)
    strategy = SpyStrategyPort([], orders_by_bar=orders_by_bar or {0: [order]})
    interactor = RunBacktestInteractor(
        strategy=strategy,
        indicators=SpyIndicatorPort([]),
        tick_model=StubTickModelPort(),
    )
    req = _request(bars, initial_deposit=10_000.0, stop_out_level=50.0,
                   symbol_spec=spec, config=config)
    return interactor, req


class TestStopOutActionConfig:
    def test_default_action_fail_stop_raises(self):
        # 既定（stop_out_action 未指定）= fail_stop = 従来どおり MarginCallError。
        interactor, req = _margin_call_setup()
        with pytest.raises(MarginCallError):
            interactor.execute(req)

    def test_close_and_halt_force_closes_and_returns_result(self):
        # close_and_halt: 例外を送出せず、保有玉を stop_out 決済し BacktestResult を返す。
        interactor, req = _margin_call_setup(stop_out_action="close_and_halt")
        # Act
        result = interactor.execute(req)
        # Assert: 例外なし・保有玉が stop_out で確定する
        assert isinstance(result, BacktestResult)
        stop_out_trades = [t for t in result.trades if t.exit_reason == "stop_out"]
        assert len(stop_out_trades) == 1
        assert stop_out_trades[0].side == "buy"
        # byte-identical 担保（ISSUE-019）: 既定 entry/floating="close" では強制決済価格は
        # bar.close（=mark_price）。本是正後も従来どおり bar1.close=1.00 で不変。
        assert stop_out_trades[0].exit_price == pytest.approx(1.00)

    def test_close_and_halt_suppresses_new_orders_after_stop_out(self):
        # stop_out 後の新規発注シグナルは無視され、玉が増えない（halt セマンティクス）。
        buy = Order(side="buy", kind="market", volume=1.0, price=None)
        bars = [
            _bar(np.datetime64("2024-01-01T00:00"), 1.10, 1.10, 1.10, 1.10),
            _bar(np.datetime64("2024-01-01T00:01"), 1.10, 1.10, 1.00, 1.00),  # stop_out
            _bar(np.datetime64("2024-01-01T00:02"), 1.00, 1.10, 1.00, 1.05),  # 新規 buy 試行
        ]
        interactor, req = _margin_call_setup(
            stop_out_action="close_and_halt",
            orders_by_bar={0: [buy], 2: [buy]},
            bars=bars,
        )
        # Act
        result = interactor.execute(req)
        # Assert: 確定トレードは初回 buy の stop_out 決済 1 件のみ（bar2 の新規発注は抑止）
        assert len(result.trades) == 1
        assert result.trades[0].exit_reason == "stop_out"

    def test_stop_out_closes_at_close_mark_price_not_bar_open(self):
        # 回帰防止（ISSUE-019）: entry_price_basis="current_open" でも stop-out 強制決済は
        # 「margin 割れを判定した時点の現値」＝bar.close（account.mark_price）で行う。
        # 過ぎ去った始値（bar.open）で決済しない（実 MT5 整合・決済価格と判定価格の整合）。
        bars = [
            _bar(np.datetime64("2024-01-01T00:00"), 1.10, 1.10, 1.10, 1.10),
            # 急落バー: open=1.05・close=0.50。close で margin 割れ。
            _bar(np.datetime64("2024-01-01T00:01"), 1.05, 1.05, 0.50, 0.50),
        ]
        interactor, req = _margin_call_setup(
            stop_out_action="close_and_halt",
            entry_price_basis="current_open",
            bars=bars,
        )
        result = interactor.execute(req)
        stop_out = [t for t in result.trades if t.exit_reason == "stop_out"]
        assert len(stop_out) == 1
        # 決済価格は bar.close=0.50（mark price）。bar.open=1.05 ではない。
        assert stop_out[0].exit_price == pytest.approx(0.50)
        assert stop_out[0].exit_price != pytest.approx(1.05)

    def test_stop_out_sell_bid_ask_closes_at_ask_close_plus_spread(self):
        # 回帰防止（ISSUE-019 / レビュー🟡）: 売り保有 + floating_pnl_basis="bid_ask" では
        # 強制決済（買い戻し）= Ask = close + spread×point。判定価格（update_floating_pnl）
        # と決済価格（mark_price）が同一基準で整合することを値で固定する。
        sell = Order(side="sell", kind="market", volume=1.0, price=None)
        bars = [
            _bar(np.datetime64("2024-01-01T00:00"), 1.00, 1.00, 1.00, 1.00),
            # 価格上昇で売り保有が含み損→margin 割れ。spread=50pt（point=0.00001→0.0005）。
            Bar(time=np.datetime64("2024-01-01T00:01"), open=1.00, high=2.00, low=1.00,
                close=2.00, volume=1.0, spread=50),
        ]
        interactor, req = _margin_call_setup(
            stop_out_action="close_and_halt",
            floating_pnl_basis="bid_ask",
            orders_by_bar={0: [sell]},
            bars=bars,
        )
        result = interactor.execute(req)
        stop_out = [t for t in result.trades if t.exit_reason == "stop_out"]
        assert len(stop_out) == 1
        assert stop_out[0].side == "sell"
        # 決済 = Ask = close(2.00) + spread(50)×point(0.00001) = 2.0005。close 単独ではない。
        assert stop_out[0].exit_price == pytest.approx(2.0005)
        assert stop_out[0].exit_price != pytest.approx(2.00)

    def test_stop_out_at_open_closes_at_open_quote_on_gap(self):
        # 回帰防止（ISSUE-022）: stop_out_at_open=True かつ週末ギャップ等で「バー open」が
        # margin を割る場合、決済は close でなく「バー open クォート」で行う。
        # bar1 open=0.50（窓開け下落・即割れ）→ close=1.05（回復）。long は open で stop-out。
        bars = [
            _bar(np.datetime64("2024-01-01T00:00"), 1.10, 1.10, 1.10, 1.10),
            _bar(np.datetime64("2024-01-01T00:01"), 0.50, 1.10, 0.50, 1.05),  # gap-down open
        ]
        interactor, req = _margin_call_setup(
            stop_out_action="close_and_halt",
            entry_price_basis="current_open",
            stop_out_at_open=True,
            bars=bars,
        )
        result = interactor.execute(req)
        stop_out = [t for t in result.trades if t.exit_reason == "stop_out"]
        assert len(stop_out) == 1
        # 決済価格は bar open=0.50（割れた瞬間の現値）。close 1.05 ではない。
        assert stop_out[0].exit_price == pytest.approx(0.50)
        assert stop_out[0].exit_price != pytest.approx(1.05)

    def test_default_no_open_check_when_close_recovers(self):
        # 対照（既定 stop_out_at_open=False）: open が割れても close が回復していれば
        # close 基準判定のみのため stop-out は起きない（byte-identical 経路の不変性）。
        bars = [
            _bar(np.datetime64("2024-01-01T00:00"), 1.10, 1.10, 1.10, 1.10),
            _bar(np.datetime64("2024-01-01T00:01"), 0.50, 1.10, 0.50, 1.05),  # close 回復
        ]
        interactor, req = _margin_call_setup(
            stop_out_action="close_and_halt",
            entry_price_basis="current_open",
            bars=bars,  # stop_out_at_open 未指定＝既定 False
        )
        result = interactor.execute(req)
        # close=1.05 では margin 割れしない（floating=(1.05-1.10)*1e5=-5000・equity=5000>>必要）
        # → open 評価しないため stop-out 0 件。
        assert [t for t in result.trades if t.exit_reason == "stop_out"] == []

    def test_stop_out_at_open_fail_stop_raises(self):
        # 回帰防止（ISSUE-022・レビュー🟢）: stop_out_at_open=True かつ既定 fail_stop で
        # バー open が割れたら MarginCallError を送出する（close 経路と同型）。
        bars = [
            _bar(np.datetime64("2024-01-01T00:00"), 1.10, 1.10, 1.10, 1.10),
            _bar(np.datetime64("2024-01-01T00:01"), 0.50, 1.10, 0.50, 1.05),  # open 割れ
        ]
        interactor, req = _margin_call_setup(
            entry_price_basis="current_open",
            stop_out_at_open=True,
            bars=bars,  # stop_out_action 未指定＝既定 fail_stop
        )
        with pytest.raises(MarginCallError):
            interactor.execute(req)


# ---- account 伝播の後方互換: TC24051901 の確定トレード列が account の有無で不変 ----
#
# 背景（🟡-3）: develop では Interactor が on_new_bar に account=None を渡していた。
# 現在は実 Account を渡すため TC24051901 の同方向抑止（"buy" not in held_sides /
# "sell" not in held_sides）が有効化された。この有効化が**確定トレード列を変えない**
# ことを明示固定する回帰テスト。
#
# 不変が成立する構造的理由（クロス系の性質）: TC24051901 のエントリは madiff の
# ゼロクロス（買い: prev<0 and curr>0／売り: prev>0 and curr<0）のみ。保有中に
# 同方向シグナルが再発火するには madiff が一度ゼロを跨いで反対符号になる必要があり、
# その跨ぎ自体が反対方向クロス＝Interactor の reverse 決済を発火させ保有方向を反転
# させる。ゆえに「同方向を保有したまま同方向シグナル」は通常実行では到達不能で、
# held_sides 抑止は事実上未発火 → トレード列は account の有無に依らず不変。
# 本テストは複数クロスの合成 madiff で end-to-end に同一トレード列を assert し、
# 将来 account 伝播や抑止条件が変わってトレード列が動くことを退行として検出する。


class _NullAccountStrategy:
    """委譲先 strategy へ account=None を渡すラッパ（develop 相当の挙動を再現）。

    Interactor は実 Account を on_new_bar に渡すが、本ラッパは account を None に
    差し替えて委譲することで「account 伝播なし（同方向抑止 OFF）」を再現する。
    open_positions は持たない（duck typing の held_sides が空集合になる）。
    """

    def __init__(self, inner):
        self._inner = inner

    def on_init(self, config, indicators):
        self._inner.on_init(config, indicators)

    def on_new_bar(self, bar_index, indicators, account):
        return self._inner.on_new_bar(bar_index, indicators, None)

    def on_position_check(self, position, bar_index, indicators):
        return self._inner.on_position_check(position, bar_index, indicators)


class _RunConfigLike:
    """determinism 属性アクセス + 戦略パラメータの subscript を 1 つで満たす config。

    Interactor は config.entry_price_basis 等を属性で、TC24051901 は cfg["point_size"]
    等を subscript で参照する（main/run_config.RunConfig と同契約）。
    """

    def __init__(self, base_config, params):
        self._base = base_config
        self._params = params

    def __getattr__(self, name):
        return getattr(self._base, name)

    def __getitem__(self, key):
        return self._params[key]


def _tc_invariance_setup(strategy):
    """複数クロスを含む合成 madiff で TC24051901 を Interactor 駆動する共通セットアップ。

    SL/TP は十分広く（建玉が SL/TP で早期決済しない）、価格は微小変動に留める。
    madiff はゼロクロスを複数回起こし、buy/sell の往復（reverse 決済）を生成する。
    """
    import pandas as pd

    from simulator.adapter.indicator.registry import PandasIndicatorRegistry

    # 複数のゼロクロス（buy→sell→buy→sell ...）を起こす madiff 系列。
    madiff = [-1.0, 0.5, 0.8, -0.6, -0.3, 0.7, 0.9, -0.4, 0.6, -0.5]
    close = [100.0 + 0.01 * i for i in range(len(madiff))]
    bars = [
        _bar(
            np.datetime64("2024-01-01T00:00") + np.timedelta64(i, "m"),
            c, c + 0.5, c - 0.5, c,
        )
        for i, c in enumerate(close)
    ]
    base = BacktestConfig(
        tick_model="ohlc_simulate", spread_model="fixed", sltp_tie="sl",
        fill_delay="next_tick", ohlc_order="auto", session_calendar="none",
        digits=5, legacy_quirks=False, return_basis="equity",
    )
    params = {
        "lot_size": 0.1, "stop_loss_points": 100_000,
        "take_profit_points": 100_000, "point_size": 0.0001,
    }
    spec = SymbolSpec(
        contract_size=1.0, volume_min=0.01, volume_max=100.0, volume_step=0.01,
        stops_level=0, digits=5, point_size=0.0001, leverage=100.0,
    )
    registry = PandasIndicatorRegistry(
        {"madiff": pd.Series(madiff), "close": pd.Series(close)}
    )
    interactor = RunBacktestInteractor(
        strategy=strategy, indicators=registry, tick_model=StubTickModelPort(),
    )
    req = RunBacktestRequest(
        config=_RunConfigLike(base, params), bars=bars, symbol_spec=spec,
        initial_deposit=100_000.0, stop_out_level=0.0,
    )
    return interactor, req


def _trade_signature(result):
    """確定トレード列を比較可能なタプル列へ正規化する（side/時刻/価格/決済理由）。"""
    return [
        (
            t.side, t.entry_time, t.exit_time,
            t.entry_price, t.exit_price, t.exit_reason,
        )
        for t in result.trades
    ]


class TestAccountPropagationTradeInvariance:
    def test_tc24051901_trades_unchanged_between_none_and_real_account(self):
        # Arrange: 同一の複数クロス合成データを、(A) account=None 相当（develop・
        #   同方向抑止 OFF）と (B) 実 Account（現在・抑止 ON）の 2 経路で実走する。
        from simulator.adapter.strategy.tc24051901 import TC24051901

        none_interactor, none_req = _tc_invariance_setup(
            _NullAccountStrategy(TC24051901())
        )
        real_interactor, real_req = _tc_invariance_setup(TC24051901())

        # Act
        none_result = none_interactor.execute(none_req)
        real_result = real_interactor.execute(real_req)

        # Assert: 複数クロスで往復トレードが現に生成される（テストが空でない＝非自明）
        assert len(real_result.trades) > 0
        # 同方向抑止の有効化は確定トレード列を一切変えない（account 伝播の後方互換）。
        # クロス系では held_sides 抑止が未発火のため side/時刻/価格/決済理由が完全一致する。
        assert _trade_signature(none_result) == _trade_signature(real_result)


# ---- warmup/trading_start: ウォームアップ期間のバーは指標 update のみ実施し
#      トレード/equity_curve/stats から除外する（config-gated・既定 None=全バー取引） ----

class TestTradingStartWarmupExclusion:
    """RunBacktestRequest.trading_start により bar.time < trading_start のバーを
    「指標 update のみ・トレード生成なし・equity_curve/stats 除外」とする振る舞いを固定。

    既定（trading_start=None）は全バー取引＝後方互換（別テストで固定）。
    """

    @staticmethod
    def _warmup_bars():
        # bar0/bar1 = warmup（trading_start 前）、bar2/bar3 = trading 期間。
        return [
            _bar(np.datetime64("2024-12-31T23:58"), 1.10, 1.11, 1.09, 1.10),
            _bar(np.datetime64("2024-12-31T23:59"), 1.10, 1.11, 1.09, 1.10),
            _bar(np.datetime64("2025-01-01T00:00"), 1.10, 1.13, 1.10, 1.12),
            _bar(np.datetime64("2025-01-01T00:01"), 1.12, 1.14, 1.11, 1.13),
        ]

    def test_warmup_bars_call_indicator_update_but_not_strategy_signal(self):
        # Arrange: 全バーで warmup のうち bar0/bar1 は trading_start 前。
        log = []
        bars = self._warmup_bars()
        interactor = RunBacktestInteractor(
            strategy=SpyStrategyPort(log),
            indicators=SpyIndicatorPort(log),
            tick_model=StubTickModelPort(),
        )
        req = RunBacktestRequest(
            config=_config(), bars=bars, symbol_spec=_symbol_spec(),
            initial_deposit=10_000.0, stop_out_level=0.0,
            trading_start=np.datetime64("2025-01-01T00:00"),
        )
        # Act
        interactor.execute(req)
        # Assert: warmup バー(0,1)では indicator.update のみ・on_new_bar は呼ばない。
        #   trading バー(2,3)では update→on_new_bar の両方が呼ばれる。
        assert ("indicator.update", 0) in log
        assert ("indicator.update", 1) in log
        assert ("strategy.on_new_bar", 0) not in log
        assert ("strategy.on_new_bar", 1) not in log
        assert ("strategy.on_new_bar", 2) in log
        assert ("strategy.on_new_bar", 3) in log

    def test_warmup_orders_not_filled_and_equity_excludes_warmup_bars(self):
        # Arrange: bar0(warmup)に買い注文・bar2(trading)に買い→bar3で反対売り(reverse決済)。
        # warmup の注文は約定せず、equity_curve は trading バー分のみ（=2件）。
        buy = Order(side="buy", kind="market", volume=1.0, price=None)
        sell = Order(side="sell", kind="market", volume=1.0, price=None)
        bars = self._warmup_bars()
        strategy = SpyStrategyPort([], orders_by_bar={0: [buy], 2: [buy], 3: [sell]})
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort([]),
            tick_model=StubTickModelPort(),
        )
        req = RunBacktestRequest(
            config=_config(), bars=bars, symbol_spec=_symbol_spec(),
            initial_deposit=10_000.0, stop_out_level=0.0,
            trading_start=np.datetime64("2025-01-01T00:00"),
        )
        # Act
        result = interactor.execute(req)
        # Assert: 確定トレードは trading 期間の 1 件のみ（warmup の buy は約定しない）。
        assert len(result.trades) == 1
        assert result.trades[0].entry_time == bars[2].time
        # equity_curve は trading バー(2,3)の 2 件のみ（warmup の 2 バーは除外）。
        assert len(result.equity_curve) == 2

    def test_default_none_trading_start_keeps_all_bars_trading(self):
        # 後方互換: trading_start=None（既定）では全バーで on_new_bar が呼ばれ、
        # equity_curve は全バー分記録される（warmup 除外なし）。
        log = []
        bars = self._warmup_bars()
        interactor = RunBacktestInteractor(
            strategy=SpyStrategyPort(log),
            indicators=SpyIndicatorPort(log),
            tick_model=StubTickModelPort(),
        )
        # trading_start を指定しない（既定 None）。
        result = interactor.execute(_request(bars))
        # Assert: 全 4 バーで on_new_bar が呼ばれ equity_curve も 4 件。
        for i in range(4):
            assert ("strategy.on_new_bar", i) in log
        assert len(result.equity_curve) == 4


# ---- 層1: prime_first_trading_bar — trading_start 境界の最初の 1 バーを
#      「アタッチ/プライム」として扱い指標 update のみ実施しトレード/equity から除外する
#      （config-gated・既定 False=従来どおり trading_start 境界バーも取引対象） ----

class TestPrimeFirstTradingBar:
    """config.prime_first_trading_bar=True のとき、trading_start 境界（bar.time >=
    trading_start となる最初の 1 バー）を warmup 同様「指標 update のみ・発注/equity 除外」
    として扱い、初回約定を次バーに落とす。

    背景: 実 MT5 はテスト開始バーをアタッチ/プライムとして扱い初回約定が次足に落ちる。
    既定 False は trading_start 境界バーも取引対象＝従来不変（別テストで固定）。
    """

    @staticmethod
    def _bars():
        # bar0/bar1 = warmup（trading_start 前）、bar2 = 境界バー（prime 対象）、bar3 = 取引。
        return [
            _bar(np.datetime64("2024-12-31T23:58"), 1.10, 1.11, 1.09, 1.10),
            _bar(np.datetime64("2024-12-31T23:59"), 1.10, 1.11, 1.09, 1.10),
            _bar(np.datetime64("2025-01-01T00:00"), 1.10, 1.13, 1.10, 1.12),
            _bar(np.datetime64("2025-01-01T00:01"), 1.12, 1.14, 1.11, 1.13),
        ]

    def _config_primed(self):
        c = _config()
        c.prime_first_trading_bar = True
        return c

    def test_prime_boundary_bar_calls_update_only_not_signal(self):
        # Arrange: prime_first_trading_bar=True・trading_start=00:00（bar2 が境界）。
        log = []
        bars = self._bars()
        interactor = RunBacktestInteractor(
            strategy=SpyStrategyPort(log),
            indicators=SpyIndicatorPort(log),
            tick_model=StubTickModelPort(),
        )
        req = RunBacktestRequest(
            config=self._config_primed(), bars=bars, symbol_spec=_symbol_spec(),
            initial_deposit=10_000.0, stop_out_level=0.0,
            trading_start=np.datetime64("2025-01-01T00:00"),
        )
        # Act
        interactor.execute(req)
        # Assert: 境界バー(2)では indicator.update のみ・on_new_bar は呼ばない
        #   （warmup の 0,1 も従来どおり除外。取引バーは 3 のみ）。
        assert ("indicator.update", 2) in log
        assert ("strategy.on_new_bar", 2) not in log
        assert ("strategy.on_new_bar", 3) in log

    def test_prime_excludes_boundary_bar_order_and_equity(self):
        # Arrange: 境界バー(2)に買い注文・取引バー(3)に買い→次バー無しで保有のまま。
        # prime のため境界バーの注文は約定せず、equity_curve は取引バー(3)の 1 件のみ。
        buy = Order(side="buy", kind="market", volume=1.0, price=None)
        bars = self._bars()
        strategy = SpyStrategyPort([], orders_by_bar={2: [buy], 3: [buy]})
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort([]),
            tick_model=StubTickModelPort(),
        )
        req = RunBacktestRequest(
            config=self._config_primed(), bars=bars, symbol_spec=_symbol_spec(),
            initial_deposit=10_000.0, stop_out_level=0.0,
            trading_start=np.datetime64("2025-01-01T00:00"),
        )
        # Act
        result = interactor.execute(req)
        # Assert: 境界バー(2)・取引バー(3)とも買いのみで反対決済が無いため確定トレード 0 件
        #   （境界バーの発注が約定していれば次バーで何も起きず保有継続＝確定 0 は両解釈で成立）。
        #   本テストの主眼は equity_curve から境界バーが除外されること。
        assert len(result.trades) == 0
        # equity_curve は取引バー(3)の 1 件のみ（warmup 2 件 + 境界 1 件は除外）。
        assert len(result.equity_curve) == 1

    def test_default_false_keeps_boundary_bar_trading(self):
        # 後方互換: prime_first_trading_bar 既定 False では trading_start 境界バー(2)も
        # 取引対象＝従来どおり on_new_bar が呼ばれ equity も記録される。
        log = []
        bars = self._bars()
        interactor = RunBacktestInteractor(
            strategy=SpyStrategyPort(log),
            indicators=SpyIndicatorPort(log),
            tick_model=StubTickModelPort(),
        )
        req = RunBacktestRequest(
            config=_config(), bars=bars, symbol_spec=_symbol_spec(),
            initial_deposit=10_000.0, stop_out_level=0.0,
            trading_start=np.datetime64("2025-01-01T00:00"),
        )
        # Act
        result = interactor.execute(req)
        # Assert: 境界バー(2)でも on_new_bar が呼ばれ equity は取引バー(2,3)の 2 件。
        assert ("strategy.on_new_bar", 2) in log
        assert len(result.equity_curve) == 2
