"""列構成が変わったときにロールアップ CSV を壊さないことの回帰テスト（ISSUE-252）。"""
from __future__ import annotations

import pandas as pd
import pytest

from marketdata import rollup


def _write(path, header, rows):
    path.write_text("\n".join([",".join(header)] + [",".join(map(str, r)) for r in rows]) + "\n")


def test_merge_keeps_all_sum_columns(tmp_path):
    """既存 CSV と新規 tail のマージで up/dn を落とさない（列を落とすとヘッダが縮む）。"""
    idx = pd.to_datetime(["2026-08-01 00:00:00", "2026-08-01 00:01:00"])
    df = pd.DataFrame(
        {"open": [1.0, 2.0], "high": [2.0, 3.0], "low": [0.5, 1.5],
         "close": [1.5, 2.5], "volume": [10.0, 20.0], "up": [6.0, 12.0], "dn": [4.0, 8.0]},
        index=idx,
    )
    agg = rollup._merge_agg(df.columns)
    got = df.groupby(level=0, sort=True).agg(agg)
    assert list(got.columns) == ["open", "high", "low", "close", "volume", "up", "dn"]


def test_append_is_refused_when_the_header_does_not_match(tmp_path):
    """ヘッダ 6 列のファイルへ 8 列行を追記しない（追記すれば CSV が恒久的に読めなくなる）。"""
    path = tmp_path / "x_1M.csv"
    _write(path, ["date", "open", "high", "low", "close", "volume"],
           [["2026-07-31 00:00:00", 1, 2, 0, 1, 5]])
    bars = {pd.Timestamp("2026-08-31"): {"open": 1.0, "high": 2.0, "low": 0.0,
                                         "close": 1.0, "volume": 5.0, "up": 3.0, "dn": 2.0}}
    assert rollup._header_of(path) != rollup._header_for_bars(bars)


def test_header_of_reads_the_first_line(tmp_path):
    path = tmp_path / "y_5m.csv"
    _write(path, ["date", "open", "high", "low", "close", "volume", "up", "dn"], [])
    assert rollup._header_of(path) == ["date", "open", "high", "low", "close", "volume", "up", "dn"]
    assert rollup._header_of(tmp_path / "missing.csv") is None
