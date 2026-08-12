"""framework/strategy_spec_loader.py の load_strategy_spec テスト（Phase 6 F-8）.

JSON dict（spec.strategy ブロック）→ (entry_long, entry_short) の EntryConditions を返す
（sizing_config_loader と対称）。未知比較子・shift 負値・未知キー・非マッピングは ConfigError。

spec スキーマ:
    strategy:
      entry_long:  [ {indicator, shift, op, rhs}, ... ]
      entry_short: [ ... ]
    rhs = number | { indicator, shift }
"""
from __future__ import annotations

import pytest

from simulator.domain.entry_conditions import EntryConditions, IndicatorRef
from simulator.domain.exceptions import ConfigError


def test_valid_spec_returns_entry_conditions_pair():
    # Arrange
    from simulator.framework.strategy_spec_loader import load_strategy_spec

    spec = {
        "entry_long": [
            {"indicator": "ema", "shift": 0, "op": ">", "rhs": 1.0},
            {"indicator": "adx", "shift": 1, "op": ">", "rhs": 20.0},
        ],
        "entry_short": [{"indicator": "rsi", "shift": 0, "op": "<", "rhs": 30.0}],
    }

    # Act
    entry_long, entry_short = load_strategy_spec(spec)

    # Assert
    assert isinstance(entry_long, EntryConditions)
    assert isinstance(entry_short, EntryConditions)
    assert len(entry_long) == 2 and len(entry_short) == 1
    # 条件が正しく効く（AND 連鎖）
    assert entry_long.matches(lambda n, s: {"ema": 2.0, "adx": 25.0}[n]) is True


def test_constant_rhs_is_number():
    from simulator.framework.strategy_spec_loader import load_strategy_spec

    entry_long, _ = load_strategy_spec(
        {"entry_long": [{"indicator": "ema", "shift": 0, "op": ">", "rhs": 5.0}]}
    )
    cond = entry_long.conditions[0]
    assert cond.rhs == 5.0
    assert not isinstance(cond.rhs, IndicatorRef)


def test_indicator_ref_rhs_is_parsed():
    from simulator.framework.strategy_spec_loader import load_strategy_spec

    entry_long, _ = load_strategy_spec(
        {
            "entry_long": [
                {
                    "indicator": "ema",
                    "shift": 0,
                    "op": ">",
                    "rhs": {"indicator": "close", "shift": 1},
                }
            ]
        }
    )
    cond = entry_long.conditions[0]
    assert isinstance(cond.rhs, IndicatorRef)
    assert cond.rhs.indicator == "close" and cond.rhs.shift == 1


def test_missing_side_defaults_to_empty():
    from simulator.framework.strategy_spec_loader import load_strategy_spec

    entry_long, entry_short = load_strategy_spec(
        {"entry_long": [{"indicator": "ema", "shift": 0, "op": ">", "rhs": 1.0}]}
    )
    assert len(entry_long) == 1
    assert len(entry_short) == 0 and bool(entry_short) is False


def test_unknown_op_raises_config_error():
    from simulator.framework.strategy_spec_loader import load_strategy_spec

    with pytest.raises(ConfigError):
        load_strategy_spec(
            {"entry_long": [{"indicator": "ema", "shift": 0, "op": "==", "rhs": 1.0}]}
        )


def test_ge_op_raises_config_error():
    from simulator.framework.strategy_spec_loader import load_strategy_spec

    with pytest.raises(ConfigError):
        load_strategy_spec(
            {"entry_long": [{"indicator": "ema", "shift": 0, "op": ">=", "rhs": 1.0}]}
        )


def test_negative_shift_raises_config_error():
    from simulator.framework.strategy_spec_loader import load_strategy_spec

    with pytest.raises(ConfigError):
        load_strategy_spec(
            {"entry_long": [{"indicator": "ema", "shift": -1, "op": ">", "rhs": 1.0}]}
        )


def test_unknown_key_in_condition_raises_config_error():
    # extra="forbid": silent drop で「設定したつもりで効かない」を作らない
    from simulator.framework.strategy_spec_loader import load_strategy_spec

    with pytest.raises(ConfigError):
        load_strategy_spec(
            {"entry_long": [{"indicator": "ema", "shift": 0, "op": ">", "rhs": 1.0, "foo": 1}]}
        )


def test_non_mapping_source_raises_config_error():
    from simulator.framework.strategy_spec_loader import load_strategy_spec

    with pytest.raises(ConfigError):
        load_strategy_spec(["not", "a", "mapping"])


def test_negative_rhs_ref_shift_raises_config_error():
    from simulator.framework.strategy_spec_loader import load_strategy_spec

    with pytest.raises(ConfigError):
        load_strategy_spec(
            {
                "entry_long": [
                    {
                        "indicator": "ema",
                        "shift": 0,
                        "op": ">",
                        "rhs": {"indicator": "close", "shift": -2},
                    }
                ]
            }
        )
