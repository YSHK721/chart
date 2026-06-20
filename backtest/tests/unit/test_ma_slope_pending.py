"""MaSlopePending 戦略アダプタの単体テスト（原典 MA_Slope_Pending_EA.mq5）。

検証観点:
    * シグナル（slope）は MaSlope と同一（上向き→買い/下向き→売り/閾値内→[]）。
    * ペンディング価格＝当該バー始値クォート ± offset（指値/逆指値）。
    * SL/TP はペンディング価格基準（CalcSlTp）。
    * 同方向保有は []、逆方向保有はペンディング 1 件（ドテンは Interactor 責務）。
実 MT5 アンカー（2603-01 journal・JP225 2026.03.02 01:01）:
    open=57653.7 spread=50 point=0.1 → bid=57653.7 ask=57658.7
    sell limit @57658.7 sl=57678.7 tp(TP400)=57618.7。
"""
from __future__ import annotations

import pandas as pd

from backtest.adapter.strategy.ma_slope_pending import MaSlopePending
from backtest.domain.order import Order


def _cfg(**kw):
    base = dict(
        slope_shift=1,
        slope_min_points=1.0,
        point_size=0.1,
        digits=1,
        stops_level=0,
        entry_type="limit",
        entry_offset_points=50.0,
        stop_loss_points=200,
        take_profit_points=400,
        lot_size=1.0,
    )
    base.update(kw)
    return base


class _Acct:
    def __init__(self, sides=()):
        self.open_positions = [type("P", (), {"side": s})() for s in sides]


def _indicators(ema_vals, opens, spreads):
    return type(
        "Ind",
        (),
        {
            "_d": {
                "ema": pd.Series(ema_vals, dtype=float),
                "open": pd.Series(opens, dtype=float),
                "spread": pd.Series(spreads, dtype=float),
            },
            "get": lambda self, name: self._d[name],
        },
    )()


def _strategy(cfg, ind):
    s = MaSlopePending()
    s.on_init(cfg, ind)
    return s


class TestSignal:
    def test_downslope_emits_sell_limit_with_mt5_anchor_prices(self):
        # ema 下向き（slope=-10 < -threshold0.1）→ sell。bar_index=2。
        ind = _indicators(
            ema_vals=[57700.0, 57690.0, 57680.0],
            opens=[0, 0, 57653.7],
            spreads=[0, 0, 50],
        )
        s = _strategy(_cfg(), ind)
        orders = s.on_new_bar(2, ind, _Acct())
        assert len(orders) == 1
        o = orders[0]
        assert isinstance(o, Order)
        assert o.kind == "sell_limit" and o.side == "sell"
        assert o.price == 57658.7      # bid(open) + offset 5.0
        assert o.sl == 57678.7         # price + SL 20.0
        assert o.tp == 57618.7         # price - TP 40.0
        assert o.volume == 1.0

    def test_upslope_emits_buy_limit_at_ask_minus_offset(self):
        # ema 上向き → buy。ask = open + spread*point = 100.0 + 50*0.1 = 105.0。
        ind = _indicators(
            ema_vals=[100.0, 110.0, 120.0],
            opens=[0, 0, 100.0],
            spreads=[0, 0, 50],
        )
        s = _strategy(_cfg(), ind)
        o = s.on_new_bar(2, ind, _Acct())[0]
        assert o.kind == "buy_limit" and o.side == "buy"
        assert o.price == 100.0      # ask(105.0) - offset 5.0
        assert o.sl == 80.0          # price - 20
        assert o.tp == 140.0         # price + 40

    def test_flat_slope_returns_empty(self):
        ind = _indicators([100.0, 100.0, 100.0], [0, 0, 100.0], [0, 0, 50])
        s = _strategy(_cfg(), ind)
        assert s.on_new_bar(2, ind, _Acct()) == []

    def test_boundary_insufficient_bars_returns_empty(self):
        ind = _indicators([100.0, 110.0], [0, 100.0], [0, 50])
        s = _strategy(_cfg(), ind)
        assert s.on_new_bar(1, ind, _Acct()) == []  # bar_index < 1+slope_shift(=2)


class TestHoldings:
    def test_same_direction_held_returns_empty(self):
        ind = _indicators([57700.0, 57690.0, 57680.0], [0, 0, 57653.7], [0, 0, 50])
        s = _strategy(_cfg(), ind)
        assert s.on_new_bar(2, ind, _Acct(sides=("sell",))) == []

    def test_opposite_held_emits_pending_for_doten(self):
        # 買い保有中に sell シグナル → sell ペンディングを返す（逆玉決済は Interactor）。
        ind = _indicators([57700.0, 57690.0, 57680.0], [0, 0, 57653.7], [0, 0, 50])
        s = _strategy(_cfg(), ind)
        orders = s.on_new_bar(2, ind, _Acct(sides=("buy",)))
        assert len(orders) == 1 and orders[0].kind == "sell_limit"


class TestStopEntry:
    def test_stop_entry_uses_favorable_side(self):
        # 逆指値: buy = ask + offset / sell = bid - offset。
        ind = _indicators([100.0, 90.0, 80.0], [0, 0, 100.0], [0, 0, 50])  # sell
        s = _strategy(_cfg(entry_type="stop"), ind)
        o = s.on_new_bar(2, ind, _Acct())[0]
        assert o.kind == "sell_stop"
        assert o.price == 95.0       # bid(100.0) - offset 5.0
        assert o.sl == 115.0         # price + 20
        assert o.tp == 55.0          # price - 40
