"""UC-001 RunBacktestInteractor every-tick 経路の単体テスト（every-tick #5/#6）。

config.tick_model == "real_ticks" のときのみ起動する every-tick 経路を、
小さな手組みティック列（(price, bid, ask, time) の list）を返すスタブ
TickModelPort で駆動する。実 parquet（marketdata/ticks/*.parquet）は読まない。

検証観点（Red→Green・構造/自己整合）:
    * on_new_bar は「足境界」でのみ評価され、ティックごとに呼ばれない
    * 新規バーの成行約定は「バー open クォート」（買い=open+spread×point / 売り=open）で
      起きる（bar.close でもティック価格でもない＝bar-mode と同一・実 MT5 突合）
    * SL/TP は到達ティック価格で決済される（ここがティック駆動の固有挙動）
    * stop-out が「ティック」で発火する（バー中間ティックで margin 割れ）
    * 縮退（1 バー 1 ティック・price=close・bid==ask==close）で bar-mode と整合
    * 既定 config（real_ticks 以外）では every-tick 経路に入らない

usecase は domain のみ依存。
"""
from __future__ import annotations

import numpy as np
import pytest

from backtest.domain.bar import Bar
from backtest.domain.exceptions import MarginCallError
from backtest.domain.order import Order
from backtest.usecase.models import BacktestConfig, SymbolSpec
from backtest.usecase.run_backtest import RunBacktestInteractor, RunBacktestRequest


# ---- テスト用スタブ/スパイ Port ----

class SpyIndicatorPort:
    def __init__(self, log=None):
        self._log = log if log is not None else []

    def get(self, name):
        return None

    def update(self, bar_index):
        self._log.append(("indicator.update", bar_index))


class SpyStrategyPort:
    """on_new_bar が orders_by_bar[bar_index] を返し、呼出を記録するスパイ。"""

    def __init__(self, orders_by_bar=None):
        self._orders_by_bar = orders_by_bar or {}
        self.on_new_bar_calls = []  # [bar_index]

    def on_init(self, config, indicators):
        pass

    def on_new_bar(self, bar_index, indicators, account):
        self.on_new_bar_calls.append(bar_index)
        return list(self._orders_by_bar.get(bar_index, []))

    def on_position_check(self, position, bar_index, indicators):
        return "hold"


class ListTickModel:
    """bar_index → ティック列（(price, bid, ask, time) の list）を返すスタブ。

    ticks_by_bar が dict のときは bar.time をキーに引く（None は空列）。
    """

    def __init__(self, ticks_by_bar):
        self._ticks_by_bar = ticks_by_bar
        self.ticks_of_calls = []

    def ticks_of(self, bar, prev_close):
        self.ticks_of_calls.append(bar.time)
        return list(self._ticks_by_bar.get(bar.time, []))


def _bar(t, o, h, l, c, *, spread=0):
    return Bar(time=t, open=o, high=h, low=l, close=c, volume=1.0, spread=spread)


def _config(tick_model="real_ticks", **overrides):
    base = dict(
        tick_model=tick_model,
        spread_model="fixed",
        sltp_tie="sl",
        fill_delay="next_tick",
        ohlc_order="auto",
        session_calendar="none",
        digits=5,
        legacy_quirks=False,
        return_basis="equity",
    )
    base.update(overrides)
    return BacktestConfig(**base)


def _spec(**overrides):
    base = dict(
        contract_size=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=0,
        digits=5,
        point_size=0.00001,
        leverage=100.0,
    )
    base.update(overrides)
    return SymbolSpec(**base)


def _request(bars, *, config=None, initial_deposit=10_000.0, stop_out_level=0.0,
             symbol_spec=None):
    return RunBacktestRequest(
        config=config or _config(),
        bars=bars,
        symbol_spec=symbol_spec or _spec(),
        initial_deposit=initial_deposit,
        stop_out_level=stop_out_level,
    )


T0 = np.datetime64("2024-01-01T00:00")
T1 = np.datetime64("2024-01-01T00:01")


