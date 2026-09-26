"""dataset.load_candles の時刻範囲（start/end・2026-09-26 承認）の AAA。

固定する契約:
  1. 範囲は ``start <= time <= end``（UNIX 秒・両端含む）。片側だけの指定も効く。
  2. 範囲は「`limit`」より**先に**掛かる（＝範囲内の直近 N 本）。
  3. 範囲を指定しない呼出は従来と同じ出力（直近 limit 本）。
計算量: 足の辞書を組むのは範囲内の行だけである（組んで捨てない）。行数を変えた 2 点で、
  出力件数が範囲内の行数と一致し、全行数に依存しないことを表明する。
"""
from __future__ import annotations

import pandas as pd
import pytest

from marketdata import dataset

_T0 = 1_704_067_200


def _frame(n: int) -> pd.DataFrame:
    index = pd.to_datetime([_T0 + 60 * i for i in range(n)], unit="s")
    return pd.DataFrame(
        {"open": range(n), "high": range(n), "low": range(n), "close": range(n)},
        index=index,
        dtype="float64",
    )


@pytest.fixture
def rows(monkeypatch):
    def install(n: int) -> None:
        monkeypatch.setattr(dataset, "load_dataframe", lambda ref, tf: _frame(n))

    return install


def _times(candles) -> "list[int]":
    return [c["time"] for c in candles]


def test_both_ends_are_inclusive(rows) -> None:
    rows(10)
    got = dataset.load_candles("x", "1m", start=_T0 + 120, end=_T0 + 300)
    assert _times(got) == [_T0 + 60 * i for i in range(2, 6)]


def test_one_sided_ranges(rows) -> None:
    rows(10)
    assert _times(dataset.load_candles("x", "1m", start=_T0 + 480)) == [_T0 + 480, _T0 + 540]
    assert _times(dataset.load_candles("x", "1m", end=_T0 + 60)) == [_T0, _T0 + 60]


def test_the_range_is_applied_before_the_limit(rows) -> None:
    rows(10)
    got = dataset.load_candles("x", "1m", 2, start=_T0, end=_T0 + 300)
    assert _times(got) == [_T0 + 240, _T0 + 300]


def test_without_a_range_the_output_is_unchanged(rows) -> None:
    rows(10)
    assert _times(dataset.load_candles("x", "1m", 3)) == [_T0 + 420, _T0 + 480, _T0 + 540]


@pytest.mark.parametrize(("n", "first", "count"), [(100, 10, 20), (100_000, 10, 20), (100_000, 5000, 3000)])
def test_output_is_the_rows_in_range_regardless_of_total_rows(rows, n, first, count) -> None:
    rows(n)
    got = dataset.load_candles("x", "1m", start=_T0 + 60 * first, end=_T0 + 60 * (first + count - 1))
    assert len(got) == count
