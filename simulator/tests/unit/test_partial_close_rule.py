"""domain/partial_close_rule.py の PartialCloseRule テスト（Phase 7 FR-08・部分決済）.

PartialCloseRule（Value Object・pandas/JSON 非依存・点数駆動）:
    PartialCloseRule(trigger_profit_points, close_fraction, point_size)
    close_volume(side, entry, ref_price, position_volume, volume_step) -> float|None
      - trigger_profit_points 未満の含み益では None（部分決済しない）。
      - 作動時は position_volume × close_fraction を volume_step で **floor**（保守側丸め）。
      - floor 結果が 0 なら None（決済不可）。
      - floor 結果が position_volume 以上なら None（全量は部分決済でない・保守側）。
    buy/sell は対称。再発火抑止（1 回のみ）は本規則の責務外（PositionManager が担う）。
"""
from __future__ import annotations

import pytest

_PT = 0.1
_ENTRY = 100.0
# trigger=50pt(=5.0)


def _rule(close_fraction=0.5):
    from simulator.domain.partial_close_rule import PartialCloseRule

    return PartialCloseRule(
        trigger_profit_points=50,
        close_fraction=close_fraction,
        point_size=_PT,
    )


def test_buy_not_triggered_returns_none():
    # 含み益 4.0 < 5.0 → None
    r = _rule()
    assert r.close_volume("buy", _ENTRY, 104.0, 0.10, 0.01) is None


def test_buy_triggered_half_floored_to_step():
    # 含み益 6.0 >= 5.0・0.10×0.5=0.05・step 0.01 で floor → 0.05
    r = _rule()
    assert r.close_volume("buy", _ENTRY, 106.0, 0.10, 0.01) == pytest.approx(0.05)


def test_trigger_boundary_is_inclusive():
    r = _rule()
    assert r.close_volume("buy", _ENTRY, 105.0, 0.10, 0.01) == pytest.approx(0.05)


def test_conservative_floor_truncates_fraction():
    # 0.03×0.5=0.015・step 0.01 → floor(1.5)=1 → 0.01（切り上げない）
    r = _rule()
    assert r.close_volume("buy", _ENTRY, 106.0, 0.03, 0.01) == pytest.approx(0.01)


def test_floor_to_zero_returns_none():
    # 0.01×0.5=0.005・step 0.01 → floor(0.5)=0 → None（決済不可）
    r = _rule()
    assert r.close_volume("buy", _ENTRY, 106.0, 0.01, 0.01) is None


def test_full_volume_is_rejected():
    # fraction 1.0 → 0.10 全量は部分決済でない → None（保守側）
    r = _rule(close_fraction=1.0)
    assert r.close_volume("buy", _ENTRY, 106.0, 0.10, 0.01) is None


def test_sell_triggered_symmetric():
    # sell: 含み益 = entry - ref = 6.0 >= 5.0 → 0.05
    r = _rule()
    assert r.close_volume("sell", _ENTRY, 94.0, 0.10, 0.01) == pytest.approx(0.05)


def test_sell_not_triggered_returns_none():
    # sell: 含み益 = 100 - 96 = 4.0 < 5.0 → None
    r = _rule()
    assert r.close_volume("sell", _ENTRY, 96.0, 0.10, 0.01) is None


def test_fill_price_is_trigger_level_buy():
    # bar 粒度の部分 TP フィル価格 = entry + trigger×point_size（極値でなくトリガー水準）。
    # trigger 50pt × point 0.1 = 5.0 → 100 + 5 = 105.0。
    r = _rule()
    assert r.fill_price("buy", _ENTRY) == pytest.approx(105.0)


def test_fill_price_is_trigger_level_sell():
    # sell 対称: entry − trigger×point_size = 100 − 5 = 95.0。
    r = _rule()
    assert r.fill_price("sell", _ENTRY) == pytest.approx(95.0)
