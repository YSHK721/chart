"""TDD: domain/oco_order_pair.py（詳細設計 §3.4 / §9.1・D1 単玉 market+SL/TP）。"""
from __future__ import annotations

import pytest

from simulator.domain.oco_order_pair import OcoOrderPair
from simulator.domain.order import Order
from simulator.domain.volatility_band import VolatilityBand


def _band() -> VolatilityBand:
    return VolatilityBand("w", O=100.0, S=90.0, T=110.0, N=2.0, p_tp=0.50)


class TestOcoOrderPairValid:
    def test_market_buy_with_sltp_constructs(self):
        entry = Order("buy", "market", 2.0, None, sl=90.0, tp=110.0)
        oco = OcoOrderPair(entry, _band())
        assert oco.entry is entry

    def test_as_orders_returns_single_entry(self):
        entry = Order("buy", "market", 2.0, None, sl=90.0, tp=110.0)
        oco = OcoOrderPair(entry, _band())
        orders = oco.as_orders()
        assert orders == [entry]


class TestOcoOrderPairInvalid:
    def test_non_market_entry_raises(self):
        entry = Order("buy", "buy_limit", 2.0, 95.0, sl=90.0, tp=110.0)
        with pytest.raises(ValueError):
            OcoOrderPair(entry, _band())

    def test_sell_side_raises(self):
        entry = Order("sell", "market", 2.0, None, sl=110.0, tp=90.0)
        with pytest.raises(ValueError):
            OcoOrderPair(entry, _band())

    def test_missing_sl_raises(self):
        entry = Order("buy", "market", 2.0, None, sl=None, tp=110.0)
        with pytest.raises(ValueError):
            OcoOrderPair(entry, _band())

    def test_missing_tp_raises(self):
        entry = Order("buy", "market", 2.0, None, sl=90.0, tp=None)
        with pytest.raises(ValueError):
            OcoOrderPair(entry, _band())