# ---- ET-2: 成行約定はバー open クォートで起きる（close でもティック価格でもない） ----

class TestFillAtBarOpenQuote:
    def test_buy_fills_at_bar_open_ask_not_tick_or_close(self):
        # Arrange: bar0 open=1.10・spread=300pts（point=0.00001）→ バー open Ask=
        # 1.10+300×0.00001=1.103。close=1.10、最初のティック ask=1.123。
        # 実 MT5 every-tick は新規バー成行をバー open クォート（買い=open+spread×point）で
        # 約定する＝entry は 1.103（close 1.10 でもティック ask 1.123 でもない）。
        # bar1 で反対シグナル → reverse 決済で確定トレードを生成し entry_price を観測。
        bars = [
            _bar(T0, 1.10, 1.20, 1.05, 1.10, spread=300),
            _bar(T1, 1.10, 1.20, 1.05, 1.11),
        ]
        ticks = {
            T0: [(1.122, 1.121, 1.123, T0)],  # 罠: ティック ask=1.123（建値ではない）
            T1: [(1.150, 1.149, 1.151, T1)],
        }
        buy = Order(side="buy", kind="market", volume=1.0, price=None)
        sell = Order(side="sell", kind="market", volume=1.0, price=None)
        strategy = SpyStrategyPort(orders_by_bar={0: [buy], 1: [sell]})
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort(),
            tick_model=ListTickModel(ticks),
        )
        # Act: entry_price_basis="current_open" でバー open クォート約定
        result = interactor.execute(
            _request(bars, config=_config(entry_price_basis="current_open"))
        )
        # Assert: entry はバー open Ask（1.103）。close 1.10 でもティック ask 1.123 でもない。
        assert len(result.trades) == 1
        assert result.trades[0].side == "buy"
        assert result.trades[0].entry_price == pytest.approx(1.103)
        assert result.trades[0].entry_price != pytest.approx(1.10)
        assert result.trades[0].entry_price != pytest.approx(1.123)

    def test_real_ticks_default_basis_fills_at_close_like_bar_mode(self):
        # 回帰防止（レビュー🟡-1）: real_ticks でも entry_price_basis 既定="close" の
        # ときは bar.close 約定になる（bar-mode と同一・derive_quotes の close 分岐）。
        # open=1.10・close=1.105・spread=300 で close≠open を作り、約定が close=1.105
        # （open 1.10 でも罠ティック ask 1.123 でもない）であることを値で固定する。
        bars = [
            _bar(T0, 1.10, 1.20, 1.05, 1.105, spread=300),
            _bar(T1, 1.10, 1.20, 1.05, 1.11),
        ]
        ticks = {
            T0: [(1.122, 1.121, 1.123, T0)],  # 罠: ティック ask=1.123
            T1: [(1.150, 1.149, 1.151, T1)],
        }
        buy = Order(side="buy", kind="market", volume=1.0, price=None)
        sell = Order(side="sell", kind="market", volume=1.0, price=None)
        strategy = SpyStrategyPort(orders_by_bar={0: [buy], 1: [sell]})
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort(),
            tick_model=ListTickModel(ticks),
        )
        # Act: _config() 既定（tick_model="real_ticks" / entry_price_basis="close"）
        result = interactor.execute(_request(bars))
        # Assert: entry は bar0.close=1.105（open 1.10 でもティック ask 1.123 でもない）
        assert len(result.trades) == 1
        assert result.trades[0].entry_price == pytest.approx(1.105)
        assert result.trades[0].entry_price != pytest.approx(1.10)
        assert result.trades[0].entry_price != pytest.approx(1.123)


# ---- ET-1: on_new_bar は足境界でのみ評価される（ティックごとに呼ばない） ----

