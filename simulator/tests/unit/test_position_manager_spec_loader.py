"""framework/position_manager_spec_loader.py のテスト（Phase 7 FR-07/08 spec 受理）.

load_position_change_spec(strategy_block, *, point_size):
    strategy ブロックの trailing / partial_close サブブロックを検証し、domain 規則
    （TrailingRule / PartialCloseRule）と粒度へ変換して :class:`PositionChangeSpec` を返す。
    どちらも無ければ None（OFF）。sizing/strategy loader と同流儀（pydantic extra=forbid・
    ValidationError→ConfigError）。
"""
from __future__ import annotations

import pytest

from simulator.domain.exceptions import ConfigError


def test_returns_none_when_neither_present():
    from simulator.framework.position_manager_spec_loader import load_position_change_spec

    assert load_position_change_spec({"entry_long": []}, point_size=0.1) is None
    assert load_position_change_spec({}, point_size=0.1) is None


def test_builds_trailing_rule():
    from simulator.framework.position_manager_spec_loader import load_position_change_spec

    spec = load_position_change_spec(
        {"trailing": {"granularity": "tick", "trigger_points": 50,
                      "distance_points": 30, "step_points": 10}},
        point_size=0.1,
    )
    assert spec is not None
    assert spec.trailing_granularity == "tick"
    assert spec.partial_close_rule is None
    # 規則が点数→価格換算を保持している（point_size 注入）。
    assert spec.trailing_rule.new_stop("buy", 100.0, 106.0, None) == pytest.approx(103.0)


def test_trailing_default_granularity_is_bar():
    from simulator.framework.position_manager_spec_loader import load_position_change_spec

    spec = load_position_change_spec(
        {"trailing": {"trigger_points": 50, "distance_points": 30}}, point_size=0.1
    )
    assert spec.trailing_granularity == "bar"
    assert spec.trailing_rule.step_points == 0  # step 省略時は連続


def test_builds_partial_close_rule():
    from simulator.framework.position_manager_spec_loader import load_position_change_spec

    spec = load_position_change_spec(
        {"partial_close": {"trigger": {"profit_points": 50}, "close_fraction": 0.5}},
        point_size=0.1,
    )
    assert spec is not None
    assert spec.trailing_rule is None
    assert spec.partial_close_rule.close_volume("buy", 100.0, 106.0, 0.10, 0.01) == pytest.approx(0.05)


def test_unknown_key_rejected():
    from simulator.framework.position_manager_spec_loader import load_position_change_spec

    with pytest.raises(ConfigError):
        load_position_change_spec(
            {"trailing": {"trigger_points": 1, "distance_points": 1, "bogus": 1}},
            point_size=0.1,
        )


def test_bad_granularity_rejected():
    from simulator.framework.position_manager_spec_loader import load_position_change_spec

    with pytest.raises(ConfigError):
        load_position_change_spec(
            {"trailing": {"granularity": "hour", "trigger_points": 1, "distance_points": 1}},
            point_size=0.1,
        )


def test_close_fraction_out_of_range_rejected():
    from simulator.framework.position_manager_spec_loader import load_position_change_spec

    with pytest.raises(ConfigError):
        load_position_change_spec(
            {"partial_close": {"trigger": {"profit_points": 1}, "close_fraction": 1.5}},
            point_size=0.1,
        )
    with pytest.raises(ConfigError):
        load_position_change_spec(
            {"partial_close": {"trigger": {"profit_points": 1}, "close_fraction": 0.0}},
            point_size=0.1,
        )
