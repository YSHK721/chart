"""§6.1 到達判定（交差）と §6.2 到達時刻（定義 A）の唯一定義を固定する。

定義 A: 到達時刻 = **現在の到達状態が始まった時刻**（`reached` が現在と同値である連続区間の
始端）。窓内の最古の到達（定義 B）ではない。観測値が水準を離れて戻ると、戻った時点が
新しい始端になる。
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
    def test_the_reach_time_is_the_start_of_the_current_run(self) -> None:
        # Arrange: 到達は t=30 で始まり、以後継続している。
        times = [10, 20, 30, 40]
        values = [90.0, 95.0, 105.0, 107.0]
        levels = [100.0, 100.0, 100.0, 100.0]

        state = reach_state(times, values, levels, side=LevelSide.ABOVE)

        assert state == ReachState(reached=True, since_time=30, truncated=False)

    def test_leaving_and_returning_restarts_the_reach_time(self) -> None:
        """定義 A の核心。定義 B（窓内最古）なら 20 になる。"""
        times = [10, 20, 30, 40, 50]
        values = [90.0, 105.0, 95.0, 105.0, 106.0]
        levels = [100.0] * 5

        state = reach_state(times, values, levels, side=LevelSide.ABOVE)

        assert state.reached is True
        assert state.since_time == 40

    def test_a_not_reached_state_also_carries_the_start_of_its_run(self) -> None:
        """`reached_now` が False の連続区間にも始端がある（§6.2 の式は両値に適用される）。"""
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

    def test_a_run_that_extends_to_the_first_sample_is_marked_truncated(self) -> None:
        """始端が履歴の先頭なら「その時刻に始まった」と断定できない（無言の縮退を作らない）。"""
        times = [10, 20, 30]
        values = [105.0, 106.0, 107.0]
        levels = [100.0] * 3

        state = reach_state(times, values, levels, side=LevelSide.ABOVE)

        assert state.reached is True
        assert state.since_time == 10
        assert state.truncated is True

    def test_an_undecidable_sample_breaks_the_run(self) -> None:
        """境界値: 途中の NaH（水準なし）は連続区間を切る。"""
        times = [10, 20, 30, 40]
        values = [105.0, float("nan"), 105.0, 106.0]
        levels = [100.0] * 4

        state = reach_state(times, values, levels, side=LevelSide.ABOVE)

        assert state.reached is True
        assert state.since_time == 30
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
