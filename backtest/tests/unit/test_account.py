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

from backtest.domain.account import Account
from backtest.domain.bar import Bar
from backtest.domain.deal import Deal
from backtest.domain.position import Position


def _bar(close):
    return Bar(
        time=np.datetime64("2024-01-01"), open=close, high=close, low=close,
        close=close, volume=0.0, spread=0,
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
