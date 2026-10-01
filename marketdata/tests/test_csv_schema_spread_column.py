"""``csv_schema`` の spread 列（ISSUE-511 段階 2）の検定。

固定するもの:
    1. spread は**既知列**であり、ヘッダでは up/dn の直後・未知列より前に置かれる。
    2. 既知値列の順序の唯一源は ``VALUE_COLUMNS``。
    3. spread を持たないデータのヘッダは従来と 1 バイトも変わらない。
    4. spread は合算集約（SUM_COLUMNS）の対象ではない（期間内の件数ではない）。
"""
from __future__ import annotations

from marketdata import csv_schema


def test_the_spread_column_is_named_spread() -> None:
    assert csv_schema.SPREAD_COLUMN == "spread"


def test_value_columns_fix_the_order_of_known_columns() -> None:
    """既知値列の順序は OHLCV → up/dn → spread（書き下し）。"""
    assert csv_schema.VALUE_COLUMNS == [
        "open", "high", "low", "close", "volume", "up", "dn", "spread",
    ]


def test_spread_is_placed_after_up_dn_and_before_unknown_columns() -> None:
    """入力順に依らず、spread は up/dn の直後・未知列（foo）より前に並ぶ。"""
    # Arrange: 未知列 foo と spread を OHLCV より前に置く。
    columns = ["foo", "spread", "open", "high", "low", "close", "volume", "up", "dn"]

    # Act
    header = csv_schema.header_for(columns)

    # Assert
    assert header == [
        "date", "open", "high", "low", "close", "volume", "up", "dn", "spread", "foo",
    ]


def test_without_spread_the_header_is_unchanged() -> None:
    """spread を持たないデータのヘッダは従来どおり（既存 CSV の書式不変）。"""
    assert csv_schema.header_for(csv_schema.OHLCV_COLUMNS) == csv_schema.HEADER
    assert csv_schema.header_for([*csv_schema.OHLCV_COLUMNS, "up", "dn"]) == [
        "date", "open", "high", "low", "close", "volume", "up", "dn",
    ]


def test_spread_is_not_summed_when_resampling() -> None:
    """spread は期間内の件数ではないので合算集約の対象にしない。"""
    assert csv_schema.SPREAD_COLUMN not in csv_schema.SUM_COLUMNS
