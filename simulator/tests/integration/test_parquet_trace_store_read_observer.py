"""parquet_trace_store の読みの観測口（検査側の設計・絶対命令 2026-09-25）。

計算量検定は実装の内部名を差し替えて測ってはならない。読み口が**自分で**「IO 段が組み立てた
行数と列」を知らせる注入点 `set_read_observer` を宣言し、検定はそこだけを使う。

表明:
    1. 読みのたびに 1 回、IO 段が組み立てた行数と列名が届く。
    2. 行を読まない問い（件数・範囲）では届かない。
    3. 外した後は届かない（検定同士が混ざらない）。
    4. 届く行数 − 返した行数 = 0（区間の外の行を組み立てない）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from simulator.adapter.trace import parquet_trace_store as store


def _write(path: Path, rows: int) -> Path:
    store.write_columns(
        path,
        {"bar_index": list(range(rows)), "close": [float(i) for i in range(rows)],
         "equity": [1.0] * rows},
    )
    return path


@pytest.fixture
def seen():
    calls: "list[tuple[int, tuple[str, ...]]]" = []
    store.set_read_observer(lambda rows, columns: calls.append((rows, columns)))
    yield calls
    store.set_read_observer(None)


def test_a_read_reports_the_rows_and_columns_the_io_layer_built(tmp_path: Path, seen) -> None:
    # Arrange
    path = _write(tmp_path / "a.parquet", 100)

    # Act
    got = store.read_columns(
        path, columns=["bar_index", "close"], start=10, end=25, time_column="bar_index"
    )

    # Assert: 届く行数 − 返した行数 = 0・届く列 − 返した列 = 0。
    assert len(seen) == 1
    rows, columns = seen[0]
    assert rows - len(got["bar_index"]) == 0
    assert set(columns) == set(got)
    # 正の対照: 0 行どうしを比べていない。
    assert got["bar_index"] == list(range(10, 25))


def test_questions_that_read_no_row_report_nothing(tmp_path: Path, seen) -> None:
    # Arrange
    path = _write(tmp_path / "a.parquet", 100)

    # Act
    counted = store.count_rows_in_window(path, start=10, end=25, time_column="bar_index")
    _low, _high, total = store.time_bounds(path, time_column="bar_index")

    # Assert
    assert (counted, total) == (15, 100)
    assert seen == []


def test_a_removed_observer_receives_nothing(tmp_path: Path) -> None:
    # Arrange
    path = _write(tmp_path / "a.parquet", 10)
    calls: list = []
    store.set_read_observer(lambda rows, columns: calls.append(rows))
    store.set_read_observer(None)

    # Act
    store.read_columns(path, columns=["close"])

    # Assert
    assert calls == []
