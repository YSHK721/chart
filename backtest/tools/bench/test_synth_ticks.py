"""Invariant tests for the synthetic tick generator (bench PoC).

These guard only the *generator* — the foundation every benchmark scenario
relies on. The benchmark runner is throwaway and intentionally untested.

Run: python -m pytest backtest/tools/bench/test_synth_ticks.py -q
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from backtest.tools.bench.synth_ticks import (
    TICK_COLUMNS,
    TickGenConfig,
    generate_ticks,
)


def _small_config() -> TickGenConfig:
    # 3 days x 1000 ticks = 3000 rows: fast, exercises all invariants.
    return TickGenConfig(start_date=date(2025, 1, 1), days=3, ticks_per_day=1000)


def test_schema_matches_spec():
    # Arrange
    cfg = _small_config()
    # Act
    df = generate_ticks(cfg)
    # Assert
    assert list(df.columns) == TICK_COLUMNS


def test_row_count_equals_days_times_ticks_per_day():
    # Arrange
    cfg = _small_config()
    # Act
    df = generate_ticks(cfg)
    # Assert
    assert len(df) == cfg.total_rows() == 3000


def test_deterministic_same_seed_same_output():
    # Arrange
    cfg = _small_config()
    # Act
    df1 = generate_ticks(cfg)
    df2 = generate_ticks(cfg)
    # Assert
    pd.testing.assert_frame_equal(df1, df2)


def test_different_seed_changes_prices():
    # Arrange
    cfg_a = _small_config()
    cfg_b = TickGenConfig(
        start_date=date(2025, 1, 1), days=3, ticks_per_day=1000, seed=999
    )
    # Act
    df_a = generate_ticks(cfg_a)
    df_b = generate_ticks(cfg_b)
    # Assert
    assert not df_a["last"].equals(df_b["last"])


def test_timestamp_is_millisecond_precision_datetime():
    # Arrange
    cfg = _small_config()
    # Act
    df = generate_ticks(cfg)
    # Assert
    assert df["timestamp"].dtype == "datetime64[ms]"


def test_timestamps_are_non_decreasing():
    # Arrange
    cfg = _small_config()
    # Act
    df = generate_ticks(cfg)
    # Assert
    assert df["timestamp"].is_monotonic_increasing


def test_dates_are_contiguous_across_days():
    # Arrange
    cfg = _small_config()
    # Act
    df = generate_ticks(cfg)
    unique_days = sorted(pd.to_datetime(df["timestamp"]).dt.normalize().unique())
    # Assert
    assert len(unique_days) == cfg.days
    diffs = {(unique_days[i + 1] - unique_days[i]).days for i in range(len(unique_days) - 1)}
    assert diffs == {1}


def test_ask_is_bid_plus_spread():
    # Arrange
    cfg = _small_config()
    # Act
    df = generate_ticks(cfg)
    # Assert
    assert ((df["ask"] - df["bid"]).round(6) == cfg.spread).all()


def test_volume_is_positive_integer():
    # Arrange
    cfg = _small_config()
    # Act
    df = generate_ticks(cfg)
    # Assert
    assert (df["volume"] > 0).all()
    assert df["volume"].dtype.kind in ("i", "u")
