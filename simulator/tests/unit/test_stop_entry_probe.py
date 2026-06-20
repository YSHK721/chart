"""StopEntryProbe 戦略 + engine OCO/持続再アームの単体テスト（260620-2604-02・ISSUE-024）。

検証観点:
    * 戦略: on_new_bar は常に [] / on_tick が当該ティッククォートで両建て逆指値 2 件を装填。
      価格・SL/TP は原典式（Buy=Ask+offset・Sell=Bid−offset / SL/TP はペンディング価格基準）。
    * _oco_ordered: 同一ティック両建てのタイブレーク（下落→sell_stop 先・上昇→buy_stop 先・
      pending_oco 無効や 1 件や ref 未知では元順序のまま）。
    * engine OCO（config pending_oco）: 両 stop が同バーで到達しても 1 本のみ約定（兄弟取消）。
      無効時は両側が独立約定する（従来挙動）。
    * engine 持続再アーム（config pending_persistent）: 約定→決済でフラット復帰したティックで
      on_tick が即再装填され、次の約定が生まれる（再アームの存在を 2 本目トレードで観測）。

実 MT5 アンカー（260620-2604-02・JP225 2026.04.01 01:01）:
    open=52969.8 spread=50 point=0.1 → bid=52969.8 ask=52974.8
    BuyStop=Ask+10=52984.8（SL=52964.8 / TP500=53034.8）、SellStop=Bid−10=52959.8。
"""
from __future__ import annotations

import numpy as np

from simulator.adapter.strategy.stop_entry_probe import StopEntryProbe
from simulator.domain.bar import Bar
from simulator.domain.order import Order
from simulator.main.run_config import RunConfig
from simulator.usecase.models import BacktestConfig, SymbolSpec
from simulator.usecase.run_backtest import (
    RunBacktestInteractor,
    RunBacktestRequest,
)


def _cfg(**kw):
    base = dict(
        point_size=0.1,
        digits=1,
        stops_level=0,
        entry_offset_points=100.0,  # ×0.1 = 10
        stop_loss_points=200,  # ×0.1 = 20
        take_profit_points=500,  # ×0.1 = 50
        lot_size=0.1,
    )
    base.update(kw)
    return base


# =========================================================================
# 戦略アダプタ
# =========================================================================

def test_on_new_bar_is_always_noop():
    s = StopEntryProbe()
    s.on_init(_cfg(), None)
    # 本 EA は OnTick で発注する＝バー境界では何もしない。
    assert s.on_new_bar(0, None, None) == []
    assert s.on_new_bar(123, None, _Acct(("buy",))) == []


def test_on_tick_arms_both_stops_at_tick_quote():
    s = StopEntryProbe()
    s.on_init(_cfg(), None)
    # MT5 アンカー: bid=52969.8 ask=52974.8。
    orders = s.on_tick(0, bid=52969.8, ask=52974.8, account=None)
    assert len(orders) == 2
    buy = next(o for o in orders if o.kind == "buy_stop")
    sell = next(o for o in orders if o.kind == "sell_stop")
    # BuyStop=Ask+offset / SellStop=Bid−offset。
    assert buy.price == 52984.8
    assert sell.price == 52959.8
    # SL/TP はペンディング価格基準（Buy: sl=price−20, tp=price+50 / Sell 対称）。
    assert (buy.sl, buy.tp) == (52964.8, 53034.8)
    assert (sell.sl, sell.tp) == (52979.8, 52909.8)
    assert buy.volume == 0.1 and sell.volume == 0.1


def test_on_tick_is_stateless_quote_drives_price():
    # 別クォートを渡すと装填価格も追従する（ステートレス＝engine が呼ぶ条件で持続を制御）。
    s = StopEntryProbe()
    s.on_init(_cfg(), None)
    o1 = s.on_tick(0, bid=100.0, ask=100.0, account=None)
    o2 = s.on_tick(0, bid=200.0, ask=200.0, account=None)
    assert next(o.price for o in o1 if o.kind == "buy_stop") == 110.0
    assert next(o.price for o in o2 if o.kind == "buy_stop") == 210.0


# =========================================================================
# engine 統合: OCO / 持続再アーム
# =========================================================================

class _Acct:
    def __init__(self, sides=()):
        self.open_positions = [type("P", (), {"side": s})() for s in sides]


class _SpyIndicators:
    def get(self, name):
        return None

    def update(self, bar_index):
        pass


