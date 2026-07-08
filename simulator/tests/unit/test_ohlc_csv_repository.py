"""CsvOHLCRepository（MarketDataPort 実装）テスト（cycle B / B1）。

DESIGN §3 価格データ形式（列: open/high/low/close/volume/spread・OHLC 整合・時刻昇順）。
CSV→domain.Bar 列へ変換。OHLCFrame 型は ports.py に未定義のため list[Bar] を返す。
外側（pandas/IO）例外を内側ドメイン例外へ翻訳（CLEAN_ARCH §6・逆向き漏出禁止）。
"""
from __future__ import annotations

import abc

import pytest

from simulator.domain.bar import Bar
from simulator.domain.exceptions import (
    DataError,
    MissingBarError,
    OHLCInvalidError,
    TimeOrderError,
)
from simulator.usecase.ports import MarketDataPort


def _write_csv(path, rows, header="time,open,high,low,close,volume,spread"):
    lines = [header] + [",".join(str(c) for c in r) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _valid_rows():
    # time, open, high, low, close, volume, spread（昇順・OHLC 整合）
    return [
        ("2024-01-01T00:00:00", 1.10, 1.20, 1.05, 1.15, 100, 2),
        ("2024-01-01T00:01:00", 1.15, 1.25, 1.10, 1.20, 110, 2),
        ("2024-01-01T00:02:00", 1.20, 1.30, 1.18, 1.28, 120, 2),
    ]


def test_csv_repository_is_market_data_port_subclass():
    from simulator.adapter.repository.ohlc_csv import CsvOHLCRepository

    assert issubclass(CsvOHLCRepository, MarketDataPort)
    assert issubclass(MarketDataPort, abc.ABC)
    repo = CsvOHLCRepository()  # 全抽象実装済でインスタンス化可
    assert isinstance(repo, MarketDataPort)


def test_load_returns_list_of_domain_bars_with_matching_values(tmp_path):
    from simulator.adapter.repository.ohlc_csv import CsvOHLCRepository

    csv = _write_csv(tmp_path / "ohlc.csv", _valid_rows())

    bars = CsvOHLCRepository().load(csv, None, None)

    assert isinstance(bars, list)
    assert len(bars) == 3
    assert all(isinstance(b, Bar) for b in bars)
    assert bars[0].open == 1.10
    assert bars[0].high == 1.20
    assert bars[0].low == 1.05
    assert bars[0].close == 1.15
    assert bars[0].volume == 100
    assert bars[0].spread == 2


def test_load_raises_ohlc_invalid_error_when_high_below_low(tmp_path):
    from simulator.adapter.repository.ohlc_csv import CsvOHLCRepository

    bad = [("2024-01-01T00:00:00", 1.10, 1.00, 1.20, 1.15, 100, 2)]  # high < low
    csv = _write_csv(tmp_path / "bad.csv", bad)

    with pytest.raises(OHLCInvalidError):
        CsvOHLCRepository().load(csv, None, None)


def test_load_raises_time_order_error_when_time_not_ascending(tmp_path):
    from simulator.adapter.repository.ohlc_csv import CsvOHLCRepository

    rows = [
        ("2024-01-01T00:02:00", 1.20, 1.30, 1.18, 1.28, 120, 2),
        ("2024-01-01T00:00:00", 1.10, 1.20, 1.05, 1.15, 100, 2),  # 逆転
    ]
    csv = _write_csv(tmp_path / "rev.csv", rows)

    with pytest.raises(TimeOrderError):
        CsvOHLCRepository().load(csv, None, None)


def test_load_raises_missing_bar_error_when_required_column_absent(tmp_path):
    from simulator.adapter.repository.ohlc_csv import CsvOHLCRepository

    # close 列欠損
    csv = _write_csv(
        tmp_path / "missing.csv",
        [("2024-01-01T00:00:00", 1.10, 1.20, 1.05, 100, 2)],
        header="time,open,high,low,volume,spread",
    )

    with pytest.raises(MissingBarError):
        CsvOHLCRepository().load(csv, None, None)


def test_load_translates_unreadable_file_to_data_error(tmp_path):
    from simulator.adapter.repository.ohlc_csv import CsvOHLCRepository

    # 存在しないファイル → pandas/OS 例外を DataError へ翻訳（内側漏出禁止）
    with pytest.raises(DataError):
        CsvOHLCRepository().load(tmp_path / "nope.csv", None, None)


def test_load_does_not_leak_framework_exceptions(tmp_path):
    from simulator.adapter.repository.ohlc_csv import CsvOHLCRepository

    # 壊れた CSV（パース不能）でも DataError（BacktestError 配下）に翻訳される
    bad = tmp_path / "broken.csv"
    bad.write_text("\x00\x00not,a,valid\nparse", encoding="utf-8")

    try:
        CsvOHLCRepository().load(bad, None, None)
    except DataError:
        pass  # 期待: 内側例外
    except Exception as e:  # noqa: BLE001
        # pandas.errors.* / OSError 等の外側例外が漏れたら失敗
        from simulator.domain.exceptions import BacktestError

        assert isinstance(e, BacktestError), f"外側例外が漏出: {type(e)!r}"
