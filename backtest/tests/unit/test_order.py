"""E-Order の単体テスト（CLEAN_ARCH §4 / PROCESS §4）。

不変条件:
    side  in {buy, sell}
    kind  in {market, buy_limit}
    volume は volume_step の倍数かつ [volume_min, volume_max]
    SL/TP は stops_level 距離制約（>= stops_level * point_size）
validate(symbol_spec) -> None | raises InvalidPriceError。

symbol_spec は duck typing（属性アクセス）で受ける。domain は外部依存ゼロ。
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from backtest.domain.order import Order
from backtest.domain.exceptions import InvalidPriceError


@dataclass(frozen=True)
class _Spec:
    """テスト用 symbol_spec スタブ（必要属性のみ）。"""

    volume_step: float = 0.01
    volume_min: float = 0.01
    volume_max: float = 100.0
    stops_level: int = 10
    point_size: float = 0.001


def _order(**kw):
    base = dict(
        side="buy",
        kind="market",
        volume=1.0,
        price=100.0,
        sl=None,
        tp=None,
    )
    base.update(kw)
    return Order(**base)


class TestOrderValid:
    def test_valid_market_order_passes(self):
        # Arrange
        order = _order()
        # Act / Assert: 例外を送出しない（戻り値 None）
        assert order.validate(_Spec()) is None

    def test_volume_at_min_boundary_passes(self):
        order = _order(volume=0.01)
        assert order.validate(_Spec()) is None

    def test_volume_at_max_boundary_passes(self):
        order = _order(volume=100.0)
        assert order.validate(_Spec()) is None

    def test_sl_tp_exactly_at_stops_level_distance_passes(self):
        # 距離 == stops_level(10) * point_size(0.001) = 0.01（境界）
        order = _order(side="buy", price=100.0, sl=99.99, tp=100.01)
        assert order.validate(_Spec()) is None

    def test_buy_limit_kind_passes(self):
        order = _order(kind="buy_limit", price=99.0)
        assert order.validate(_Spec()) is None

    def test_none_sl_tp_skips_distance_check(self):
        order = _order(sl=None, tp=None)
        assert order.validate(_Spec()) is None


class TestOrderInvalid:
    def test_invalid_side_raises(self):
        order = _order(side="long")
        with pytest.raises(InvalidPriceError):
            order.validate(_Spec())

    def test_invalid_kind_raises(self):
        order = _order(kind="sell_stop")
        with pytest.raises(InvalidPriceError):
            order.validate(_Spec())

    def test_volume_below_min_raises(self):
        order = _order(volume=0.005)
        with pytest.raises(InvalidPriceError):
            order.validate(_Spec())

    def test_volume_above_max_raises(self):
        order = _order(volume=200.0)
        with pytest.raises(InvalidPriceError):
            order.validate(_Spec())

    def test_volume_not_multiple_of_step_raises(self):
        # 0.015 は step 0.01 の倍数でない
        order = _order(volume=0.015)
        with pytest.raises(InvalidPriceError):
            order.validate(_Spec())

    def test_sl_too_close_raises(self):
        # 距離 0.005 < 0.01（stops_level 違反）
        order = _order(side="buy", price=100.0, sl=99.995)
        with pytest.raises(InvalidPriceError):
            order.validate(_Spec())

    def test_tp_too_close_raises(self):
        order = _order(side="buy", price=100.0, tp=100.005)
        with pytest.raises(InvalidPriceError):
            order.validate(_Spec())