class _BothStopsOnce:
    """bar0 の on_new_bar で両建て逆指値（SL/TP 無し）を 1 度だけ返すスパイ（OCO 検証用・非持続）。"""

    def __init__(self, buy_price, sell_price):
        self._orders = [
            Order(side="buy", kind="buy_stop", volume=0.1, price=buy_price),
            Order(side="sell", kind="sell_stop", volume=0.1, price=sell_price),
        ]
        self._done = False

    def on_init(self, config, indicators):
        pass

    def on_new_bar(self, bar_index, indicators, account):
        if self._done:
            return []
        self._done = True
        return list(self._orders)

    def on_position_check(self, position, bar_index, indicators):
        return "hold"


class _ListTickModel:
    def __init__(self, ticks_by_bar):
        self._ticks_by_bar = ticks_by_bar

    def ticks_of(self, bar, prev_close):
        return list(self._ticks_by_bar.get(bar.time, []))


def _bar(t, o, h, l, c, *, spread=0):
    return Bar(time=t, open=o, high=h, low=l, close=c, volume=1.0, spread=spread)


def _config(**overrides):
    base = dict(
        tick_model="real_ticks",
        spread_model="fixed",
        sltp_tie="sl",
        fill_delay="next_tick",
        ohlc_order="auto",
        session_calendar="none",
        digits=1,
        legacy_quirks=False,
        return_basis="equity",
        entry_price_basis="current_open",
        pending_lifecycle=True,
    )
    base.update(overrides)
    return BacktestConfig(**base)


def _spec():
    return SymbolSpec(
        contract_size=10.0, volume_min=0.01, volume_max=100.0, volume_step=0.01,
        stops_level=0, digits=1, point_size=0.1, leverage=10.0,
    )


class _ClosedBars:
    """指定 index を閉鎖バーとして返すスタブ SessionCalendarPort。"""

    def __init__(self, closed):
        self._closed = set(closed)

    def closed_bar_indices(self, bars):
        return set(self._closed)


def _run(strategy, bars, ticks, *, config, session_calendar=None,
         initial_deposit=10_000.0, stop_out_level=0.0):
    interactor = RunBacktestInteractor(
        strategy=strategy, indicators=_SpyIndicators(), tick_model=_ListTickModel(ticks),
        session_calendar=session_calendar,
    )
    req = RunBacktestRequest(
        config=config, bars=bars, symbol_spec=_spec(),
        initial_deposit=initial_deposit, stop_out_level=stop_out_level,
    )
    return interactor.execute(req)


class _MarketBuyOnce:
    """bar0 で成行 buy を 1 度だけ返すスパイ（stop-out 検証用）。"""

    def __init__(self):
        self._done = False

    def on_init(self, config, indicators):
        pass

    def on_new_bar(self, bar_index, indicators, account):
        if self._done:
            return []
        self._done = True
        return [Order(side="buy", kind="market", volume=1.0, price=None)]

    def on_position_check(self, position, bar_index, indicators):
        return "hold"


T0 = np.datetime64("2026-04-01T00:00")
T1 = np.datetime64("2026-04-01T00:01")
T2 = np.datetime64("2026-04-01T00:02")


def test_engine_oco_cancels_sibling_when_one_fills():
    # bar0 で buy_stop@110 / sell_stop@90 を設置。価格が上（115）に達し buy が約定した後、
    # 下（85）へ振れても OCO（pending_oco=True）で sell は取消され約定しない＝建玉 1 本のみ。
    bars = [_bar(T0, 100.0, 120.0, 80.0, 100.0), _bar(T1, 100.0, 100.0, 100.0, 100.0)]
    ticks = {
        T0: [(100.0, 100.0, 100.0, T0), (115.0, 115.0, 115.0, T0), (85.0, 85.0, 85.0, T0)],
        T1: [(100.0, 100.0, 100.0, T1)],
    }
    res = _run(
        _BothStopsOnce(110.0, 90.0), bars, ticks,
        config=_config(pending_oco=True),
    )
    # OCO 有効: buy 1 本のみ（end_of_test で清算され 1 トレード）。
    assert len(res.trades) == 1
    assert res.trades[0].side == "buy"
    assert res.trades[0].entry_price == 110.0


