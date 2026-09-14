"""adapter/strategy/ma_slope.py の MaSlope 戦略テスト（原典 fixtures/mt5/ma_slope_jp225_202501/expert/MA_Slope_EA.mq5 / StrategyPort）。

戦略（原典 MA_Slope_EA — EMA 傾きで売買・新規バーのみ・確定足参照）:
    EMA(MA_Period=20, close) を indicators.get("ema") で取得。
    確定足 slope = ema[bar_index-1] − ema[bar_index-1-SlopeShift]（原典: ma[1]−ma[1+SlopeShift]）。
    threshold = SlopeMinPts × point_size。
    slope >  threshold → 買い / slope < −threshold → 売り / |slope| ≤ threshold → 様子見([])。
    同方向保有 → [] / 反対方向保有 → ドテン（反対側の成行 Order を返し interactor が反転決済）。
    SL/TP は stop_loss_points / take_profit_points が 0 なら None（本 EA は SL/TP 無し）。

ポート契約（usecase/run_backtest.py の Interactor 呼び出しに整合）:
    on_new_bar(bar_index, indicators, account) -> list[Order]
    - Order は kind="market"・price=None（約定価格は execution で解決）
    - 同方向重複は account.open_positions（None 可）の side で抑止する（tc24051901 と同契約）
    - 境界: bar_index < (1 + SlopeShift) は確定足 2 点が引けず []

config は subscript アクセス（dict 様。既存戦略・RunConfig と同契約）。
"""
from __future__ import annotations

import pandas as pd

from simulator.domain.order import Order
from simulator.usecase.ports import StrategyPort


# JP225 想定: point_size=0.1。SlopeMinPts=1.0 → threshold=0.1。SL/TP=0（無し）。
_CONFIG = {
    "lot_size": 0.1,
    "slope_shift": 1,
    "slope_min_points": 1.0,
    "point_size": 0.1,
    "stop_loss_points": 0,
    "take_profit_points": 0,
}


class _Account:
    def __init__(self, sides):
        self.open_positions = [type("P", (), {"side": s})() for s in sides]


def _registry(ema_vals):
    from simulator.adapter.indicator.registry import PandasIndicatorRegistry

    return PandasIndicatorRegistry({"ema": pd.Series(ema_vals)})


def test_ma_slope_implements_strategy_port():
    # Arrange / Act
    from simulator.adapter.strategy.ma_slope import MaSlope

    strat = MaSlope()

    # Assert: LSP — StrategyPort のサブクラスで抽象解決済み
    assert isinstance(strat, StrategyPort)


def test_upward_slope_above_threshold_opens_buy():
    # Arrange: 確定足 slope = ema[bi-1]-ema[bi-2]。bi=2 → ema[1]-ema[0]=0.3 > threshold0.1 → 買い
    from simulator.adapter.strategy.ma_slope import MaSlope

    strat = MaSlope()
    ind = _registry([100.0, 100.3, 100.5])
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(2, ind, _Account([]))

    # Assert: 買い成行・SL/TP 無し（本 EA は StopLoss/TakeProfit=0）
    assert len(orders) == 1
    o = orders[0]
    assert isinstance(o, Order)
    assert o.side == "buy" and o.kind == "market"
    assert o.price is None
    assert o.sl is None and o.tp is None
    assert o.volume == 0.1


def test_downward_slope_below_neg_threshold_opens_sell():
    # Arrange: ema[1]-ema[0] = -0.3 < -0.1 → 売り
    from simulator.adapter.strategy.ma_slope import MaSlope

    strat = MaSlope()
    ind = _registry([100.0, 99.7, 99.5])
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(2, ind, _Account([]))

    # Assert
    assert len(orders) == 1
    o = orders[0]
    assert o.side == "sell" and o.kind == "market"
    assert o.price is None and o.sl is None and o.tp is None


def test_slope_within_threshold_returns_no_orders():
    # Arrange: |slope| = 0.05 ≤ threshold 0.1 → 様子見（発注なし）
    from simulator.adapter.strategy.ma_slope import MaSlope

    strat = MaSlope()
    ind = _registry([100.0, 100.05, 100.1])
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(2, ind, _Account([]))

    # Assert
    assert orders == []


def test_slope_exactly_at_threshold_is_not_a_signal():
    # Arrange: 境界 slope == threshold（0.1）。原典は厳密 > / < のため発注なし
    from simulator.adapter.strategy.ma_slope import MaSlope

    strat = MaSlope()
    ind = _registry([100.0, 100.1, 100.2])  # slope = 0.1 == threshold
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(2, ind, _Account([]))

    # Assert: 等号は signal にしない（slope > threshold が False）
    assert orders == []


def test_same_side_position_blocks_duplicate_entry():
    # Arrange: 既に買い保有中の上向き傾き → 重複禁止（原典: signal==current は何もしない）
    from simulator.adapter.strategy.ma_slope import MaSlope

    strat = MaSlope()
    ind = _registry([100.0, 100.3, 100.5])
    strat.on_init(_CONFIG, ind)

    # Act
    orders = strat.on_new_bar(2, ind, _Account(["buy"]))

    # Assert
    assert orders == []


