"""adapter/strategy/generic_condition_strategy.py の GenericConditionStrategy テスト（Phase 6 F-8）.

StrategyPort 実装（新規 Port 0）。spec 由来の EntryConditions（TBD-11: AND 連鎖・厳密不等号・
履歴参照）を registry 系列で評価し、成行 Order（固定 SL/TP）を返す。

ポート契約（tc24051901 / pro_fit_band と同形）:
    on_new_bar(bar_index, indicators, account) -> list[Order]
    - warmup ガード: bar_index < max_shift → []
    - held_sides 重複抑止（同方向保有中は再発注しない）
    - 成行 Order（kind="market"・price=None）・SL/TP は sltp_from_points（点数固定）
    - 基準価格系列は required_price_series(entry_price_basis)（close→"close" / current_open→"open"）
    - 系列未登録は例外伝播（fail-stop・無音の誤建値を作らない）
    - on_position_check → "hold"
"""
from __future__ import annotations

import pandas as pd
import pytest

from simulator.adapter.indicator.registry import PandasIndicatorRegistry
from simulator.domain.entry_conditions import Condition, EntryConditions, IndicatorRef
from simulator.domain.exceptions import IndicatorBufferError
from simulator.domain.order import Order
from simulator.usecase.ports import StrategyPort

_CONFIG = {
    "lot_size": 0.1,
    "stop_loss_points": 100,
    "take_profit_points": 200,
    "point_size": 0.0001,
}


class _Account:
    def __init__(self, sides):
        self.open_positions = [type("P", (), {"side": s})() for s in sides]


def _registry(**series):
    return PandasIndicatorRegistry({k: pd.Series(v) for k, v in series.items()})


def _long_only(**kw):
    """entry_long のみ（entry_short 空）で戦略を組む。"""
    from simulator.adapter.strategy.generic_condition_strategy import (
        GenericConditionStrategy,
    )

    return GenericConditionStrategy(
        entry_long=EntryConditions([Condition(indicator="ema", shift=0, op=">", rhs=1.0)]),
        entry_short=EntryConditions([]),
        **kw,
    )


def test_implements_strategy_port():
    strat = _long_only()
    assert isinstance(strat, StrategyPort)


def test_long_condition_true_opens_market_buy_with_fixed_sltp():
    # Arrange: ema[0]=2>1 → 成行買い。base=close[bar]=1.2010
    strat = _long_only()
    ind = _registry(ema=[2.0], close=[1.2010])
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(0, ind, _Account([]))

    # Assert
    assert len(orders) == 1
    o = orders[0]
    assert isinstance(o, Order)
    assert o.side == "buy" and o.kind == "market" and o.price is None
    assert o.volume == 0.1
    assert o.sl == pytest.approx(1.2010 - 100 * 0.0001)
    assert o.tp == pytest.approx(1.2010 + 200 * 0.0001)


def test_short_condition_true_opens_market_sell():
    # Arrange: entry_short 条件 rsi[0]<30 成立 → 成行売り
    from simulator.adapter.strategy.generic_condition_strategy import (
        GenericConditionStrategy,
    )

    strat = GenericConditionStrategy(
        entry_long=EntryConditions([]),
        entry_short=EntryConditions([Condition(indicator="rsi", shift=0, op="<", rhs=30.0)]),
    )
    ind = _registry(rsi=[25.0], close=[1.3000])
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(0, ind, _Account([]))

    # Assert: sell は sltp 対称
    assert len(orders) == 1
    o = orders[0]
    assert o.side == "sell" and o.kind == "market"
    assert o.sl == pytest.approx(1.3000 + 100 * 0.0001)
    assert o.tp == pytest.approx(1.3000 - 200 * 0.0001)


def test_condition_false_returns_no_orders():
    # Arrange: ema[0]=0.5>1 は偽 → 発注なし
    strat = _long_only()
    ind = _registry(ema=[0.5], close=[1.2010])
    strat.on_init(_CONFIG, ind)

    # Act / Assert
    assert strat.on_new_bar(0, ind, _Account([])) == []


