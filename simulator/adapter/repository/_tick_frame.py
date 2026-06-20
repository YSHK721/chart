"""tick frame の adapter 内部ヘルパー（列検証・partition 列付与・日付述語構築）。

OHLC 用 ``_ohlc_frame`` とは別系統（tick は timestamp/bid/ask/last/volume の列構成・
hive partition <root>/<symbol>/year=/month=/day= を前提とする）。pandas を技術
ドライバとして本ファイル内に隔離する（usecase/domain にのみ論理依存）。

例外翻訳方針（CLEAN_ARCH §6）:
    TICK_COLUMNS 欠損   → MissingBarError
    timestamp 非昇順    → TimeOrderError
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from simulator.domain.exceptions import MissingBarError, TimeOrderError

# 設計の列定義（synth_ticks.TICK_COLUMNS と一致）。
TICK_COLUMNS = ("timestamp", "bid", "ask", "last", "volume")


def validate_tick_columns(df: pd.DataFrame) -> pd.DataFrame:
    """TICK_COLUMNS 必須列と timestamp 昇順を検証する（不正は内側例外へ翻訳）。"""
    missing = [c for c in TICK_COLUMNS if c not in df.columns]
    if missing:
        raise MissingBarError(
            f"必須列が不足しています: {missing}",
            context={"missing": missing, "columns": list(df.columns)},
        )

    ts = pd.to_datetime(df["timestamp"])
    if not ts.is_monotonic_increasing:
        raise TimeOrderError(
            "timestamp が昇順ではありません",
            context={"first": str(ts.iloc[0]), "last": str(ts.iloc[-1])},
        )
    return df


def with_partition_columns(df: pd.DataFrame) -> pd.DataFrame:
    """timestamp から year/month/day の hive partition 列を付与する。"""
    out = df.copy()
    ts = pd.to_datetime(out["timestamp"])
    out["year"] = ts.dt.year
    out["month"] = ts.dt.month
    out["day"] = ts.dt.day
    return out


def _date_predicate(start: datetime, end: datetime) -> list[tuple[int, int, int]]:
    """[start, end) 半開区間を覆う (year, month, day) を列挙する（端含む）。

    end がちょうど日境界 00:00:00 のときは end 当日を含めない（半開）。
    """
    start_day = datetime(start.year, start.month, start.day)
    # end の直前の瞬間が属する日まで列挙する（半開）。
    last_instant = end - timedelta(microseconds=1)
    end_day = datetime(last_instant.year, last_instant.month, last_instant.day)

    days: list[tuple[int, int, int]] = []
    cur = start_day
    while cur <= end_day:
        days.append((cur.year, cur.month, cur.day))
        cur += timedelta(days=1)
    return days
