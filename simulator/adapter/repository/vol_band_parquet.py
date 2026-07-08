"""VolBandRepositoryPort 実装：Parquet 永続（詳細設計 §5.3 / §8.1/§8.3）。

pandas は本ファイルに局所化（DI-4）。出力先は禁止プレフィクス（marketdata/・
tests/fixtures/・tests/confirmation/）を拒否し、CLI 指定の OUT 配下のみ許可する
（NFR-S2・C1・既存データ非波及）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pandas as pd

from simulator.domain.variance_forecast import VarianceForecast

_FORBIDDEN_PREFIXES = ("marketdata", "simulator/tests/fixtures", "simulator/tests/confirmation")


class OutputPathError(Exception):
    """禁止プレフィクスへの書き込み試行（既存データ非波及違反）。"""


def _assert_out_path(path: Path) -> None:
    s = str(path).replace("\\", "/")
    for pref in _FORBIDDEN_PREFIXES:
        if s == pref or s.startswith(pref + "/") or f"/{pref}/" in s or s.startswith(pref):
            raise OutputPathError(f"禁止プレフィクスへの出力: {path}")


def _to_row(f: VarianceForecast) -> dict:
    return {
        "week_id": f.week_id,
        "sigma_plus": f.sigma_plus,
        "sigma_minus": f.sigma_minus,
        "sigma_total_prev": f.sigma_total_prev,
        "estimable": bool(f.estimable),
    }


def _from_row(row) -> VarianceForecast:
    def _f(v):
        return None if pd.isna(v) else float(v)
    return VarianceForecast(
        week_id=str(row["week_id"]),
        sigma_plus=_f(row["sigma_plus"]),
        sigma_minus=_f(row["sigma_minus"]),
        sigma_total_prev=_f(row["sigma_total_prev"]),
        estimable=bool(row["estimable"]),
    )


class VolBandParquetRepo:
    def __init__(self, out_dir: Path) -> None:
        self._dir = Path(out_dir)
        self._path = self._dir / "vol_band_forecasts.parquet"

    def save(self, forecast: VarianceForecast) -> None:
        self.save_all([forecast])

    def save_all(self, forecasts: "Sequence[VarianceForecast]") -> None:
        _assert_out_path(self._path)
        self._dir.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame([_to_row(f) for f in forecasts])
        df.to_parquet(self._path, index=False)

    def get(self, week_id: str) -> "VarianceForecast | None":
        if not self._path.exists():
            return None
        df = pd.read_parquet(self._path)
        row = df[df.week_id == week_id]
        return _from_row(row.iloc[0]) if len(row) else None

    def all_week_ids(self) -> "tuple[str, ...]":
        if not self._path.exists():
            return ()
        df = pd.read_parquet(self._path)
        return tuple(str(w) for w in df["week_id"].tolist())