class TestOnNewBarOnlyAtBarBoundary:
    def test_on_new_bar_called_once_per_bar_regardless_of_tick_count(self):
        # Arrange: bar0 に 3 ティック・bar1 に 2 ティックを与えるが、on_new_bar は
        # 各 bar で 1 回だけ呼ばれる（ティック内側ループでは呼ばない）。
        bars = [
            _bar(T0, 1.10, 1.12, 1.08, 1.10),
            _bar(T1, 1.10, 1.13, 1.09, 1.11),
        ]
        ticks = {
            T0: [(1.10, 1.10, 1.10, T0), (1.11, 1.11, 1.11, T0), (1.10, 1.10, 1.10, T0)],
            T1: [(1.11, 1.11, 1.11, T1), (1.12, 1.12, 1.12, T1)],
        }
        strategy = SpyStrategyPort()
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort(),
            tick_model=ListTickModel(ticks),
        )
        # Act
        interactor.execute(_request(bars))
        # Assert: on_new_bar は bar ごとに 1 回（合計 2 回・ティック数に依存しない）
        assert strategy.on_new_bar_calls == [0, 1]

    def test_signal_orders_fill_at_bar_open_once_not_per_tick(self):
        # ---- distinguishing 強化（弱い Red 是正・AP.1 R-7）----
        # 「on_new_bar は足境界で 1 回だけ評価され、その orders は当該足の【バー open
        #  クォート】で 1 回だけ約定する。ティックごとに on_new_bar を再評価して各ティック
        #  価格で約定し直すことはしない」ことを、トレードの【値】で区別する。
        #
        # 区別の仕組み（every-tick 経路でしか成立しない distinguishing アサーション）:
        #   - bar0 open=1.10・spread=300pts → 買いはバー open Ask=1.103 で約定
        #     （close=1.10 でもどのティック ask でもない）。
        #   - bar1 open=1.13・spread=0 → 反対（売り）の reverse はバー open（long 決済=
        #     Bid=open=1.13）で決済（close=1.11 でもどのティック bid でもない）。
        #   - もし実装がティックごとに約定し直すなら entry/exit がティック価格に変わる。
        #     バー open クォートで 1 回のみ約定することを確定トレードの値で固定する。
        bars = [
            _bar(T0, 1.10, 1.20, 1.05, 1.10, spread=300),
            _bar(T1, 1.13, 1.20, 1.05, 1.11),
        ]
        ticks = {
            # bar0: どのティック ask もバー open Ask=1.103 と異なる（ティック約定なら値が変わる罠）。
            T0: [
                (1.130, 1.129, 1.131, T0),
                (1.160, 1.159, 1.161, T0),
                (1.170, 1.169, 1.171, T0),
            ],
            # bar1: どのティック bid もバー open=1.13 と異なる（罠）。
            T1: [(1.141, 1.140, 1.142, T1), (1.180, 1.179, 1.181, T1)],
        }
        buy = Order(side="buy", kind="market", volume=1.0, price=None)
        sell = Order(side="sell", kind="market", volume=1.0, price=None)
        strategy = SpyStrategyPort(orders_by_bar={0: [buy], 1: [sell]})
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort(),
            tick_model=ListTickModel(ticks),
        )
        # Act
        result = interactor.execute(
            _request(bars, config=_config(entry_price_basis="current_open"))
        )
        # Assert: on_new_bar は足境界で各 1 回（ティック数に依存しない）
        assert strategy.on_new_bar_calls == [0, 1]
        # かつ約定は「バー open クォート」でのみ起きる（ティックごとの再約定をしない）:
        #   確定トレードはちょうど 1 件・買い。
        assert len(result.trades) == 1
        assert result.trades[0].side == "buy"
        # entry は bar0 バー open Ask=1.103（close 1.10 でもティック ask でもない）
        assert result.trades[0].entry_price == pytest.approx(1.103)
        assert result.trades[0].entry_price != pytest.approx(1.10)
        assert result.trades[0].entry_price != pytest.approx(1.131)
        # exit は bar1 バー open=Bid=1.13（close 1.11 でもティック bid でもない）
        assert result.trades[0].exit_reason == "reverse"
        assert result.trades[0].exit_price == pytest.approx(1.13)
        assert result.trades[0].exit_price != pytest.approx(1.11)
        assert result.trades[0].exit_price != pytest.approx(1.140)


