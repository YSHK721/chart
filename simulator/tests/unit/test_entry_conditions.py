"""domain/entry_conditions.py の EntryConditions テスト（Phase 6 F-8・TBD-11）.

EntryConditions（Value Object・pandas/JSON 非依存）:
    matches(sample: Callable[[str,int], float]) -> bool
        条件は AND 連鎖（全真で成立）。厳密不等号（op ∈ {">","<"}）の生 float 比較。
    op ∈ {">","<"} 違反は構築時 ConfigError。shift < 0 も構築時 ConfigError。
    rhs は定数（number）または {indicator, shift}（IndicatorRef）。
    max_shift は lhs/rhs 双方の shift の最大（warmup 境界）。空条件は 0。
"""
from __future__ import annotations

import math

import pytest

from simulator.domain.exceptions import ConfigError


def _sample_from(table):
    """(name, shift) -> table[name][shift] を返すサンプラ（iloc[bar_index-shift] 相当）。"""
    return lambda name, shift: table[name][shift]


def test_all_true_and_chain_holds():
    # Arrange: 2 条件 AND・両方 true
    from simulator.domain.entry_conditions import Condition, EntryConditions

    conds = EntryConditions(
        [
            Condition(indicator="ema", shift=0, op=">", rhs=1.0),
            Condition(indicator="adx", shift=0, op=">", rhs=20.0),
        ]
    )
    sample = _sample_from({"ema": {0: 2.0}, "adx": {0: 25.0}})

    # Act / Assert
    assert conds.matches(sample) is True


def test_one_false_condition_fails_and_chain():
    # Arrange: 片方 false → AND 全体 false
    from simulator.domain.entry_conditions import Condition, EntryConditions

    conds = EntryConditions(
        [
            Condition(indicator="ema", shift=0, op=">", rhs=1.0),
            Condition(indicator="adx", shift=0, op=">", rhs=30.0),  # 25>30 は偽
        ]
    )
    sample = _sample_from({"ema": {0: 2.0}, "adx": {0: 25.0}})

    # Act / Assert
    assert conds.matches(sample) is False


def test_less_than_op_strict():
    # Arrange: op "<" の厳密不等号
    from simulator.domain.entry_conditions import Condition, EntryConditions

    conds = EntryConditions([Condition(indicator="rsi", shift=0, op="<", rhs=30.0)])

    # Act / Assert: 29<30 真 / 30<30 偽（厳密）
    assert conds.matches(_sample_from({"rsi": {0: 29.0}})) is True
    assert conds.matches(_sample_from({"rsi": {0: 30.0}})) is False


def test_greater_than_is_strict_at_boundary():
    # Arrange: op ">" は境界で偽（許容誤差なし）
    from simulator.domain.entry_conditions import Condition, EntryConditions

    conds = EntryConditions([Condition(indicator="ema", shift=0, op=">", rhs=5.0)])

    # Act / Assert
    assert conds.matches(_sample_from({"ema": {0: 5.0}})) is False


def test_shift_reads_past_bar():
    # Arrange: shift=1 は 1 本前を参照（[bar-1]）
    from simulator.domain.entry_conditions import Condition, EntryConditions

    conds = EntryConditions([Condition(indicator="close", shift=1, op=">", rhs=1.0)])

    # Act / Assert: shift 0=最新（0.5）は使わず shift1=過去（2.0）を読む
    assert conds.matches(_sample_from({"close": {0: 0.5, 1: 2.0}})) is True


def test_rhs_indicator_ref_compares_two_series():
    # Arrange: rhs が {indicator, shift}（指標同士の比較）
    from simulator.domain.entry_conditions import Condition, EntryConditions, IndicatorRef

    conds = EntryConditions(
        [Condition(indicator="ema", shift=0, op=">", rhs=IndicatorRef(indicator="close", shift=1))]
    )
    sample = _sample_from({"ema": {0: 3.0}, "close": {1: 2.0}})

    # Act / Assert: ema[0]=3 > close[1]=2 → 真
    assert conds.matches(sample) is True


def test_nan_lhs_yields_false():
    # Arrange: warmup で NaN 参照 → どの比較も偽（誤シグナル禁止）
    from simulator.domain.entry_conditions import Condition, EntryConditions

    conds = EntryConditions([Condition(indicator="ema", shift=0, op=">", rhs=-1.0)])

    # Act / Assert: NaN > -1.0 は False
    assert conds.matches(_sample_from({"ema": {0: float("nan")}})) is False


def test_empty_conditions_matches_true_but_is_falsey():
    # Arrange: 空条件は AND の空＝真だが、真偽値（__bool__）は偽（＝側が無効）
    from simulator.domain.entry_conditions import EntryConditions

    conds = EntryConditions([])

    # Act / Assert
    assert conds.matches(lambda n, s: 0.0) is True
    assert bool(conds) is False
    assert len(conds) == 0


def test_max_shift_is_max_over_lhs_and_rhs():
    # Arrange: lhs shift=1・rhs shift=3 → max_shift=3
    from simulator.domain.entry_conditions import Condition, EntryConditions, IndicatorRef

    conds = EntryConditions(
        [
            Condition(indicator="ema", shift=1, op=">", rhs=1.0),
            Condition(indicator="ema", shift=0, op="<", rhs=IndicatorRef(indicator="sma", shift=3)),
        ]
    )

    # Act / Assert
    assert conds.max_shift == 3


def test_empty_max_shift_is_zero():
    from simulator.domain.entry_conditions import EntryConditions

    assert EntryConditions([]).max_shift == 0


def test_unknown_op_raises_config_error():
    # Arrange / Act / Assert: TBD-11 — op は {">","<"} のみ。== は構築時 ConfigError
    from simulator.domain.entry_conditions import Condition, EntryConditions

    with pytest.raises(ConfigError):
        EntryConditions([Condition(indicator="ema", shift=0, op="==", rhs=1.0)])


def test_ge_op_raises_config_error():
    # Arrange / Act / Assert: >= も不可（TBD-11）
    from simulator.domain.entry_conditions import Condition, EntryConditions

    with pytest.raises(ConfigError):
        EntryConditions([Condition(indicator="ema", shift=0, op=">=", rhs=1.0)])


def test_negative_lhs_shift_raises_config_error():
    # Arrange / Act / Assert: shift < 0 は構築時 ConfigError
    from simulator.domain.entry_conditions import Condition, EntryConditions

    with pytest.raises(ConfigError):
        EntryConditions([Condition(indicator="ema", shift=-1, op=">", rhs=1.0)])


def test_negative_rhs_ref_shift_raises_config_error():
    # Arrange / Act / Assert: rhs 参照の shift < 0 も構築時 ConfigError
    from simulator.domain.entry_conditions import Condition, EntryConditions, IndicatorRef

    with pytest.raises(ConfigError):
        EntryConditions(
            [Condition(indicator="ema", shift=0, op=">", rhs=IndicatorRef(indicator="sma", shift=-2))]
        )
