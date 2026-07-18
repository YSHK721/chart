"""TickStorePort の marketdata 実装（ISSUE-091 🔴-2）。

正準ティック経路（marketdata.tick_m1 の day parquet・read-only）と DATA_DIR 単一基点を
compute から隔離する具象 gateway。挙動は従来の直 import と同一（列挙・読取とも等価委譲）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence, Tuple

import numpy as np
import pandas as pd

from marketdata import paths as _paths
from marketdata.tick_m1 import day_parquet_files

_EMPTY_SECS = np.array([], dtype=np.int64)
_EMPTY_MIDS = np.array([], dtype=np.float64)


class MarketdataTickStore:
    """保存済み day parquet の列挙・読取・データ基点・窓ティック復号（TickStorePort 実装）。"""

    def day_files(self, lo_day: Any, hi_day: Any, *, symbol: str) -> "list[Path]":
        return day_parquet_files(lo_day, hi_day, symbol=symbol)

    def read_ticks(self, path: Path, columns: "Sequence[str]") -> Any:
        return pd.read_parquet(path, columns=list(columns))

    def load_window_ticks(
        self,
        symbol: str,
        start: Any,
        end: Any,
        *,
        columns: "Sequence[str]",
        outlier_frac: float,
    ) -> "Tuple[Any, Any]":
        """``[start, end)`` の実ティックを ``(secs:int64, mids:float64)`` で返す（ISSUE-133 SRP: 旧
        ``market_profile_dwell._load_window_ticks`` の tick I/O 解析を gateway へ移設・挙動不変）。

        day parquet を列挙・読取 → concat → tz 除去し UTC 秒 int64 へ → 窓 ``[start,end)`` マスク →
        mid=(bid+ask)/2 → 窓内 mid 中央値 ±``outlier_frac`` の外れ値除去 → secs で安定ソート。空なら空配列。
        """
        s, e = int(start), int(end)
        lo_day = pd.Timestamp(s, unit="s").normalize()
        hi_day = pd.Timestamp(max(s, e - 1), unit="s").normalize()
        files = self.day_files(lo_day, hi_day, symbol=symbol)
        if not files:
            return _EMPTY_SECS, _EMPTY_MIDS
        frames = [self.read_ticks(p, columns) for p in files]
        tdf = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]

        ts = pd.to_datetime(tdf["timestamp"])
        if getattr(ts.dt, "tz", None) is not None:
            ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
        secs = ts.to_numpy().astype("datetime64[s]").astype("int64")
        win = (secs >= s) & (secs < e)
        secs = secs[win]
        mids = ((tdf["bidPrice"].to_numpy(dtype="float64") + tdf["askPrice"].to_numpy(dtype="float64"))
                / 2.0)[win]
        if len(mids):
            m = float(np.median(mids))
            if m > 0:
                keep = np.abs(mids / m - 1.0) <= outlier_frac
                secs, mids = secs[keep], mids[keep]
        order = np.argsort(secs, kind="stable")
        return secs[order].astype(np.int64), mids[order].astype(np.float64)

    def data_dir(self) -> Path:
        return _paths.DATA_DIR
