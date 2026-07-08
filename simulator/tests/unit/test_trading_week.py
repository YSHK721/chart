"""TDD: domain/trading_week.py（詳細設計 §3.1 / §9.1）。

TradingWeek 不変条件・week_id_of/weekday_of/same_trading_day の UTC 規約。
"""
from __future__ import annotations

import dataclasses

import pytest

from simulator.domain.trading_week import (
    TradingWeek,
    same_trading_day,
    week_id_of,
    weekday_of,
)


class TestTradingWeekValid:
    def test_constructs_with_consistent_times(self):
        # Arrange / Act
        wk = TradingWeek(
            week_id="2024-W07",
            first_trading_time=100,
            last_trading_time=300,
            trading_times=(100, 200, 300),
        )
        # Assert
        assert wk.week_id == "2024-W07"
        assert wk.trading_times == (100, 200, 300)
        assert wk.event_flag is False

    def test_single_day_week_is_valid(self):
        # 境界：trading_times 1 件
        wk = TradingWeek("2024-W07", 100, 100, (100,))
        assert wk.first_trading_time == wk.last_trading_time == 100

    def test_is_frozen(self):
        wk = TradingWeek("2024-W07", 100, 300, (100, 300))
        with pytest.raises(dataclasses.FrozenInstanceError):
            wk.week_id = "x"  # type: ignore[misc]


class TestTradingWeekInvalid:
    def test_empty_trading_times_raises(self):
        with pytest.raises(ValueError):
            TradingWeek("2024-W07", 100, 100, ())

    def test_first_mismatch_raises(self):
        with pytest.raises(ValueError):
            TradingWeek("2024-W07", 999, 300, (100, 200, 300))

    def test_last_mismatch_raises(self):
        with pytest.raises(ValueError):
            TradingWeek("2024-W07", 100, 999, (100, 200, 300))

    def test_non_ascending_raises(self):
        with pytest.raises(ValueError):
            TradingWeek("2024-W07", 100, 300, (100, 300, 200))

    def test_duplicate_times_raises(self):
        with pytest.raises(ValueError):
            TradingWeek("2024-W07", 100, 300, (100, 200, 200, 300))


class TestWeekIdOf:
    def test_known_epoch_to_iso_week(self):
        # 2024-02-14 12:00 UTC は ISO 2024-W07（水曜）。
        ts = 1_707_912_000  # 2024-02-14T12:00:00Z
        assert week_id_of(ts) == "2024-W07"

    def test_weekday_monday_is_zero(self):
        # 2024-02-12 は月曜（Mon=0）。
        ts = 1_707_739_200  # 2024-02-12T12:00:00Z (Mon)
        assert weekday_of(ts) == 0

    def test_weekday_wednesday(self):
        ts = 1_707_912_000  # 2024-02-14 (Wed)
        assert weekday_of(ts) == 2


class TestSameTradingDay:
    def test_same_day_true(self):
        a = 1_707_912_000  # 2024-02-14T12:00Z
        b = 1_707_915_600  # 2024-02-14T13:00Z
        assert same_trading_day(a, b) is True

    def test_different_day_false(self):
        a = 1_707_912_000  # 02-14
        b = 1_707_998_400  # 02-15
        assert same_trading_day(a, b) is False
