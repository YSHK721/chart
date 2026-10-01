"""tick → M1 CSV（spread 列）→ domain.Bar.spread の往復（ISSUE-511 段階 2）。

``build_m1_from_ticks(point=P)`` が書いた CSV を ``MarketdataCsvOHLCRepository`` が読み、
分内最小の気配幅（整数 points）がそのまま Bar.spread に届くことを固定する。
データは tmp_path の tick 木のみ（既存データに触れない）。point はテストが注入する。
"""
from __future__ import annotations

import pandas as pd

from marketdata import tick_m1
from simulator.adapter.repository.ohlc_marketdata_csv import MarketdataCsvOHLCRepository

_POINT = 0.1


def test_a_built_m1_with_a_point_reaches_bar_spread(tmp_path):
    # Arrange: 1 日・2 分（分内最小幅 7.12 → 71・7.18 → 72）。
    day = pd.Timestamp("2026-09-01")
    rows = [
        ("2026-09-01 00:00:05", 63057.6, 63067.6),
        ("2026-09-01 00:00:30", 63057.6, 63064.72),
        ("2026-09-01 00:00:55", 63058.0, 63066.0),
        ("2026-09-01 00:01:10", 63059.0, 63066.18),
        ("2026-09-01 00:01:40", 63059.5, 63069.5),
    ]
    ticks = pd.DataFrame(
        {
            "timestamp": pd.to_datetime([r[0] for r in rows]).tz_localize("UTC"),
            "bidPrice": [r[1] for r in rows],
            "askPrice": [r[2] for r in rows],
        }
    )
    parquet = tick_m1.day_parquet_path(day, symbol="SPY225", data_dir=tmp_path)
    parquet.parent.mkdir(parents=True, exist_ok=True)
    ticks.to_parquet(parquet)

    # Act
    out = tick_m1.build_m1_from_ticks(
        day, day, symbol="SPY225", ref="spread_round_trip", data_dir=tmp_path,
        price_basis=tick_m1.PRICE_BASIS_BID, point=_POINT,
    )
    bars = MarketdataCsvOHLCRepository().load(str(out))

    # Assert
    assert [b.spread for b in bars] == [71, 72]
    assert [b.close for b in bars] == [63058.0, 63059.5]  # bid 基準の終値
