"""TDD: usecase look-ahead 依存検証（詳細設計 §6 / §9.6・NFR-D4）。

assert_no_lookahead: σ̂/S/T/N の入力 week_id が全て target 週 w 未満であること。
混入注入（当週 RS を右辺へ）で LookaheadViolationError。
"""
from __future__ import annotations

import pytest

from simulator.usecase.run_weekly_segments import (
    LookaheadViolationError,
    assert_no_lookahead,
)


class TestAssertNoLookahead:
    def test_all_inputs_strictly_before_target_ok(self):
        # target "2024-W07" に対し入力は W05/W06（全て手前）
        assert_no_lookahead("2024-W07", ["2024-W05", "2024-W06"])

    def test_empty_inputs_ok(self):
        assert_no_lookahead("2024-W07", [])

    def test_current_week_input_raises(self):
        # 当週 RS を右辺に混入 → 違反
        with pytest.raises(LookaheadViolationError):
            assert_no_lookahead("2024-W07", ["2024-W06", "2024-W07"])

    def test_future_week_input_raises(self):
        with pytest.raises(LookaheadViolationError):
            assert_no_lookahead("2024-W07", ["2024-W08"])
