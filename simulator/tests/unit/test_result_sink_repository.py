"""JsonResultRepository / ParquetResultRepository（ResultSinkPort 実装）テスト（cycle B / B3）。

save_trades / save_stats / save_report の round-trip（保存→再読込で値一致）と
I/O 例外→BacktestError(context 付与) 翻訳を検証する。
"""
from __future__ import annotations

import abc
import json

import pandas as pd
import pytest

from simulator.domain.exceptions import BacktestError
from simulator.usecase.ports import ResultSinkPort


def _stats():
    return {"STAT_PROFIT": 12345.67, "STAT_TRADES": 42, "STAT_PROFIT_FACTOR": 1.5}


def _trades_df():
    return pd.DataFrame(
        {
            "side": ["buy", "sell"],
            "entry_price": [1.10, 1.20],
            "exit_price": [1.15, 1.18],
            "profit": [50.0, -20.0],
        }
    )


# ---- JsonResultRepository ----

def test_json_result_repository_is_result_sink_port_subclass():
    from simulator.adapter.repository.result_sink import JsonResultRepository

    assert issubclass(JsonResultRepository, ResultSinkPort)
    assert issubclass(ResultSinkPort, abc.ABC)
    assert isinstance(JsonResultRepository(), ResultSinkPort)


def test_json_save_stats_round_trip(tmp_path):
    from simulator.adapter.repository.result_sink import JsonResultRepository

    p = tmp_path / "stats.json"
    JsonResultRepository().save_stats(_stats(), p)

    loaded = json.loads(p.read_text(encoding="utf-8"))
    assert loaded["STAT_PROFIT"] == 12345.67
    assert loaded["STAT_TRADES"] == 42


def test_json_save_trades_round_trip(tmp_path):
    from simulator.adapter.repository.result_sink import JsonResultRepository

    p = tmp_path / "trades.json"
    JsonResultRepository().save_trades(_trades_df(), p)

    reloaded = pd.read_json(p)
    assert list(reloaded["side"]) == ["buy", "sell"]
    assert reloaded["profit"].tolist() == [50.0, -20.0]


def test_json_save_report_writes_html_text(tmp_path):
    from simulator.adapter.repository.result_sink import JsonResultRepository

    p = tmp_path / "report.html"
    JsonResultRepository().save_report("<html>R</html>", p)

    assert p.read_text(encoding="utf-8") == "<html>R</html>"


def test_json_save_stats_translates_io_error_to_backtest_error_with_context():
    from simulator.adapter.repository.result_sink import JsonResultRepository

    # 存在しないディレクトリ配下 → OSError を BacktestError へ翻訳
    with pytest.raises(BacktestError) as ei:
        JsonResultRepository().save_stats(_stats(), "/nonexistent_dir_xyz/stats.json")
    assert ei.value.context  # context 付与


# ---- ParquetResultRepository ----

def test_parquet_result_repository_is_result_sink_port_subclass():
    from simulator.adapter.repository.result_sink import ParquetResultRepository

    assert issubclass(ParquetResultRepository, ResultSinkPort)
    assert isinstance(ParquetResultRepository(), ResultSinkPort)


def test_parquet_save_trades_round_trip(tmp_path):
    from simulator.adapter.repository.result_sink import ParquetResultRepository

    p = tmp_path / "trades.parquet"
    ParquetResultRepository().save_trades(_trades_df(), p)

    reloaded = pd.read_parquet(p)
    assert list(reloaded["side"]) == ["buy", "sell"]
    assert reloaded["profit"].tolist() == [50.0, -20.0]


def test_parquet_save_trades_translates_io_error_to_backtest_error():
    from simulator.adapter.repository.result_sink import ParquetResultRepository

    with pytest.raises(BacktestError):
        ParquetResultRepository().save_trades(
            _trades_df(), "/nonexistent_dir_xyz/trades.parquet"
        )
