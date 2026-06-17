"""adapter/strategy/tc24051901.py の TC24051901 戦略テスト（SPEC §3.1・StrategyPort）。

戦略（SPEC §3.1 — MADiff ゼロクロス両建て）:
    エントリ:
        買い: prev < 0 && curr > 0 かつ 同方向ポジ無し → 成行買い
        売り: prev > 0 && curr < 0 かつ 同方向ポジ無し → 成行売り
    決済: 発注時の固定 SL/TP のみ（反転決済なし）。
        買い: price=Ask, sl=price−StopLoss×point, tp=price+TakeProfit×point
        売り: price=Bid, sl=price+StopLoss×point, tp=price−TakeProfit×point
    実行頻度: 毎ティック（新規バー判定なし）。

ポート契約（usecase/run_backtest.py の Interactor 呼び出しに整合）:
    on_new_bar(bar_index, indicators, account) -> list[Order]
    - curr = indicators.get("madiff")[bar_index], prev = [bar_index-1]
    - エントリ基準価格は indicators.get("close")[bar_index]（最小骨格は spread=0 で
      Ask=Bid=close。実 spread は spread_model 接続時に拡張＝範囲外）
    - Order は kind="market"・price=None（約定価格は execution で解決）・sl/tp は絶対価格
    同方向重複は account.open_positions（None 可）の side で抑止する。
"""
from __future__ import annotations

import pandas as pd

from backtest.domain.order import Order
from backtest.usecase.ports import StrategyPort


_CONFIG = {
    "lot_size": 0.1,
    "stop_loss_points": 100,
    "take_profit_points": 200,
    "point_size": 0.0001,
}


class _Account:
    def __init__(self, sides):
        self.open_positions = [type("P", (), {"side": s})() for s in sides]


def _registry(madiff_vals, close_vals):
    from backtest.adapter.indicator.registry import PandasIndicatorRegistry

    return PandasIndicatorRegistry(
        {"madiff": pd.Series(madiff_vals), "close": pd.Series(close_vals)}
    )


def test_tc24051901_implements_strategy_port():
    # Arrange / Act
    from backtest.adapter.strategy.tc24051901 import TC24051901

    strat = TC24051901()

    # Assert: LSP — StrategyPort のサブクラスで抽象解決済み
    assert isinstance(strat, StrategyPort)


def test_bullish_cross_prev_neg_curr_pos_opens_buy():
    # Arrange: prev<0 && curr>0 → 成行買い。sl=Ask−SL×point, tp=Ask+TP×point
    from backtest.adapter.strategy.tc24051901 import TC24051901

    strat = TC24051901()
    ind = _registry(madiff_vals=[-1.0, 0.5], close_vals=[1.2000, 1.2010])
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(1, ind, _Account([]))

    # Assert
    assert len(orders) == 1
    o = orders[0]
    assert isinstance(o, Order)
    assert o.side == "buy" and o.kind == "market"
    assert o.sl == 1.2010 - 100 * 0.0001
    assert o.tp == 1.2010 + 200 * 0.0001


def test_bearish_cross_prev_pos_curr_neg_opens_sell():
    # Arrange: prev>0 && curr<0 → 成行売り。sl=Bid+SL×point, tp=Bid−TP×point
    from backtest.adapter.strategy.tc24051901 import TC24051901

    strat = TC24051901()
    ind = _registry(madiff_vals=[0.7, -0.3], close_vals=[1.3000, 1.2990])
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(1, ind, _Account([]))

    # Assert
    assert len(orders) == 1
    o = orders[0]
    assert o.side == "sell" and o.kind == "market"
    assert o.sl == 1.2990 + 100 * 0.0001
    assert o.tp == 1.2990 - 200 * 0.0001


def test_no_cross_returns_no_orders():
    # Arrange: 同符号継続（prev>0, curr>0）はクロスでない → 発注なし
    from backtest.adapter.strategy.tc24051901 import TC24051901

    strat = TC24051901()
    ind = _registry(madiff_vals=[0.4, 0.8], close_vals=[1.2000, 1.2010])
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(1, ind, _Account([]))

    # Assert
    assert orders == []