def test_engine_stop_out_fires_on_closed_bar():
    # stop-out（ブローカーのリスク清算）は「閉鎖バー」でも発火する＝SL/TP（顧客注文）とは
    #   非対称（2603 突合で 01:00 pre-open の stop-out が MT5 と一致・実証）。bar0 で buy 建て→
    #   bar1(閉鎖) の含み損で margin_level<stop_out_level → 閉鎖足でも stop-out 決済する。
    #   buy 1lot@100（margin=1*10*100/10=100）, 初期 110。bar1 価格98 → floating=(98-100)*10=-20→
    #   equity=90 → margin_level=90% < 100% で stop-out。
    bars = [_bar(T0, 100.0, 100.0, 100.0, 100.0), _bar(T1, 98.0, 98.0, 98.0, 98.0)]
    ticks = {T0: [(100.0, 100.0, 100.0, T0)], T1: [(98.0, 98.0, 98.0, T1)]}
    res = _run(
        _MarketBuyOnce(), bars, ticks,
        # pending_lifecycle=True で every-tick 経路・close_and_halt で強制決済（例外送出しない）。
        config=_config(stop_out_action="close_and_halt"),
        session_calendar=_ClosedBars({1}), initial_deposit=110.0, stop_out_level=100.0,
    )
    assert len(res.trades) == 1
    assert res.trades[0].exit_reason == "stop_out"
    # stop-out は閉鎖足 bar1（T1）で発火する（SL/TP のように繰り延べない）。
    assert res.trades[0].exit_time == T1


def test_engine_oco_same_tick_both_trigger_fills_both_hedge():
    # 広 spread 足で 1 ティックの bid-ask 帯（q_bid=price / q_ask=price+spread×point）が両 stop を
    # 跨ぐ場合、pending_oco=True でも両建て約定する（実 MT5 hedging はサーバが OnTick 前に当該
    # ティックの trigger 分を全約定＝OCO されない・2604-02 01:05:30 で実証）。
    #   spread=300・point=0.1 → 帯幅 30。price=85 → q_bid=85 / q_ask=115。buy_stop110・sell_stop90 を共に跨ぐ。
    bars = [_bar(T0, 85.0, 85.0, 85.0, 85.0, spread=300), _bar(T1, 85.0, 85.0, 85.0, 85.0)]
    ticks = {T0: [(85.0, 85.0, 85.0, T0)], T1: [(85.0, 85.0, 85.0, T1)]}
    res = _run(_BothStopsOnce(110.0, 90.0), bars, ticks, config=_config(pending_oco=True))
    assert sorted(t.side for t in res.trades) == ["buy", "sell"]  # 両建て（OCO されない）


def test_engine_oco_cross_tick_cancels_the_untriggered_sibling():
    # 別ティックで片側のみ trigger → 約定が起きたティックで「trigger しなかった」残ペンディングを
    # OCO 取消（CancelOpposite）。tick1 で buy 約定・sell は未 trigger → 取消され tick2 で約定しない。
    bars = [_bar(T0, 100.0, 120.0, 80.0, 100.0), _bar(T1, 100.0, 100.0, 100.0, 100.0)]
    ticks = {
        T0: [(100.0, 100.0, 100.0, T0), (115.0, 115.0, 115.0, T0), (85.0, 85.0, 85.0, T0)],
        T1: [(100.0, 100.0, 100.0, T1)],
    }
    res = _run(_BothStopsOnce(110.0, 90.0), bars, ticks, config=_config(pending_oco=True))
    assert len(res.trades) == 1 and res.trades[0].side == "buy"


def test_engine_without_oco_both_stops_fill_independently():
    # pending_oco 無効（既定）: 同じ値動きで buy(115) と sell(85) が独立約定＝建玉 2 本。
    bars = [_bar(T0, 100.0, 120.0, 80.0, 100.0), _bar(T1, 100.0, 100.0, 100.0, 100.0)]
    ticks = {
        T0: [(100.0, 100.0, 100.0, T0), (115.0, 115.0, 115.0, T0), (85.0, 85.0, 85.0, T0)],
        T1: [(100.0, 100.0, 100.0, T1)],
    }
    res = _run(_BothStopsOnce(110.0, 90.0), bars, ticks, config=_config(pending_oco=False))
    sides = sorted(t.side for t in res.trades)
    assert sides == ["buy", "sell"]  # 両側独立約定（従来挙動・回帰防止）


