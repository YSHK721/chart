"""E-Deal の単体テスト（CLEAN_ARCH §4 / METRICS §5.2）。

不変条件: direction in {in, out}。profit は式で一意（METRICS §5.2）:
    (close - entry) * sign * lot * contract_size + swap + commission
振る舞いなし（不変データ）。profit 計算は from_close ファクトリで行う。
"""
from __future__ import annotations

import dataclasses

import pytest

from backtest.domain.deal import Deal
from backtest.domain.exceptions import ExecutionError


class TestDealConstruction:
    def test_in_direction_stores_fields(self):
        deal = Deal(direction="in", price=100.0, volume=1.0, profit=0.0, swap=0.0, commission=0.0)
        assert deal.direction == "in"
        assert deal.price == 100.0

    def test_deal_is_frozen(self):
        deal = Deal(direction="out", price=100.0, volume=1.0, profit=5.0, swap=0.0, commission=0.0)
        with pytest.raises(dataclasses.FrozenInstanceError):
            deal.profit = 9.0  # type: ignore[misc]

    def test_invalid_direction_raises(self):
        with pytest.raises(ExecutionError):
            Deal(direction="sideways", price=100.0, volume=1.0, profit=0.0, swap=0.0, commission=0.0)


class TestDealFromClose:
    def test_buy_profit_formula(self):
        # (110 - 100) * (+1) * 1 * 100000 + swap(-5) + commission(-7) = 1_000_000 - 12
        deal = Deal.from_close(
            side="buy", entry_price=100.0, close_price=110.0, volume=1.0,
            contract_size=100_000, swap=-5.0, commission=-7.0,
        )
        assert deal.direction == "out"
        assert deal.profit == pytest.approx(1_000_000.0 - 12.0)

    def test_sell_profit_formula(self):
        # (90 - 100) * (-1) * 1 * 100000 + 0 + 0 = +1_000_000
        deal = Deal.from_close(
            side="sell", entry_price=100.0, close_price=90.0, volume=1.0,
            contract_size=100_000, swap=0.0, commission=0.0,
        )
        assert deal.profit == pytest.approx(1_000_000.0)

    def test_profit_round_digits_rounds_profit(self):
        # ISSUE-020: profit_round_digits 指定で profit を口座通貨桁へ丸める。
        # (100.002 - 100) * 1 * 1 * 100 = 0.2。None=素値 / 0=整数丸め。
        raw = Deal.from_close(
            side="buy", entry_price=100.0, close_price=100.002, volume=1.0,
            contract_size=100, swap=0.0, commission=0.0,
        )
        assert raw.profit == pytest.approx(0.2)
        rounded = Deal.from_close(
            side="buy", entry_price=100.0, close_price=100.002, volume=1.0,
            contract_size=100, swap=0.0, commission=0.0, profit_round_digits=0,
        )
        assert rounded.profit == pytest.approx(0.0)

    def test_swap_and_commission_included(self):
        # price == entry -> 価格差分 0、profit = swap + commission
        deal = Deal.from_close(
            side="buy", entry_price=100.0, close_price=100.0, volume=1.0,
            contract_size=100_000, swap=-3.0, commission=-2.0,
        )
        assert deal.profit == pytest.approx(-5.0)

    @pytest.mark.parametrize("bad_side", ["BUY", "long"])
    def test_invalid_side_raises_instead_of_silent_sign_flip(self, bad_side):
        # 回帰（🟡-3）: 不正 side を sell(-1) 扱いし損益符号を静かに反転させない
        with pytest.raises(ExecutionError):
            Deal.from_close(
                side=bad_side, entry_price=100.0, close_price=110.0, volume=1.0,
                contract_size=100_000, swap=0.0, commission=0.0,
            )
