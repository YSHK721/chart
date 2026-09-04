"""PendingLifecycleEngine 単体テスト（ISSUE-094 🔴-1 抽出物の直接検証）。

RunBacktestInteractor._execute_every_tick から抽出したペンディング注文の「トリガ評価 +
OCO」判定と MT5 OHLC クォート規約を直接検証する。account 反映を伴わない純ロジック契約を
固定し、既存 every-tick 挙動テスト（byte-identical）とは独立に単体で担保する。
"""
from __future__ import annotations

from simulator.domain.order import Order
from simulator.usecase.pending_lifecycle import PendingLifecycleEngine


def _order(kind, price, side, volume=1.0):
    return Order(side=side, volume=volume, kind=kind, price=price)


class TestTickQuote:
    def test_bid_is_price_ask_adds_spread_times_point(self):
        bid, ask = PendingLifecycleEngine.tick_quote(100.0, spread=10, point_size=0.1)
        assert bid == 100.0
        assert ask == 100.0 + 10 * 0.1

    def test_zero_spread_bid_equals_ask(self):
        bid, ask = PendingLifecycleEngine.tick_quote(52000.0, spread=0, point_size=0.1)
        assert bid == 52000.0
        assert ask == 52000.0


class TestEvaluateTriggers:
    def test_no_resting_yields_empty(self):
        filled, carried = PendingLifecycleEngine.evaluate_triggers(
            [], bid=100.0, ask=100.0, oco=False
        )
        assert filled == []
        assert carried == []

    def test_buy_stop_triggers_when_ask_reaches_price(self):
        # buy_stop: Ask >= price で約定。ask=100.5 >= 100.0 → 約定。
        o = _order("buy_stop", 100.0, "buy")
        filled, carried = PendingLifecycleEngine.evaluate_triggers(
            [o], bid=100.4, ask=100.5, oco=False
        )
        assert len(filled) == 1
        order, pos = filled[0]
        assert order is o
        assert pos.side == "buy"
        assert pos.entry_price == 100.0  # 約定価格=注文価格（スリッページ0）
        assert carried == []

    def test_untriggered_order_carried(self):
        # buy_limit: Ask <= price で約定。ask=101 > 100 → 未約定→carried。
        o = _order("buy_limit", 100.0, "buy")
        filled, carried = PendingLifecycleEngine.evaluate_triggers(
            [o], bid=101.0, ask=101.0, oco=False
        )
        assert filled == []
        assert carried == [o]

    def test_oco_clears_carried_when_one_fills(self):
        # 両側 stop（buy_stop@100 / sell_stop@100）。広帯 bid=99.5, ask=100.5 →
        #   buy_stop: ask>=100 約定 / sell_stop: bid<=100 約定 → 両約定。OCO でも両約定は残す。
        buy_stop = _order("buy_stop", 100.0, "buy")
        sell_stop = _order("sell_stop", 100.0, "sell")
        filled, carried = PendingLifecycleEngine.evaluate_triggers(
            [buy_stop, sell_stop], bid=99.5, ask=100.5, oco=True
        )
        assert len(filled) == 2  # trigger した側どうしは取り消さない
        assert carried == []

    def test_oco_cancels_untriggered_sibling(self):
        # buy_stop@100 が約定・sell_limit@200 は未到達。OCO 有効 → 未約定 sibling を取消。
        buy_stop = _order("buy_stop", 100.0, "buy")
        sell_limit = _order("sell_limit", 200.0, "sell")
        filled, carried = PendingLifecycleEngine.evaluate_triggers(
            [buy_stop, sell_limit], bid=100.5, ask=100.5, oco=True
        )
        assert len(filled) == 1
        assert filled[0][0] is buy_stop
        assert carried == []  # OCO で未約定 sell_limit を取消

    def test_no_oco_keeps_untriggered_sibling(self):
        buy_stop = _order("buy_stop", 100.0, "buy")
        sell_limit = _order("sell_limit", 200.0, "sell")
        filled, carried = PendingLifecycleEngine.evaluate_triggers(
            [buy_stop, sell_limit], bid=100.5, ask=100.5, oco=False
        )
        assert len(filled) == 1
        assert carried == [sell_limit]  # OCO 無効なら未約定は持ち越し

    def test_preserves_resting_scan_order(self):
        # filled は resting の走査順を保つ（account 反映順＝評価順の byte-identical 担保）。
        b1 = _order("buy_stop", 100.0, "buy", volume=0.1)
        b2 = _order("buy_stop", 100.0, "buy", volume=0.2)
        filled, carried = PendingLifecycleEngine.evaluate_triggers(
            [b1, b2], bid=100.5, ask=100.5, oco=False
        )
        assert [o for o, _ in filled] == [b1, b2]