# ---- ET-3: SL/TP は到達ティック価格で決済される ----

class TestSlTpClosesAtTickPrice:
    def test_sl_closes_on_mid_bar_tick_at_sl_price(self):
        # ---- distinguishing 強化（弱い Red 是正・AP.1 R-7）----
        # 「SL は到達ティック価格で判定・決済される」ことを、bar-mode（bar.low/high で
        #  判定）では【決済が起きない／値が変わる】構成で区別する。
        #
        # 区別の仕組み（every-tick 経路でしか成立しない distinguishing 構成）:
        #   - SL=1.095。bar1 の OHLC は low=1.100 で、SL=1.095 に【一切到達しない】
        #     （bar-mode の check_sltp_hit は low<=sl=1.100<=1.095=False → SL 非ヒット）。
        #   - 一方 bar1 の 2 番目ティック price=1.094 は SL=1.095 を貫く（every-tick の
        #     check_sltp_hit_at_tick(price=1.094) は 1.094<=1.095=True → SL ヒット）。
        #   - よって every-tick 経路でのみ SL 決済が成立する。bar-mode fallback では
        #     bar1 で決済されず（trades 件数・exit_reason が変わり）必ず落ちる。
        #   - entry も bar0 バー open Ask=1.101（open=1.10+spread100×point0.00001、close=1.10
        #     でもティック ask 1.108 でもない）で固定し、経路を二重に区別する。
        bars = [
            _bar(T0, 1.10, 1.12, 1.10, 1.10, spread=100),
            # bar1.low=1.100 は SL=1.095 に届かない（bar-mode では SL 非ヒット）。
            _bar(T1, 1.10, 1.12, 1.10, 1.11),
        ]
        ticks = {
            # entry はバー open Ask=1.101。ティック ask=1.108 は罠（建値ではない）。
            T0: [(1.100, 1.099, 1.108, T0)],
            # 1 番目は SL 未到達、2 番目 price=1.094 が SL=1.095 を貫く（ティックのみ到達）。
            T1: [(1.110, 1.110, 1.110, T1), (1.094, 1.094, 1.094, T1)],
        }
        order = Order(side="buy", kind="market", volume=1.0, price=None,
                      sl=1.095, tp=1.300)
        strategy = SpyStrategyPort(orders_by_bar={0: [order]})
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort(),
            tick_model=ListTickModel(ticks),
        )
        # Act
        result = interactor.execute(
            _request(bars, config=_config(entry_price_basis="current_open"))
        )
        # Assert: ティック価格でのみ成立する SL 決済（bar.low では到達しない）
        assert len(result.trades) == 1
        # entry は bar0 バー open Ask=1.101（close 1.10 でもティック ask 1.108 でもない）
        assert result.trades[0].entry_price == pytest.approx(1.101)
        assert result.trades[0].entry_price != pytest.approx(1.10)
        assert result.trades[0].entry_price != pytest.approx(1.108)
        # SL 決済・exit_price は SL(1.095)・exit は bar1（bar.low 1.100 では非ヒット）
        assert result.trades[0].exit_reason == "sl"
        assert result.trades[0].exit_price == pytest.approx(1.095)
        assert result.trades[0].exit_time == bars[1].time

    def test_position_opened_on_bar_not_sltp_checked_same_bar_ticks(self):
        # Arrange: fill_delay=next_tick。bar0 の建玉足ティックでは SL/TP 監視しない。
        # bar0 に SL 貫通ティック(1.04)を置いても bar0 では決済されず、bar1 で初めて決済。
        bars = [
            _bar(T0, 1.10, 1.12, 1.00, 1.10),
            _bar(T1, 1.10, 1.12, 1.00, 1.10),
        ]
        ticks = {
            T0: [(1.10, 1.10, 1.10, T0), (1.04, 1.04, 1.04, T0)],  # 建玉足: 監視外
            T1: [(1.04, 1.04, 1.04, T1)],                          # 次足: SL ヒット
        }
        order = Order(side="buy", kind="market", volume=1.0, price=None,
                      sl=1.095, tp=1.300)
        strategy = SpyStrategyPort(orders_by_bar={0: [order]})
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort(),
            tick_model=ListTickModel(ticks),
        )
        # Act
        result = interactor.execute(_request(bars))
        # Assert: 決済は bar1（建玉足 bar0 では監視しない）
        assert len(result.trades) == 1
        assert result.trades[0].exit_reason == "sl"
        assert result.trades[0].exit_time == bars[1].time
        assert result.trades[0].entry_time == bars[0].time


