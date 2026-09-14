from __future__ import annotations

import pandas as pd

from simulator.adapter.indicator.registry import PandasIndicatorRegistry


def _registry(close_vals):
    return PandasIndicatorRegistry({"close": pd.Series(close_vals)})


def test_open_then_close_5m_long_sets_tp_to_close_5_bars_later():
    from simulator.adapter.strategy.open_then_close_5m import OpenThenClose5mLong

    strat = OpenThenClose5mLong()
    close = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0]
    ind = _registry(close)
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

    orders = strat.on_new_bar(0, ind, type("Account", (), {"open_positions": []})())

    assert len(orders) == 1
    assert orders[0].side == "buy"
    assert orders[0].tp == 105.0
    assert orders[0].sl is None
