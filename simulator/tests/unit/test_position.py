"""E-Position の単体テスト（CLEAN_ARCH §4 / METRICS §5.1, §5.3）。

不変条件: volume > 0, entry_price > 0。
floating_pnl(price, contract_size) = (price - entry) * lot * contract_size * sign
    （buy=+1 / sell=-1, METRICS §5.1）
required_margin(leverage) = lot * contract_size * entry / leverage（METRICS §5.3）
"""
from __future__ import annotations

import pytest

from simulator.domain.position import Position
from simulator.domain.exceptions import ExecutionError


def _pos(**kw):
    base = dict(side="buy", volume=1.0, entry_price=100.0)
    base.update(kw)
    return Position(**base)


class TestFloatingPnl:
    def test_buy_profit_when_price_above_entry(self):
        # (110 - 100) * 1.0 * 100000 * (+1) = 1_000_000
        pos = _pos(side="buy", volume=1.0, entry_price=100.0)
        assert pos.floating_pnl(price=110.0, contract_size=100_000) == pytest.approx(1_000_000.0)

    def test_sell_profit_when_price_below_entry(self):
        # (90 - 100) * 1.0 * 100000 * (-1) = +1_000_000
        pos = _pos(side="sell", volume=1.0, entry_price=100.0)
        assert pos.floating_pnl(price=90.0, contract_size=100_000) == pytest.approx(1_000_000.0)

    def test_buy_loss_when_price_below_entry(self):
        pos = _pos(side="buy", volume=2.0, entry_price=100.0)
        # (95 - 100) * 2 * 100000 * (+1) = -1_000_000
        assert pos.floating_pnl(price=95.0, contract_size=100_000) == pytest.approx(-1_000_000.0)

    def test_sell_loss_when_price_above_entry(self):
        pos = _pos(side="sell", volume=2.0, entry_price=100.0)
        # (105 - 100) * 2 * 100000 * (-1) = -1_000_000
        assert pos.floating_pnl(price=105.0, contract_size=100_000) == pytest.approx(-1_000_000.0)

    def test_zero_at_price_equals_entry_buy(self):
        # 符号境界: price == entry -> 0
        pos = _pos(side="buy", entry_price=100.0)
        assert pos.floating_pnl(price=100.0, contract_size=100_000) == 0.0

    def test_zero_at_price_equals_entry_sell(self):
        pos = _pos(side="sell", entry_price=100.0)
        assert pos.floating_pnl(price=100.0, contract_size=100_000) == 0.0


class TestRequiredMargin:
    def test_required_margin_formula(self):
        # 1.0 * 100000 * 100 / 100 = 100000
        pos = _pos(volume=1.0, entry_price=100.0)
        assert pos.required_margin(leverage=100, contract_size=100_000) == pytest.approx(100_000.0)

    def test_required_margin_scales_with_volume(self):
        pos = _pos(volume=2.5, entry_price=100.0)
        # 2.5 * 100000 * 100 / 100 = 250000
        assert pos.required_margin(leverage=100, contract_size=100_000) == pytest.approx(250_000.0)

    def test_required_margin_scales_with_leverage(self):
        pos = _pos(volume=1.0, entry_price=100.0)
        # leverage 200 -> 半分
        assert pos.required_margin(leverage=200, contract_size=100_000) == pytest.approx(50_000.0)


class TestPositionInvariants:
    def test_zero_volume_raises(self):
        with pytest.raises(ExecutionError):
            _pos(volume=0.0)

    def test_negative_volume_raises(self):
        with pytest.raises(ExecutionError):
            _pos(volume=-1.0)

    def test_zero_entry_price_raises(self):
        with pytest.raises(ExecutionError):
            _pos(entry_price=0.0)

    def test_negative_entry_price_raises(self):
        with pytest.raises(ExecutionError):
            _pos(entry_price=-100.0)

    def test_invalid_side_raises(self):
        with pytest.raises(ExecutionError):
            _pos(side="long")
