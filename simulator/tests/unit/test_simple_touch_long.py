from __future__ import annotations

import pandas as pd

from simulator.adapter.indicator.registry import PandasIndicatorRegistry


def _registry(low_vals, close_vals, sma_vals):
    return PandasIndicatorRegistry({
        "low": pd.Series(low_vals),
        "close": pd.Series(close_vals),
        "sma_5": pd.Series(sma_vals),
    })


def test_simple_touch_long_fires_once_on_first_close_touch():
    from simulator.adapter.strategy.simple_touch_long import SimpleTouchLong

    strat = SimpleTouchLong()
    ind = _registry(
        [110.0, 112.0, 120.0],
        [110.0, 112.0, 100.0],
        [109.0, 108.0, 101.0],
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
    assert orders[0].side == "buy"
    assert orders[0].volume == 1.0
