"""TickStorePort の marketdata 実装（ISSUE-091 🔴-2）。

正準ティック経路（marketdata.tick_m1 の day parquet・read-only）と DATA_DIR 単一基点を
compute から隔離する具象 gateway。挙動は従来の直 import と同一（列挙・読取とも等価委譲）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from marketdata import paths as _paths
from marketdata.tick_m1 import day_parquet_files


class MarketdataTickStore:
    """保存済み day parquet の列挙・読取・データ基点（TickStorePort 実装）。"""

    def day_files(self, lo_day: Any, hi_day: Any, *, symbol: str) -> "list[Path]":
        return day_parquet_files(lo_day, hi_day, symbol=symbol)

    def read_ticks(self, path: Path, columns: "Sequence[str]") -> Any:
        return pd.read_parquet(path, columns=list(columns))

    def data_dir(self) -> Path:
        return _paths.DATA_DIR
