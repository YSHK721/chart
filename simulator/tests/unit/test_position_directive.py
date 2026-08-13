"""domain/position_directive.py の PositionDirective テスト（Phase 7 FR-07/08）.

PositionDirective（不変 DTO・pandas/JSON 非依存）:
    フィールド new_sl / new_tp / close_volume（いずれも float|None）。
    全 None は「無変更」（is_noop() が True）。
    frozen のため生成後の属性代入は不可（値オブジェクト）。
"""
from __future__ import annotations

import dataclasses

import pytest


def test_all_none_is_noop():
    # Arrange / Act: 全フィールド None
    from simulator.domain.position_directive import PositionDirective

    d = PositionDirective(new_sl=None, new_tp=None, close_volume=None)

    # Assert: 無変更
    assert d.is_noop() is True


def test_any_field_set_is_not_noop():
    from simulator.domain.position_directive import PositionDirective

    assert PositionDirective(new_sl=1.0, new_tp=None, close_volume=None).is_noop() is False
    assert PositionDirective(new_sl=None, new_tp=2.0, close_volume=None).is_noop() is False
    assert PositionDirective(new_sl=None, new_tp=None, close_volume=0.05).is_noop() is False


def test_fields_are_stored_verbatim():
    from simulator.domain.position_directive import PositionDirective

    d = PositionDirective(new_sl=101.5, new_tp=98.0, close_volume=0.03)

    assert d.new_sl == 101.5
    assert d.new_tp == 98.0
    assert d.close_volume == 0.03


def test_close_price_defaults_none_and_is_stored():
    from simulator.domain.position_directive import PositionDirective

    # 既定は None（後方互換）。
    assert PositionDirective(new_sl=None, new_tp=None, close_volume=None).close_price is None
    # 指定時は保持（部分決済のフィル価格）。
    d = PositionDirective(new_sl=None, new_tp=None, close_volume=0.05, close_price=105.0)
    assert d.close_price == 105.0
    # close_price は変更指示でない（is_noop は 3 アクション項目のみで判定）。
    assert PositionDirective(
        new_sl=None, new_tp=None, close_volume=None, close_price=105.0
    ).is_noop() is True


def test_is_frozen_value_object():
    from simulator.domain.position_directive import PositionDirective

    d = PositionDirective(new_sl=None, new_tp=None, close_volume=None)

    # frozen: 属性代入は FrozenInstanceError
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.new_sl = 1.0  # type: ignore[misc]
