"""§6.1 到達判定（交差）と §6.2 到達時刻（定義 C＝最初の接点）の唯一定義を固定する。

定義 C: 到達時刻 = **現在の状態が履歴で最初に現れた時刻**（依頼者指示 2026-08-31）。
途中で観測値が水準を離れて戻っても起点は動かない（定義 A「連続区間の始端」は反転のたびに
若返るため置換された）。
"""
from __future__ import annotations

import pytest

from dashboard_ui.domain.reach import LevelSide, ReachState, is_reached, reach_state


class TestIsReached:
    def test_above_level_is_reached_when_the_value_is_at_or_over_it(self) -> None:
        assert is_reached(110.0, 100.0, LevelSide.ABOVE) is True
        assert is_reached(99.0, 100.0, LevelSide.ABOVE) is False

    def test_below_level_is_reached_when_the_value_is_at_or_under_it(self) -> None:
        assert is_reached(90.0, 100.0, LevelSide.BELOW) is True
        assert is_reached(101.0, 100.0, LevelSide.BELOW) is False

    def test_equality_counts_as_reached_on_both_sides(self) -> None:
        """境界値: 現在値と水準が同値（§13.1 の `high >= v` / `low <= v` と同一規約）。"""
        assert is_reached(100.0, 100.0, LevelSide.ABOVE) is True
        assert is_reached(100.0, 100.0, LevelSide.BELOW) is True

    def test_numpy_inputs_yield_plain_python_booleans(self) -> None:
        """numpy スカラを渡すと `np.bool_` が漏れ、`is True` 判定と直列化が壊れる。"""
        import numpy as np

        result = is_reached(np.float64(110.0), np.float64(100.0), LevelSide.ABOVE)

        assert result is True
        assert type(result) is bool

    def test_a_non_finite_value_or_level_is_undecidable(self) -> None:
        assert is_reached(float("nan"), 100.0, LevelSide.ABOVE) is None
        assert is_reached(100.0, float("nan"), LevelSide.ABOVE) is None


class TestReachState:
    def test_the_reach_time_is_the_first_contact(self) -> None:
        # Arrange: 到達は t=30 で始まり、以後継続している。
        times = [10, 20, 30, 40]
        values = [90.0, 95.0, 105.0, 107.0]
        levels = [100.0, 100.0, 100.0, 100.0]

        state = reach_state(times, values, levels, side=LevelSide.ABOVE)

        assert state == ReachState(reached=True, since_time=30, truncated=False)

    def test_leaving_and_returning_keeps_the_first_contact(self) -> None:
        """定義 C の核心。定義 A（連続区間の始端）なら 40 に若返る。"""
        times = [10, 20, 30, 40, 50]
        values = [90.0, 105.0, 95.0, 105.0, 106.0]
        levels = [100.0] * 5

        state = reach_state(times, values, levels, side=LevelSide.ABOVE)

        assert state.reached is True
        assert state.since_time == 20
        assert state.truncated is False

    def test_a_not_reached_state_also_carries_its_first_occurrence(self) -> None:
        """`reached_now` が False にも最初の時刻がある（§6.2 の式は両値に適用される）。"""
        times = [10, 20, 30]
        values = [105.0, 95.0, 96.0]
        levels = [100.0] * 3

        state = reach_state(times, values, levels, side=LevelSide.ABOVE)

        assert state.reached is False
        assert state.since_time == 20

    def test_the_level_moving_can_flip_the_state_without_the_value_moving(self) -> None:
        """§6.1: 水準はバーごとに動くため固定値比較では判定できない。"""
        times = [10, 20, 30]
        values = [100.0, 100.0, 100.0]
        levels = [90.0, 90.0, 110.0]

        state = reach_state(times, values, levels, side=LevelSide.ABOVE)

        assert state.reached is False
        assert state.since_time == 30

    def test_below_side_tracks_the_downward_reach(self) -> None:
        times = [10, 20, 30]
        values = [110.0, 95.0, 94.0]
        levels = [100.0] * 3

        state = reach_state(times, values, levels, side=LevelSide.BELOW)

        assert state == ReachState(reached=True, since_time=20, truncated=False)

    def test_a_first_contact_at_the_first_sample_is_marked_truncated(self) -> None:
        """最初の接点が履歴の先頭なら「その時刻に始まった」と断定できない（無言の縮退を作らない）。"""
        times = [10, 20, 30]
        values = [105.0, 106.0, 107.0]
        levels = [100.0] * 3

        state = reach_state(times, values, levels, side=LevelSide.ABOVE)

        assert state.reached is True
        assert state.since_time == 10
        assert state.truncated is True

    def test_an_undecidable_gap_does_not_erase_an_earlier_first_contact(self) -> None:
        """境界値: 途中の NaN（水準なし）を挟んでも最初の接点は動かない（定義 C）。"""
        times = [10, 20, 30, 40]
        values = [105.0, float("nan"), 105.0, 106.0]
        levels = [100.0] * 4

        state = reach_state(times, values, levels, side=LevelSide.ABOVE)

        assert state.reached is True
        assert state.since_time == 10
        assert state.truncated is True

    def test_an_opposite_state_before_the_first_contact_certifies_it(self) -> None:
        """最初の接点より前に反対の状態を見届けていれば truncated ではない。"""
        times = [10, 20, 30, 40]
        values = [95.0, 105.0, 95.0, 105.0]
        levels = [100.0] * 4

        state = reach_state(times, values, levels, side=LevelSide.ABOVE)

        assert state.reached is True
        assert state.since_time == 20
        assert state.truncated is False

    def test_an_undecidable_latest_sample_yields_an_unknown_state(self) -> None:
        times = [10, 20]
        values = [105.0, 106.0]
        levels = [100.0, float("nan")]

        state = reach_state(times, values, levels, side=LevelSide.ABOVE)

        assert state.reached is None
        assert state.since_time is None

    def test_an_empty_history_yields_an_unknown_state(self) -> None:
        state = reach_state([], [], [], side=LevelSide.ABOVE)

        assert state == ReachState(reached=None, since_time=None, truncated=False)

    def test_mismatched_lengths_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            reach_state([10, 20], [1.0], [1.0, 2.0], side=LevelSide.ABOVE)
