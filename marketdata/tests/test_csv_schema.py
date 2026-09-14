"""csv_schema 単一定義の検証（ISSUE-094 🟡-6）。

tick_m1 と rollup の CSV スキーマ（_HEADER / _DATE_FMT）が csv_schema の唯一の規則源から
共有され、両者が **同一オブジェクト** を指す（手動同期の余地が構造的に消えている）ことを固定する。
byte 値は従来と不変であることも併せて確認する。
"""

from __future__ import annotations

from marketdata import csv_schema, rollup, tick_m1


# --- 従来値の byte 不変（回帰の壁） --------------------------------------- #
def test_header_and_datefmt_byte_unchanged():
    assert csv_schema.HEADER == ["date", "open", "high", "low", "close", "volume"]
    assert csv_schema.OHLCV_COLUMNS == ["open", "high", "low", "close", "volume"]
    assert csv_schema.DATE_FMT == "%Y-%m-%d %H:%M:%S"


# --- tick_m1 / rollup が csv_schema と同一オブジェクトを共有する ---------- #
def test_tick_m1_shares_schema_object():
    assert tick_m1._HEADER is csv_schema.HEADER
    assert tick_m1._DATE_FMT is csv_schema.DATE_FMT
    assert tick_m1._OHLCV_COLUMNS is csv_schema.OHLCV_COLUMNS


def test_rollup_shares_schema_object():
    assert rollup._HEADER is csv_schema.HEADER
    assert rollup._DATE_FMT is csv_schema.DATE_FMT


# --- tick_m1 と rollup が相互に一致する（既存 test_tick_m1 のパリティを本源に昇格） --- #
def test_tick_m1_and_rollup_headers_are_identical_object():
    assert tick_m1._HEADER is rollup._HEADER
    assert tick_m1._DATE_FMT is rollup._DATE_FMT
