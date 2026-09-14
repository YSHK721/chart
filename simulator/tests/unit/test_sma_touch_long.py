from __future__ import annotations

import pandas as pd

from simulator.adapter.indicator.registry import PandasIndicatorRegistry
from simulator.domain.order import Order


def _registry(low_vals, close_vals, sma_vals):
    return PandasIndicatorRegistry({
        "low": pd.Series(low_vals),
        "close": pd.Series(close_vals),
        "sma_5": pd.Series(sma_vals),
    })


def test_first_close_touch_of_sma5_opens_long():
    from simulator.adapter.strategy.sma_touch_long import SmaTouchLong

    strat = SmaTouchLong()
    ind = _registry(
        [110.0, 112.0, 120.0],
        [110.0, 112.0, 100.0],
        [109.0, 108.0, 101.0],
    )
    strat.on_init({"lot_size": 0.1, "stop_loss_points": 0, "take_profit_points": 0, "point_size": 0.1}, ind)

    orders = strat.on_new_bar(2, ind, type("Account", (), {"open_positions": []})())

    assert len(orders) == 1
    order = orders[0]
    assert isinstance(order, Order)
    assert order.side == "buy" and order.kind == "market"
    assert order.price is None
    assert order.volume == 0.1


def test_second_touch_does_not_duplicate_after_first_trigger():
    from simulator.adapter.strategy.sma_touch_long import SmaTouchLong

    strat = SmaTouchLong()
    ind = _registry(
        [110.0, 108.0, 100.0, 99.0, 100.2],
        [110.0, 108.0, 100.0, 99.0, 100.2],
        [109.0, 106.0, 102.0, 100.0, 101.0],
    )
    strat.on_init({"lot_size": 0.1, "stop_loss_points": 0, "take_profit_points": 0, "point_size": 0.1}, ind)

    strat.on_new_bar(2, ind, type("Account", (), {"open_positions": []})())
    orders = strat.on_new_bar(4, ind, type("Account", (), {"open_positions": []})())

    assert orders == []


def test_order_volume_is_lifted_to_symbol_minimum():
    from simulator.adapter.strategy.sma_touch_long import SmaTouchLong

    strat = SmaTouchLong()
    ind = _registry(
        [110.0, 108.0, 100.0, 99.0],
        [110.0, 108.0, 100.0, 99.0],
        [109.0, 106.0, 102.0, 100.0],
    )
    cfg = {
        "lot_size": 0.1,
        "volume_min": 1.0,
        "volume_max": 100.0,
        "volume_step": 1.0,
        "stop_loss_points": 0,
        "take_profit_points": 0,
        "point_size": 0.1,
    }
    strat.on_init(cfg, ind)

    orders = strat.on_new_bar(2, ind, type("Account", (), {"open_positions": []})())

    assert len(orders) == 1
    assert orders[0].volume == 1.0


def test_registry_builds_a_true_sma5_series():
    from simulator.main.ea_bindings.sma_touch_long import build_registry

    df = __import__("pandas").DataFrame({
        "low": [10.0, 9.0, 8.0, 7.0, 6.0, 5.0],
        "close": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0],
    })

    registry = build_registry(df)
    expected = df["close"].rolling(window=5, min_periods=5).mean()

    pd.testing.assert_series_equal(
        registry.get("sma_5").reset_index(drop=True),
        expected.reset_index(drop=True),
        check_names=False,
    )
    pd.testing.assert_series_equal(
        registry.get("close").reset_index(drop=True),
        df["close"].reset_index(drop=True),
        check_names=False,
    )