# ---- ET-4: stop-out がティックで発火する（バー中間ティックで margin 割れ） ----

class TestStopOutFiresAtTick:
    def test_margin_call_raises_at_mid_bar_tick(self):
        # Arrange: 1 lot 買い@1.10、contract=100000、leverage=100 → 必要証拠金=1100。
        # bar1 の 2 番目ティック price/bid=1.00 で含み損 -10000 → equity≈0 →
        # margin_level≈0% < 50% でそのティックで MarginCallError。
        bars = [
            _bar(T0, 1.10, 1.10, 1.10, 1.10),
            _bar(T1, 1.10, 1.10, 1.00, 1.10),
        ]
        ticks = {
            T0: [(1.10, 1.10, 1.10, T0)],
            T1: [(1.10, 1.10, 1.10, T1), (1.00, 1.00, 1.00, T1)],  # 2 番目で margin 割れ
        }
        spec = _spec(contract_size=100_000.0, leverage=100.0)
        order = Order(side="buy", kind="market", volume=1.0, price=None)
        strategy = SpyStrategyPort(orders_by_bar={0: [order]})
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort(),
            tick_model=ListTickModel(ticks),
        )
        req = _request(bars, initial_deposit=10_000.0, stop_out_level=50.0,
                       symbol_spec=spec)
        # Act / Assert: ティックで stop-out 発火 → MarginCallError（fail_stop 既定）
        with pytest.raises(MarginCallError):
            interactor.execute(req)

    def test_close_and_halt_closes_at_tick_and_completes(self):
        # Arrange: stop_out_action="close_and_halt" でティック発火時に強制決済・完走。
        bars = [
            _bar(T0, 1.10, 1.10, 1.10, 1.10),
            _bar(T1, 1.10, 1.10, 1.00, 1.10),
        ]
        ticks = {
            T0: [(1.10, 1.10, 1.10, T0)],
            T1: [(1.00, 1.00, 1.00, T1)],
        }
        spec = _spec(contract_size=100_000.0, leverage=100.0)
        config = _config(stop_out_action="close_and_halt")
        order = Order(side="buy", kind="market", volume=1.0, price=None)
        strategy = SpyStrategyPort(orders_by_bar={0: [order]})
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort(),
            tick_model=ListTickModel(ticks),
        )
        req = _request(bars, config=config, initial_deposit=10_000.0,
                       stop_out_level=50.0, symbol_spec=spec)
        # Act
        result = interactor.execute(req)
        # Assert: 強制決済され（stop_out reason）完走し result を返す
        assert len(result.trades) == 1
        assert result.trades[0].exit_reason == "stop_out"


# ---- ET-5: 縮退（1 バー 1 ティック・price=close・bid==ask==close）で bar-mode と整合 ----

