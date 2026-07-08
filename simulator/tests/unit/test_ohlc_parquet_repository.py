"""ParquetOHLCRepository（MarketDataPort 実装）テスト（cycle B / B2）。

pyarrow/pandas read_parquet で parquet→domain.Bar 列へ変換。例外翻訳は CSV と同方針
（OHLC違反→OHLCInvalidError・破損/IO→DataError）。
"""
from __future__ import annotations

import abc

import pandas as pd
import pytest

from simulator.domain.bar import Bar
from simulator.domain.exceptions import DataError, OHLCInvalidError
from simulator.usecase.ports import MarketDataPort


def _valid_df():
    return pd.DataFrame(
        {
            "time": [
                "2024-01-01T00:00:00",
                "2024-01-01T00:01:00",
            ],
            "open": [1.10, 1.15],
            "high": [1.20, 1.25],
            "low": [1.05, 1.10],
            "close": [1.15, 1.20],
            "volume": [100.0, 110.0],
            "spread": [2, 2],
        }
    )


def test_parquet_repository_is_market_data_port_subclass():
    from simulator.adapter.repository.ohlc_parquet import ParquetOHLCRepository

    assert issubclass(ParquetOHLCRepository, MarketDataPort)
    assert issubclass(MarketDataPort, abc.ABC)
    assert isinstance(ParquetOHLCRepository(), MarketDataPort)


def test_load_returns_list_of_domain_bars_with_matching_values(tmp_path):
    from simulator.adapter.repository.ohlc_parquet import ParquetOHLCRepository

    p = tmp_path / "ohlc.parquet"
    _valid_df().to_parquet(p)

    bars = ParquetOHLCRepository().load(p, None, None)

    assert isinstance(bars, list)
    assert len(bars) == 2
    assert all(isinstance(b, Bar) for b in bars)
    assert bars[1].close == 1.20
    assert bars[1].spread == 2


def test_load_raises_ohlc_invalid_error_when_high_below_low(tmp_path):
    from simulator.adapter.repository.ohlc_parquet import ParquetOHLCRepository

    df = _valid_df()
    df.loc[0, "high"] = 1.00  # high < low
    p = tmp_path / "bad.parquet"
    df.to_parquet(p)

    with pytest.raises(OHLCInvalidError):
        ParquetOHLCRepository().load(p, None, None)


def test_load_translates_unreadable_file_to_data_error(tmp_path):
    from simulator.adapter.repository.ohlc_parquet import ParquetOHLCRepository

    with pytest.raises(DataError):
        ParquetOHLCRepository().load(tmp_path / "nope.parquet", None, None)
