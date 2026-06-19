"""Deterministic synthetic tick generator for the tick-loading benchmark PoC.

This module is the *only* part of the bench PoC guarded by tests, because its
output (row count, determinism, schema, contiguous dates) is the foundation
every benchmark scenario relies on. The benchmark runner itself (bench_run.py)
is throwaway measurement code.

Spec (from approved PoC instruction):
- columns: timestamp (ms precision) / bid / ask / last / volume
- ~1 year of data, intraday density ~ a few ticks/sec during sessions
- total ~3M..10M rows, day-contiguous so files can be split per day
- deterministic given a fixed seed
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

TICK_COLUMNS = ["timestamp", "bid", "ask", "last", "volume"]


@dataclass(frozen=True)
class TickGenConfig:
    """Configuration for one synthetic-tick generation run."""

    start_date: date
    days: int
    ticks_per_day: int
    seed: int = 20260619
    session_start_hour: int = 0
    session_hours: int = 22  # JP225 index CFD trades ~22h/day
    base_price: float = 39000.0  # JP225-ish level
    spread: float = 5.0

    def total_rows(self) -> int:
        return self.days * self.ticks_per_day


def generate_ticks(config: TickGenConfig) -> pd.DataFrame:
    """Generate a deterministic synthetic tick DataFrame for the given config.

    Returns columns TICK_COLUMNS with:
    - timestamp: ms-precision UTC datetimes, monotonically non-decreasing,
      spread across `days` contiguous calendar days within a daily session window
    - bid/ask/last: random walk around base_price, ask = bid + spread
    - volume: small positive integers
    """
    rng = np.random.default_rng(config.seed)
    total = config.total_rows()

    # Price random walk (deterministic via seeded rng).
    steps = rng.normal(0.0, 2.0, size=total).cumsum()
    last = config.base_price + steps
    bid = last - config.spread / 2.0
    ask = bid + config.spread
    volume = rng.integers(1, 10, size=total)

    # Timestamps: for each day, spread ticks_per_day across the session window.
    session_span_ms = config.session_hours * 3600 * 1000
    timestamps = np.empty(total, dtype="datetime64[ms]")
    tpd = config.ticks_per_day
    for d in range(config.days):
        day0 = datetime(
            config.start_date.year,
            config.start_date.month,
            config.start_date.day,
            tzinfo=timezone.utc,
        ) + timedelta(days=d, hours=config.session_start_hour)
        day0_ms = np.datetime64(day0.replace(tzinfo=None), "ms")
        # Evenly spaced offsets within the session window, deterministic.
        offsets = (np.arange(tpd, dtype=np.int64) * session_span_ms) // tpd
        timestamps[d * tpd : (d + 1) * tpd] = day0_ms + offsets.astype(
            "timedelta64[ms]"
        )

    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "bid": bid.astype(np.float64),
            "ask": ask.astype(np.float64),
            "last": last.astype(np.float64),
            "volume": volume.astype(np.int32),
        }
    )
