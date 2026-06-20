"""E-Account の単体テスト（CLEAN_ARCH §4 / METRICS §5.1）。

不変条件・関係式:
    equity = balance + floating_pnl + swap + commission
    margin_level = equity / margin * 100  （margin == 0 のとき ∞）
公開振る舞い: apply_deal(deal), update_floating_pnl(bar), margin_level()。
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from simulator.domain.account import Account
from simulator.domain.bar import Bar
from simulator.domain.deal import Deal
from simulator.domain.position import Position


def _bar(close, spread=0):
    return Bar(
        time=np.datetime64("2024-01-01"), open=close, high=close, low=close,
        close=close, volume=0.0, spread=spread,
    )


class TestEquity:
    def test_equity_is_sum_of_components(self):
        acc = Account(balance=10_000.0)
        acc.floating_pnl = 500.0
        acc.swap = -10.0
        acc.commission = -7.0
        # 10000 + 500 - 10 - 7
        assert acc.equity == pytest.approx(10_483.0)

    def test_equity_equals_balance_when_no_floating(self):
        acc = Account(balance=10_000.0)
        assert acc.equity == pytest.approx(10_000.0)


class TestMarginLevel:
    def test_margin_level_formula(self):
        acc = Account(balance=10_000.0, margin=5_000.0)
        # equity 10000 / margin 5000 * 100 = 200
        assert acc.margin_level() == pytest.approx(200.0)

    def test_margin_level_infinite_when_margin_zero(self):
        # 境界: margin == 0 -> ∞
        acc = Account(balance=10_000.0, margin=0.0)
        assert math.isinf(acc.margin_level())


class TestApplyDeal:
    def test_apply_deal_adds_profit_to_balance(self):
        acc = Account(balance=10_000.0)
        deal = Deal(direction="out", price=110.0, volume=1.0, profit=250.0, swap=0.0, commission=0.0)
        acc.apply_deal(deal)
        assert acc.balance == pytest.approx(10_250.0)

    def test_apply_loss_deal_reduces_balance(self):
        acc = Account(balance=10_000.0)
        deal = Deal(direction="out", price=90.0, volume=1.0, profit=-300.0, swap=0.0, commission=0.0)
        acc.apply_deal(deal)
        assert acc.balance == pytest.approx(9_700.0)


class TestUpdateFloatingPnl:
    def test_recomputes_floating_from_open_positions(self):
        pos = Position(side="buy", volume=1.0, entry_price=100.0)
        acc = Account(balance=10_000.0, contract_size=100_000, open_positions=[pos])
        # bar.close 101 -> (101-100)*1*100000*(+1) = 100000
        acc.update_floating_pnl(_bar(101.0))
        assert acc.floating_pnl == pytest.approx(100_000.0)

    def test_zero_floating_when_no_open_positions(self):
        acc = Account(balance=10_000.0, contract_size=100_000, open_positions=[])
        acc.update_floating_pnl(_bar(101.0))
        assert acc.floating_pnl == pytest.approx(0.0)

    def test_floating_sums_multiple_positions(self):
        p1 = Position(side="buy", volume=1.0, entry_price=100.0)
        p2 = Position(side="sell", volume=1.0, entry_price=100.0)
        acc = Account(balance=10_000.0, contract_size=100_000, open_positions=[p1, p2])
        # buy: (101-100)*1*1e5*+1 = +100000 ; sell: (101-100)*1*1e5*-1 = -100000 ; sum 0
        acc.update_floating_pnl(_bar(101.0))
        assert acc.floating_pnl == pytest.approx(0.0)


# ---- 層2: floating_pnl_basis="bid_ask" — 含み損益を決済価格基準で評価する
#      （買い保有=Bid=close / 売り保有=Ask=close+spread×point）。config-gated・既定 "close" ----

class TestUpdateFloatingPnlBidAsk:
    """floating_pnl_basis="bid_ask" のとき、保有ポジを決済側の価格で評価する。

    実 MT5 はポジション決済価格基準で含み損益を評価する（買い保有=Bid・売り保有=Ask）。
    Ask = Bid(=bar.close) + spread×point。これにより売り保有の含み損が close 固定より
    悲観的（正しく）になり stop-out 発火が MT5 へ寄る。既定 "close" は従来評価で不変。
    """

    def test_buy_uses_bid_equals_close(self):
        # 買い保有は Bid(=bar.close) で評価＝close 評価と同値（spread の影響を受けない）。
        pos = Position(side="buy", volume=1.0, entry_price=100.0)
        acc = Account(
            balance=10_000.0, contract_size=100_000, open_positions=[pos],
            floating_pnl_basis="bid_ask", point_size=0.1,
        )
        # bar.close=101, spread=10 → buy は Bid=101 で評価 → (101-100)*1*1e5*+1 = 100000
        acc.update_floating_pnl(_bar(101.0, spread=10))
        assert acc.floating_pnl == pytest.approx(100_000.0)

    def test_sell_uses_ask_equals_close_plus_spread_times_point(self):
        # 売り保有は Ask(=bar.close + spread×point) で評価。Ask>close により含み損が
        # close 評価より悲観的になる（売りは価格が高いほど損）。
        pos = Position(side="sell", volume=1.0, entry_price=100.0)
        acc = Account(
            balance=10_000.0, contract_size=100_000, open_positions=[pos],
            floating_pnl_basis="bid_ask", point_size=0.1,
        )
        # bar.close=101, spread=10 → Ask=101+10*0.1=102 → (102-100)*1*1e5*-1 = -200000
        # （close 評価なら (101-100)*1e5*-1 = -100000。Ask 評価で -200000＝より悲観的）
        acc.update_floating_pnl(_bar(101.0, spread=10))
        assert acc.floating_pnl == pytest.approx(-200_000.0)

    def test_default_close_basis_ignores_spread_for_sell(self):
        # 後方互換: floating_pnl_basis 既定 "close" では売り保有も close 評価
        # （spread を無視）＝従来挙動が不変であることを固定する。
        pos = Position(side="sell", volume=1.0, entry_price=100.0)
        acc = Account(balance=10_000.0, contract_size=100_000, open_positions=[pos])
        # 既定 close 評価: (101-100)*1*1e5*-1 = -100000（spread=10 を無視）
        acc.update_floating_pnl(_bar(101.0, spread=10))
        assert acc.floating_pnl == pytest.approx(-100_000.0)


# ---- every-tick #3: 現在ティックの評価価格（bid/ask）で含み損益を更新する
#      update_floating_pnl_at(bid, ask)。買い保有=Bid 評価・売り保有=Ask 評価。
#      既存 update_floating_pnl(bar)（bar 経路）は不変。 ----

class TestUpdateFloatingPnlAtTick:
    """update_floating_pnl_at(bid, ask): 現在ティックの bid/ask で含み損益を再評価する。

    every-tick モードでは bar の close ではなく到達ティックの bid/ask で評価する。
    買い保有は決済（=売り戻し）= Bid、売り保有は決済（=買い戻し）= Ask で評価する
    （実 MT5 のポジション決済価格基準評価に整合）。bar 経路（update_floating_pnl）は不変。
    """

    def test_buy_evaluated_at_bid(self):
        # 買い保有はティック Bid で評価する。
        pos = Position(side="buy", volume=1.0, entry_price=100.0)
        acc = Account(balance=10_000.0, contract_size=100_000, open_positions=[pos])
        # bid=101, ask=102 → 買いは Bid=101 → (101-100)*1*1e5*+1 = 100000
        acc.update_floating_pnl_at(bid=101.0, ask=102.0)
        assert acc.floating_pnl == pytest.approx(100_000.0)

    def test_sell_evaluated_at_ask(self):
        # 売り保有はティック Ask で評価する（売りは価格が高いほど損＝悲観側）。
        pos = Position(side="sell", volume=1.0, entry_price=100.0)
        acc = Account(balance=10_000.0, contract_size=100_000, open_positions=[pos])
        # bid=101, ask=102 → 売りは Ask=102 → (102-100)*1*1e5*-1 = -200000
        acc.update_floating_pnl_at(bid=101.0, ask=102.0)
        assert acc.floating_pnl == pytest.approx(-200_000.0)

    def test_sums_buy_at_bid_and_sell_at_ask(self):
        # 複数保有: 買いは Bid・売りは Ask で各々評価し合算する。
        p_buy = Position(side="buy", volume=1.0, entry_price=100.0)
        p_sell = Position(side="sell", volume=1.0, entry_price=100.0)
        acc = Account(
            balance=10_000.0, contract_size=100_000,
            open_positions=[p_buy, p_sell],
        )
        # buy@Bid=101: (101-100)*1e5*+1 = +100000 ; sell@Ask=102: (102-100)*1e5*-1 = -200000
        acc.update_floating_pnl_at(bid=101.0, ask=102.0)
        assert acc.floating_pnl == pytest.approx(-100_000.0)

    def test_zero_when_no_open_positions(self):
        # 保有なしは 0（境界）。
        acc = Account(balance=10_000.0, contract_size=100_000, open_positions=[])
        acc.update_floating_pnl_at(bid=101.0, ask=102.0)
        assert acc.floating_pnl == pytest.approx(0.0)