def test_opposite_side_position_triggers_reversal_order():
    # Arrange: sell 保有中の上向き傾き → ドテン（反対側 buy の成行 Order を返す）
    # 原典: current!=0 かつ signal≠current → 既存決済（interactor 反転）→新規。
    from simulator.adapter.strategy.ma_slope import MaSlope

    strat = MaSlope()
    ind = _registry([100.0, 100.3, 100.5])
    strat.on_init(_CONFIG, ind)

    # Act: sell 保有中の買いシグナル
    orders = strat.on_new_bar(2, ind, _Account(["sell"]))

    # Assert: 反対側（buy）成行を返す（interactor が既存 sell を reverse 決済する）
    assert len(orders) == 1 and orders[0].side == "buy" and orders[0].kind == "market"


def test_boundary_insufficient_bars_for_slope_returns_no_orders():
    # Arrange: 境界 bar_index < (1+SlopeShift)=2 は確定足 2 点が引けず []
    from simulator.adapter.strategy.ma_slope import MaSlope

    strat = MaSlope()
    ind = _registry([100.0, 100.3])
    strat.on_init(_CONFIG, ind)

    # Act: bar_index=1（slope は ema[0]-ema[-1] となり不正）
    orders = strat.on_new_bar(1, ind, _Account([]))

    # Assert
    assert orders == []


def test_slope_shift_two_uses_two_bar_lookback():
    # Arrange: SlopeShift=2 → slope = ema[bi-1] − ema[bi-3]。bi=3 → ema[2]-ema[0]。
    # 回帰: SlopeShift を無視して常に 1 本前と比較すると誤判定になる。
    from simulator.adapter.strategy.ma_slope import MaSlope

    cfg = dict(_CONFIG, slope_shift=2)
    strat = MaSlope()
    # ema[2]-ema[0] = 100.4-100.0 = 0.4 > 0.1 → 買い。直近 1 本 ema[2]-ema[1]=0.1（境界以下）
    ind = _registry([100.0, 100.3, 100.4, 100.45])
    strat.on_init(cfg, ind)

    # Act: bar_index=3（SlopeShift=2 で確定足 ema[2], ema[0] を参照）
    orders = strat.on_new_bar(3, ind, _Account([]))

    # Assert: 2 本前との差で買いシグナル
    assert len(orders) == 1 and orders[0].side == "buy"


def test_slope_shift_two_boundary_below_min_index_returns_no_orders():
    # Arrange: SlopeShift=2 → 最小有効 bar_index = 1+2 = 3。bar_index=2 は []
    from simulator.adapter.strategy.ma_slope import MaSlope

    cfg = dict(_CONFIG, slope_shift=2)
    strat = MaSlope()
    ind = _registry([100.0, 100.3, 100.7])
    strat.on_init(cfg, ind)

    # Act
    orders = strat.on_new_bar(2, ind, _Account([]))

    # Assert
    assert orders == []


def test_on_init_rejects_positive_stop_loss():
    # Arrange: MaSlope は SL 未サポート。StrategyPort 契約（on_new_bar）に無い
    # 暗黙事前条件を on_new_bar 経路の NotImplementedError で強制するのは LSP 不成立
    # （ISSUE-098 🟡-2）。起動前（on_init）にドメイン例外 ConfigError で拒否する。
    from simulator.adapter.strategy.ma_slope import MaSlope
    from simulator.domain.exceptions import ConfigError

    strat = MaSlope()
    ind = _registry([100.0, 100.3, 100.5])
    cfg = dict(_CONFIG, stop_loss_points=200)

    # Act / Assert: on_init 時点で ConfigError（起動前検出）
    import pytest
    with pytest.raises(ConfigError):
        strat.on_init(cfg, ind)


def test_on_init_rejects_positive_take_profit():
    # Arrange: TP>0 も同様に on_init で拒否する（ISSUE-098 🟡-2）
    from simulator.adapter.strategy.ma_slope import MaSlope
    from simulator.domain.exceptions import ConfigError

    strat = MaSlope()
    ind = _registry([100.0, 100.3, 100.5])
    cfg = dict(_CONFIG, take_profit_points=400)

    # Act / Assert
    import pytest
    with pytest.raises(ConfigError):
        strat.on_init(cfg, ind)


def test_on_init_accepts_zero_sl_tp_and_runs_normally():
    # Arrange: SL/TP=0（本 EA の正常系）は on_init が例外を投げず、on_new_bar が
    # NotImplementedError を送出せず正常に発注する（LSP 是正の回帰保証）
    from simulator.adapter.strategy.ma_slope import MaSlope

    strat = MaSlope()
    ind = _registry([100.0, 100.3, 100.5])

    # Act: SL/TP=0 は正常起動
    strat.on_init(_CONFIG, ind)
    orders = strat.on_new_bar(2, ind, _Account([]))

    # Assert: 例外なく買い成行・SL/TP 無し
    assert len(orders) == 1
    assert orders[0].side == "buy"
    assert orders[0].sl is None and orders[0].tp is None


def test_on_position_check_always_holds():
    # Arrange: 反転はシグナルで実施（on_position_check は SL/TP 監視用）→ "hold"
    from simulator.adapter.strategy.ma_slope import MaSlope

    strat = MaSlope()
    ind = _registry([100.0, 100.3, 100.5])
    strat.on_init(_CONFIG, ind)

    # Act
    decision = strat.on_position_check(None, 2, ind)

    # Assert
    assert decision == "hold"