def test_warmup_guard_below_max_shift_returns_empty():
    # Arrange: 条件 shift=2 → max_shift=2。bar_index=1(<2) は warmup で発注不可
    from simulator.adapter.strategy.generic_condition_strategy import (
        GenericConditionStrategy,
    )

    strat = GenericConditionStrategy(
        entry_long=EntryConditions([Condition(indicator="ema", shift=2, op=">", rhs=1.0)]),
        entry_short=EntryConditions([]),
    )
    ind = _registry(ema=[2.0, 2.0, 2.0], close=[1.2, 1.2, 1.2])
    strat.on_init(_CONFIG, ind)

    # Act / Assert: bar_index=1 は warmup → 空。bar_index=2 は評価され発注
    assert strat.on_new_bar(1, ind, _Account([])) == []
    assert len(strat.on_new_bar(2, ind, _Account([]))) == 1


def test_same_side_position_blocks_duplicate_entry():
    # Arrange: buy 保有中は buy を再発注しない
    strat = _long_only()
    ind = _registry(ema=[2.0], close=[1.2010])
    strat.on_init(_CONFIG, ind)

    # Act / Assert
    assert strat.on_new_bar(0, ind, _Account(["buy"])) == []


def test_opposite_side_position_does_not_block():
    # Arrange: sell 保有は buy エントリを妨げない（両建て）
    strat = _long_only()
    ind = _registry(ema=[2.0], close=[1.2010])
    strat.on_init(_CONFIG, ind)

    # Act / Assert
    orders = strat.on_new_bar(0, ind, _Account(["sell"]))
    assert len(orders) == 1 and orders[0].side == "buy"


def test_empty_side_never_fires():
    # Arrange: entry_short 空 → sell 条件が無い＝一度も売らない
    strat = _long_only()
    ind = _registry(ema=[0.5], close=[1.2010])  # long 条件も偽
    strat.on_init(_CONFIG, ind)

    # Act / Assert
    assert strat.on_new_bar(0, ind, _Account([])) == []


def test_open_basis_reads_open_series_for_base_price():
    # Arrange: entry_price_basis="current_open" → 建値系列は "open"（required_price_series）
    strat = _long_only(entry_price_basis="current_open")
    ind = _registry(ema=[2.0], open=[1.5000], close=[9.9999])
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(0, ind, _Account([]))

    # Assert: base=open[0]=1.5000（close は使わない）
    assert orders[0].sl == pytest.approx(1.5000 - 100 * 0.0001)


def test_missing_series_propagates_fail_stop():
    # Arrange: 条件が参照する "ema" が registry に無い → 例外伝播（無音にしない）
    strat = _long_only()
    ind = _registry(close=[1.2010])  # ema 未登録
    strat.on_init(_CONFIG, ind)

    # Act / Assert
    with pytest.raises(IndicatorBufferError):
        strat.on_new_bar(0, ind, _Account([]))


def test_rhs_indicator_ref_is_evaluated():
    # Arrange: ema[0] > close[1]（指標同士）
    from simulator.adapter.strategy.generic_condition_strategy import (
        GenericConditionStrategy,
    )

    strat = GenericConditionStrategy(
        entry_long=EntryConditions(
            [Condition(indicator="ema", shift=0, op=">", rhs=IndicatorRef(indicator="close", shift=1))]
        ),
        entry_short=EntryConditions([]),
    )
    ind = _registry(ema=[0.0, 3.0], close=[2.0, 5.0])
    strat.on_init(_CONFIG, ind)

    # Act: bar_index=1 → ema[1]=3 > close[0]=2 真
    orders = strat.on_new_bar(1, ind, _Account([]))

    # Assert
    assert len(orders) == 1 and orders[0].side == "buy"


def test_on_position_check_holds():
    strat = _long_only()
    ind = _registry(ema=[2.0], close=[1.2010])
    strat.on_init(_CONFIG, ind)
    assert strat.on_position_check(None, 0, ind) == "hold"


def test_zero_sl_points_yields_none_sl_on_order():
    # Arrange: stop_loss_points=0 → Order.sl は None（sltp_from_points 経由）
    strat = _long_only()
    ind = _registry(ema=[2.0], close=[1.2010])
    strat.on_init({**_CONFIG, "stop_loss_points": 0}, ind)

    # Act
    orders = strat.on_new_bar(0, ind, _Account([]))

    # Assert
    assert orders[0].sl is None
    assert orders[0].tp == pytest.approx(1.2010 + 200 * 0.0001)