class TestDegenerateMatchesBarMode:
    """1 バー 1 ティック・price=bid=ask=close で every-tick が bar-mode と一致する。

    spread=0・各バー 1 ティック（close 相当）に縮退させると、約定価格（close）・
    決済価格（close）・含み損評価（close）が bar 経路と一致するはずである。
    SL/TP 決済価格は SL/TP 値で同一のため、確定トレード列が一致する。
    """

    def _scenario_bars(self):
        return [
            _bar(T0, 1.10, 1.11, 1.09, 1.10),
            _bar(T1, 1.10, 1.13, 1.08, 1.12),
            _bar(np.datetime64("2024-01-01T00:02"), 1.12, 1.14, 1.11, 1.13),
        ]

    def test_degenerate_every_tick_matches_bar_mode_trades(self):
        from backtest.usecase.run_backtest import RunBacktestInteractor as RB

        bars = self._scenario_bars()
        # bar0 買い建て、bar2 反対（売り）シグナルで reverse 決済。
        buy = Order(side="buy", kind="market", volume=1.0, price=None)
        sell = Order(side="sell", kind="market", volume=1.0, price=None)
        orders = {0: [buy], 2: [sell]}

        # --- bar-mode（基準・既定 config） ---
        bar_strategy = SpyStrategyPort(orders_by_bar=orders)

        class _StubBarTick:
            def ticks_of(self, bar, prev_close):
                return [(bar.close, bar.close, bar.close, bar.time)]

        bar_interactor = RB(
            strategy=bar_strategy,
            indicators=SpyIndicatorPort(),
            tick_model=_StubBarTick(),
        )
        bar_result = bar_interactor.execute(
            _request(bars, config=_config(tick_model="ohlc_expand"))
        )

        # --- every-tick（1 バー 1 ティック=close・spread0） ---
        et_ticks = {b.time: [(b.close, b.close, b.close, b.time)] for b in bars}
        et_strategy = SpyStrategyPort(orders_by_bar=orders)
        et_interactor = RB(
            strategy=et_strategy,
            indicators=SpyIndicatorPort(),
            tick_model=ListTickModel(et_ticks),
        )
        et_result = et_interactor.execute(
            _request(bars, config=_config(tick_model="real_ticks"))
        )

        # Assert: 確定トレードが一致（side/entry/exit/価格/理由）
        assert len(et_result.trades) == len(bar_result.trades) == 1
        bt, et = bar_result.trades[0], et_result.trades[0]
        assert et.side == bt.side
        assert et.entry_price == pytest.approx(bt.entry_price)
        assert et.exit_price == pytest.approx(bt.exit_price)
        assert et.exit_reason == bt.exit_reason
        assert et.entry_time == bt.entry_time
        assert et.exit_time == bt.exit_time
        # stats の確定損益も一致
        assert et_result.stats.profit == pytest.approx(bar_result.stats.profit)


# ---- ET-6: 既定 config（real_ticks 以外）では every-tick 経路に入らない ----

class TestDefaultConfigDoesNotEnterEveryTick:
    def test_default_tick_model_uses_bar_path_filling_at_close(self):
        # Arrange: tick_model="ohlc_expand"（既定相当）。every-tick 経路に入らないため
        # 約定は bar-mode の close 約定（buy=ask=close）になり、ティックの bid/ask を
        # 渡しても無視される（ListTickModel は呼ばれてもよいが価格は close）。
        bars = [
            _bar(T0, 1.10, 1.20, 1.05, 1.10),
            _bar(T1, 1.10, 1.20, 1.05, 1.11),
        ]
        # every-tick 経路が誤って起動したら entry=1.123 になる罠ティック。
        ticks = {T0: [(1.122, 1.121, 1.123, T0)], T1: [(1.150, 1.149, 1.151, T1)]}
        buy = Order(side="buy", kind="market", volume=1.0, price=None)
        sell = Order(side="sell", kind="market", volume=1.0, price=None)
        strategy = SpyStrategyPort(orders_by_bar={0: [buy], 1: [sell]})
        interactor = RunBacktestInteractor(
            strategy=strategy,
            indicators=SpyIndicatorPort(),
            tick_model=ListTickModel(ticks),
        )
        # Act: 既定（real_ticks 以外）→ bar 経路で close 約定
        result = interactor.execute(
            _request(bars, config=_config(tick_model="ohlc_expand"))
        )
        # Assert: entry は close(1.10)（every-tick の ask 1.123 ではない＝経路に入っていない）
        assert len(result.trades) == 1
        assert result.trades[0].entry_price == pytest.approx(1.10)