def test_engine_persistent_rearms_after_close_on_same_tick():
    # 持続＋再アーム: bar0 で probe が arm→buy 約定→SL 決済→同ティックで即再アーム→再約定。
    #   t0(100): arm buy_stop=110/sell_stop=90（buy sl=90 tp=160）。
    #   t1(115): buy 約定@110・OCO で sell 取消。
    #   t2(90) : buy SL=90 決済（pnl=-20）→ フラット→ 同ティックで再アーム buy_stop=100/sell_stop=80。
    #   t3(105): 再アームの buy_stop@100 が約定。end_of_test で清算。
    # → トレード 2 本（1 本目 SL 確定・2 本目の存在が再アーム発火の証拠）。
    bars = [_bar(T0, 100.0, 120.0, 80.0, 100.0)]
    ticks = {
        T0: [
            (100.0, 100.0, 100.0, T0),
            (115.0, 115.0, 115.0, T0),
            (90.0, 90.0, 90.0, T0),
            (105.0, 105.0, 105.0, T0),
        ],
    }
    probe = StopEntryProbe()
    # 実運用と同じく RunConfig（属性=決定論 / subscript=戦略パラメータ）を供給する。
    config = RunConfig(
        _config(pending_oco=True, pending_persistent=True), _cfg()
    )
    res = _run(probe, bars, ticks, config=config)
    assert len(res.trades) == 2
    assert res.trades[0].side == "buy"
    assert res.trades[0].entry_price == 110.0
    assert res.trades[0].exit_price == 90.0
    assert res.trades[0].exit_reason == "sl"
    # 2 本目は決済ティック(90)の即再アーム buy_stop=Ask+10=100 が約定したもの。
    assert res.trades[1].entry_price == 100.0


def test_engine_persistent_resting_does_not_fill_on_closed_bar():
    # 持続 resting が「閉鎖バー」へ持ち越されても約定しない（実 MT5 は閉鎖時間帯で約定拒否・
    #   2604-02 ISSUE-024: 23:59 等の閉鎖足で偽約定していた）。bar0 で arm→bar1(閉鎖)で
    #   買値到達も約定せず→bar2(開場)で約定する。約定時刻が bar2 であることで閉鎖足スキップを観測。
    bars = [
        _bar(T0, 100.0, 100.0, 100.0, 100.0),
        _bar(T1, 115.0, 115.0, 115.0, 115.0),  # 閉鎖足（buy_stop=110 に到達するが約定不可）
        _bar(T2, 115.0, 115.0, 115.0, 115.0),
    ]
    ticks = {
        T0: [(100.0, 100.0, 100.0, T0)],
        T1: [(115.0, 115.0, 115.0, T1)],
        T2: [(115.0, 115.0, 115.0, T2)],
    }
    probe = StopEntryProbe()
    config = RunConfig(
        _config(pending_oco=True, pending_persistent=True), _cfg()
    )
    res = _run(probe, bars, ticks, config=config, session_calendar=_ClosedBars({1}))
    assert len(res.trades) == 1
    assert res.trades[0].side == "buy"
    assert res.trades[0].entry_price == 110.0
    # 約定は bar2（T2）で起きる＝閉鎖足 bar1 ではスキップされた。
    assert res.trades[0].entry_time == T2


def test_engine_held_position_sltp_skips_closed_bar():
    # 保有玉の SL/TP は「閉鎖バー」では発火しない（実 MT5 は市場閉鎖で OnTick が走らず SL/TP を
    #   処理しない・2604-02 ISSUE-024: pre-open 01:00 で SL 誤発火し再アームが 1 バー先行していた）。
    #   bar0 で buy@110 約定→bar1(閉鎖)で SL=90 到達も決済せず→bar2(開場)で決済する。
    bars = [
        _bar(T0, 100.0, 120.0, 80.0, 100.0),
        _bar(T1, 90.0, 90.0, 90.0, 90.0),  # 閉鎖足: buy SL=90 に到達するが決済不可
        _bar(T2, 90.0, 90.0, 90.0, 90.0),
    ]
    ticks = {
        T0: [(100.0, 100.0, 100.0, T0), (115.0, 115.0, 115.0, T0)],  # arm→buy@110 約定
        T1: [(90.0, 90.0, 90.0, T1)],
        T2: [(90.0, 90.0, 90.0, T2)],
    }
    probe = StopEntryProbe()
    config = RunConfig(_config(pending_oco=True, pending_persistent=True), _cfg())
    res = _run(probe, bars, ticks, config=config, session_calendar=_ClosedBars({1}))
    assert len(res.trades) == 1
    assert res.trades[0].entry_price == 110.0
    assert res.trades[0].exit_reason == "sl"
    assert res.trades[0].exit_price == 90.0
    # SL 決済は閉鎖足 bar1 ではなく開場 bar2（T2）で起きる。
    assert res.trades[0].exit_time == T2