def test_first_bar_has_no_prev_returns_no_orders():
    # Arrange: 境界 bar_index=0 は prev 不在 → クロス判定不可で発注なし
    from backtest.adapter.strategy.tc24051901 import TC24051901

    strat = TC24051901()
    ind = _registry(madiff_vals=[0.5], close_vals=[1.2000])
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(0, ind, _Account([]))

    # Assert
    assert orders == []


def test_same_side_position_blocks_duplicate_entry():
    # Arrange: 既に買い保有中の bullish cross は重複禁止（SPEC §3.1 制限）
    from backtest.adapter.strategy.tc24051901 import TC24051901

    strat = TC24051901()
    ind = _registry(madiff_vals=[-1.0, 0.5], close_vals=[1.2000, 1.2010])
    strat.on_init(_CONFIG, ind)

    # Act: 既存 buy 保有
    orders = strat.on_new_bar(1, ind, _Account(["buy"]))

    # Assert
    assert orders == []


def test_opposite_side_position_does_not_block_entry():
    # Arrange: 反対方向ポジ（sell 保有）は買いエントリを妨げない（両建て可・SPEC §3.1）
    from backtest.adapter.strategy.tc24051901 import TC24051901

    strat = TC24051901()
    ind = _registry(madiff_vals=[-1.0, 0.5], close_vals=[1.2000, 1.2010])
    strat.on_init(_CONFIG, ind)

    # Act: sell 保有中の bullish cross
    orders = strat.on_new_bar(1, ind, _Account(["sell"]))

    # Assert
    assert len(orders) == 1 and orders[0].side == "buy"


def test_warmup_nan_madiff_emits_no_order_on_warmup_bar():
    # Arrange: warmup の MADiff は NaN（SPEC §1.2 未確定）。warmup bar では curr が NaN。
    # 回帰: warmup の値が numeric sentinel（例 0 付近の微小負値）だと誤クロスで spurious
    # Order を生むため、NaN 化により warmup bar で発注ゼロを担保する（誤シグナル禁止）。
    from backtest.adapter.strategy.tc24051901 import TC24051901

    strat = TC24051901()
    # index0,1 = warmup(NaN), index2 = 有効値。bar_index=1 は warmup bar（curr NaN）。
    ind = _registry(madiff_vals=[float("nan"), float("nan"), 0.5],
                    close_vals=[1.2000, 1.2010, 1.2020])
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(1, ind, _Account([]))

    # Assert: warmup bar は curr=NaN → どの比較も False → 発注なし
    assert orders == []


def test_first_valid_bar_after_warmup_does_not_false_cross():
    # Arrange: warmup→有効の遷移 bar。prev=warmup(NaN), curr=有効値（正）。
    # 回帰: prev が 0 付近 numeric sentinel だと prev<0 が成立し誤 buy を出すリスク。
    # NaN なら prev<0 が必ず False → warmup 由来の誤クロスを構造的に禁止する。
    from backtest.adapter.strategy.tc24051901 import TC24051901

    strat = TC24051901()
    ind = _registry(madiff_vals=[float("nan"), float("nan"), 0.5],
                    close_vals=[1.2000, 1.2010, 1.2020])
    strat.on_init(_CONFIG, ind)

    # Act: bar_index=2 は最初の有効 bar（prev=index1=NaN, curr=0.5）
    orders = strat.on_new_bar(2, ind, _Account([]))

    # Assert: warmup prev との比較は誤クロスを生まない（発注なし）
    assert orders == []


def test_on_position_check_always_holds():
    # Arrange: TC24051901 は反転決済なし（固定 SL/TP のみ）→ 常に "hold"
    from backtest.adapter.strategy.tc24051901 import TC24051901

    strat = TC24051901()
    ind = _registry(madiff_vals=[-1.0, 0.5], close_vals=[1.2000, 1.2010])
    strat.on_init(_CONFIG, ind)

    # Act
    decision = strat.on_position_check(None, 1, ind)

    # Assert
    assert decision == "hold"
