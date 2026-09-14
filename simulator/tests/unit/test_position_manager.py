"""adapter/position_manager の PositionManager / NullPositionManager テスト（Phase 7）.

PositionManager（PositionManagerPort 実装・TrailingRule/PartialCloseRule を DI）:
    evaluate(*, ot, ref_price, granularity, account) -> PositionDirective|None
      - トレーリングは trailing_granularity と一致する粒度でのみ適用。
      - 部分決済は 1 回のみ（同一玉で 2 回目以降は close_volume を出さない）。
      - 何も作動しないと None。
NullPositionManager（LSP・既定経路 byte 不変）: evaluate は常に None。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from simulator.domain.position import Position


def _ot(side, volume, entry_price, sl=None, tp=None, entry_time=0):
    """_OpenTrade 相当の最小 fake（.position/.sl/.tp/.entry_price/.entry_time）。"""
    return SimpleNamespace(
        position=Position(side=side, volume=volume, entry_price=entry_price),
        sl=sl,
        tp=tp,
        entry_price=entry_price,
        entry_time=entry_time,
    )


def _trailing(step_points=10):
    from simulator.domain.trailing_rule import TrailingRule

    return TrailingRule(
        trigger_points=50, distance_points=30, step_points=step_points, point_size=0.1
    )


def _partial(close_fraction=0.5):
    from simulator.domain.partial_close_rule import PartialCloseRule

    return PartialCloseRule(
        trigger_profit_points=50, close_fraction=close_fraction, point_size=0.1
    )


# --- NullPositionManager ----------------------------------------------------

def test_null_manager_always_returns_none():
    from simulator.adapter.position_manager.position_manager import NullPositionManager

    m = NullPositionManager()
    ot = _ot("buy", 0.1, 100.0)
    assert m.evaluate(ot=ot, ref_price=106.0, granularity="bar", account=None) is None
    assert m.evaluate(ot=ot, ref_price=106.0, granularity="tick", account=None) is None


# --- トレーリング -----------------------------------------------------------

def test_trailing_applies_on_matching_granularity():
    from simulator.adapter.position_manager.position_manager import PositionManager

    m = PositionManager(trailing_rule=_trailing(), trailing_granularity="bar")
    ot = _ot("buy", 0.1, 100.0, sl=102.0)
    d = m.evaluate(ot=ot, ref_price=106.0, granularity="bar", account=None)
    assert d is not None
    assert d.new_sl == pytest.approx(103.0)
    assert d.close_volume is None


def test_trailing_skipped_on_mismatched_granularity():
    from simulator.adapter.position_manager.position_manager import PositionManager

    m = PositionManager(trailing_rule=_trailing(), trailing_granularity="bar")
    ot = _ot("buy", 0.1, 100.0, sl=102.0)
    # tick 粒度では bar トレーリングは作動しない
    assert m.evaluate(ot=ot, ref_price=106.0, granularity="tick", account=None) is None


def test_trailing_not_triggered_returns_none():
    from simulator.adapter.position_manager.position_manager import PositionManager

    m = PositionManager(trailing_rule=_trailing(), trailing_granularity="bar")
    ot = _ot("buy", 0.1, 100.0, sl=102.0)
    assert m.evaluate(ot=ot, ref_price=104.0, granularity="bar", account=None) is None


# --- 部分決済 ---------------------------------------------------------------

def test_partial_close_directive_when_triggered():
    from simulator.adapter.position_manager.position_manager import PositionManager

    m = PositionManager(
        partial_close_rule=_partial(), trailing_granularity="tick", volume_step=0.01
    )
    ot = _ot("buy", 0.10, 100.0)
    d = m.evaluate(ot=ot, ref_price=106.0, granularity="tick", account=None)
    assert d is not None
    assert d.close_volume == pytest.approx(0.05)
    assert d.new_sl is None
    # tick 粒度: フィル価格は現在価格（ref_price）＝忠実。
    assert d.close_price == pytest.approx(106.0)


def test_partial_close_bar_fill_price_is_trigger_level():
    # bar 粒度: フィル価格はトリガー水準（entry 100 + trigger 50×point 0.1 = 105）＝
    #   到達価格 ref（極値）ではない（部分 TP のレベルフィル・依頼者裁定）。
    from simulator.adapter.position_manager.position_manager import PositionManager

    m = PositionManager(
        partial_close_rule=_partial(), trailing_granularity="bar", volume_step=0.01
    )
    ot = _ot("buy", 0.10, 100.0)
    d = m.evaluate(ot=ot, ref_price=110.0, granularity="bar", account=None)
    assert d is not None
    assert d.close_volume == pytest.approx(0.05)
    assert d.close_price == pytest.approx(105.0)


def test_partial_close_fires_only_once_per_position():
    from simulator.adapter.position_manager.position_manager import PositionManager

    m = PositionManager(
        partial_close_rule=_partial(), trailing_granularity="tick", volume_step=0.01
    )
    ot = _ot("buy", 0.10, 100.0, entry_time=7)
    first = m.evaluate(ot=ot, ref_price=106.0, granularity="tick", account=None)
    assert first is not None and first.close_volume == pytest.approx(0.05)
    # 同一玉（同 side/entry_time/entry_price）の残玉で再評価 → 再発火しない
    residual = _ot("buy", 0.05, 100.0, entry_time=7)
    second = m.evaluate(ot=residual, ref_price=106.0, granularity="tick", account=None)
    assert second is None


# --- 両規則の合成 -----------------------------------------------------------

def test_both_rules_compose_into_one_directive():
    from simulator.adapter.position_manager.position_manager import PositionManager

    m = PositionManager(
        trailing_rule=_trailing(),
        partial_close_rule=_partial(),
        trailing_granularity="tick",
        volume_step=0.01,
    )
    ot = _ot("buy", 0.10, 100.0, sl=102.0)
    d = m.evaluate(ot=ot, ref_price=106.0, granularity="tick", account=None)
    assert d is not None
    assert d.new_sl == pytest.approx(103.0)
    assert d.close_volume == pytest.approx(0.05)


def test_no_rules_returns_none():
    from simulator.adapter.position_manager.position_manager import PositionManager

    m = PositionManager(trailing_granularity="bar")
    ot = _ot("buy", 0.1, 100.0, sl=102.0)
    assert m.evaluate(ot=ot, ref_price=106.0, granularity="bar", account=None) is None
