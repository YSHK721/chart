"""adapter/repository/ohlc_mt5_csv.py の Mt5CsvOHLCRepository テスト。

MT5 エクスポート形式（タブ区切り・ヘッダ `<DATE> <TIME> <OPEN> <HIGH> <LOW>
<CLOSE> <TICKVOL> <VOL> <SPREAD>`・日付 2025.01.02・spread=点 int）を
domain.Bar 列へ読み込む入力アダプタ（MarketDataPort 実装）。

既存 CsvOHLCRepository（comma 区切り・time/open/.../spread 列）は MT5 形式と
非互換のため、本形式専用ローダを追加する（既存形式テストは壊さない）。

検証契約（既存 CsvOHLCRepository と同等の不変条件）:
    - <DATE>+<TIME> を結合し numpy.datetime64 へ（昇順比較可能）
    - <TICKVOL> を volume・<SPREAD> を spread(点 int) へ
    - OHLC 整合違反 → OHLCInvalidError（domain.Bar 由来）
    - 時刻降順 → TimeOrderError
"""
from __future__ import annotations

import numpy as np
import pytest

from backtest.domain.exceptions import OHLCInvalidError, TimeOrderError
from backtest.usecase.ports import MarketDataPort

# MT5 エクスポート形式の最小サンプル（タブ区切り）。実 fixture 先頭2バーに準拠。
_HEADER = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>"
_ROW0 = "2025.01.02\t01:00:00\t39400.5\t39400.5\t39400.5\t39400.5\t1\t0\t480"
_ROW1 = "2025.01.02\t01:01:00\t39402.0\t39447.0\t39402.0\t39447.0\t9\t0\t100"


def _write(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_mt5_loader_implements_market_data_port():
    # Arrange / Act
    from backtest.adapter.repository.ohlc_mt5_csv import Mt5CsvOHLCRepository

    repo = Mt5CsvOHLCRepository()

    # Assert: LSP — MarketDataPort のサブクラスで抽象解決済み
    assert isinstance(repo, MarketDataPort)


def test_parses_tab_separated_date_time_into_bars(tmp_path):
    # Arrange: MT5 形式の 2 バー
    from backtest.adapter.repository.ohlc_mt5_csv import Mt5CsvOHLCRepository

    csv_path = _write(tmp_path / "mt5.csv", [_HEADER, _ROW0, _ROW1])

    # Act
    bars = Mt5CsvOHLCRepository().load(csv_path)

    # Assert: 2 バー・<DATE>+<TIME> 結合・OHLC・volume(=tickvol)・spread(点)
    assert len(bars) == 2
    b0, b1 = bars
    assert b0.time == np.datetime64("2025-01-02T01:00:00")
    assert b0.open == 39400.5 and b0.high == 39400.5
    assert b0.low == 39400.5 and b0.close == 39400.5
    assert b0.volume == 1.0
    assert b0.spread == 480
    assert b1.time == np.datetime64("2025-01-02T01:01:00")
    assert b1.open == 39402.0 and b1.high == 39447.0
    assert b1.spread == 100


def test_spread_column_is_integer_points(tmp_path):
    # Arrange: spread=100 点
    from backtest.adapter.repository.ohlc_mt5_csv import Mt5CsvOHLCRepository

    csv_path = _write(tmp_path / "mt5.csv", [_HEADER, _ROW1])

    # Act
    bars = Mt5CsvOHLCRepository().load(csv_path)

    # Assert: spread は点単位の int
    assert isinstance(bars[0].spread, int)
    assert bars[0].spread == 100


def test_descending_time_raises_time_order_error(tmp_path):
    # Arrange: 2 行目が 1 行目より過去（降順）
    from backtest.adapter.repository.ohlc_mt5_csv import Mt5CsvOHLCRepository

    desc = "2025.01.02\t00:59:00\t39402.0\t39447.0\t39402.0\t39447.0\t9\t0\t100"
    csv_path = _write(tmp_path / "mt5.csv", [_HEADER, _ROW0, desc])

    # Act / Assert
    with pytest.raises(TimeOrderError):
        Mt5CsvOHLCRepository().load(csv_path)


def test_ohlc_invariant_violation_raises(tmp_path):
    # Arrange: low(39450) > high(39400) の不整合
    from backtest.adapter.repository.ohlc_mt5_csv import Mt5CsvOHLCRepository

    bad = "2025.01.02\t01:00:00\t39400.0\t39400.0\t39450.0\t39400.0\t1\t0\t100"
    csv_path = _write(tmp_path / "mt5.csv", [_HEADER, bad])

    # Act / Assert
    with pytest.raises(OHLCInvalidError):
        Mt5CsvOHLCRepository().load(csv_path)
