"""TDD: adapter/repository/vol_band_parquet.py（詳細設計 §5.3 / §8.1）。

save_all → get round-trip。estimable=False は sigma null 保持。出力先検証。
"""
from __future__ import annotations

import pytest

from simulator.adapter.repository.vol_band_parquet import (
    OutputPathError,
    VolBandParquetRepo,
)
from simulator.domain.variance_forecast import VarianceForecast


class TestRoundTrip:
    def test_save_all_then_get(self, tmp_path):
        repo = VolBandParquetRepo(out_dir=tmp_path)
        fcs = [
            VarianceForecast("2024-W07", 0.025, 0.020, 0.018, estimable=True),
            VarianceForecast.no_trade("2024-W08", sigma_total_prev=0.019),
        ]
        repo.save_all(fcs)
        a = repo.get("2024-W07")
        b = repo.get("2024-W08")
        assert a is not None and a.estimable is True
        assert abs(a.sigma_plus - 0.025) < 1e-12
        assert b is not None and b.estimable is False
        assert b.sigma_plus is None

    def test_get_missing_returns_none(self, tmp_path):
        repo = VolBandParquetRepo(out_dir=tmp_path)
        repo.save_all([VarianceForecast("2024-W07", 0.025, 0.020, 0.018, estimable=True)])
        assert repo.get("2099-W01") is None


class TestOutputGuard:
    def test_rejects_marketdata_prefix(self):
        from pathlib import Path
        repo = VolBandParquetRepo(out_dir=Path("marketdata"))
        with pytest.raises(OutputPathError):
            repo.save_all([VarianceForecast("2024-W07", 0.025, 0.020, 0.018, estimable=True)])
