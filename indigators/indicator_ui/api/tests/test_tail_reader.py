"""tail_reader の検証（TDD: Red→Green）— ファイル末尾から逆方向シークで最後の n_rows だけ読む。

OOM 回避（D-2）: 1 分足原子（4.5M 行 / 284MB）を全読みせず、末尾 n_rows（＋ヘッダ確保）だけを
逆方向シークで読む。``read_tail(path, n)`` の結果が ``全読み.tail(n)`` と index/値で一致することを
真値（oracle）として固定する。実 284MB は読まない（小さな tmp CSV のみ・メモリ小）。
"""

from __future__ import annotations

import csv as _csv

import pandas as pd
import pytest

from adapter.compute import tail_reader

_HEADER = ("date", "open", "high", "low", "close", "volume")


def _write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(_HEADER)
        w.writerows(rows)


def _rows(n, start="2020-01-01 00:00:00"):
    # 決定論的な n 行（date,open,high,low,close,volume）。連続する 1 分足タイムスタンプ。
    idx = pd.date_range(start, periods=n, freq="1min")
    out = []
    for i, ts in enumerate(idx):
        out.append([
            ts.strftime("%Y-%m-%d %H:%M:%S"),
            100.0 + i, 100.5 + i, 99.5 + i, 100.2 + i, 1.0 + (i % 7),
        ])
    return out


def _full_read(path):
    # oracle: 全読みして date を index 化する（read_tail の比較基準）。
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


# --------------------------------------------------------------------------- #
# read_tail == 全読み.tail(n)（最重要・index/値一致）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("n", [1, 5, 50])
def test_read_tail_matches_full_read_tail_n(tmp_path, n):
    # Arrange
    csv_path = tmp_path / "data.csv"
    _write_csv(csv_path, _rows(200))
    # Act
    got = tail_reader.read_tail(csv_path, n)
    # Assert: 全読み .tail(n) と index/値で一致（逆方向シークで末尾だけ読む）。
    expected = _full_read(csv_path).tail(n)
    assert list(got.index) == list(expected.index)
    assert got.index.name == "date"
    for col in ("open", "high", "low", "close", "volume"):
        assert got[col].to_numpy() == pytest.approx(expected[col].to_numpy())


def test_read_tail_n_larger_than_rows_returns_all(tmp_path):
    # n > 行数 は安全に全件を返す（境界・全読み tail と一致）。
    csv_path = tmp_path / "small.csv"
    _write_csv(csv_path, _rows(3))
    got = tail_reader.read_tail(csv_path, 100)
    expected = _full_read(csv_path)
    assert list(got.index) == list(expected.index)
    assert len(got) == 3


def test_read_tail_header_only_returns_empty(tmp_path):
    # ヘッダのみ（データ 0 行）は空 DataFrame を安全に返す（境界）。
    csv_path = tmp_path / "headeronly.csv"
    _write_csv(csv_path, [])
    got = tail_reader.read_tail(csv_path, 5)
    assert len(got) == 0


def test_read_tail_does_not_read_whole_file(tmp_path, monkeypatch):
    # メモリ有界: 全読み（chunksize なし pd.read_csv で全件ロード）をしないことを実証する。
    # 末尾 n 行を逆シークで読むため、全件 read_csv は呼ばれてはならない。
    csv_path = tmp_path / "big.csv"
    _write_csv(csv_path, _rows(5000))
    real_read_csv = pd.read_csv
    full_reads = {"n": 0}

    def _spy(*args, **kwargs):
        # nrows / chunksize なしの全件 read_csv をカウントする。
        if not kwargs.get("nrows") and not kwargs.get("chunksize"):
            full_reads["n"] += 1
        return real_read_csv(*args, **kwargs)

    monkeypatch.setattr(tail_reader.pd, "read_csv", _spy)
    tail_reader.read_tail(csv_path, 10)
    assert full_reads["n"] == 0
