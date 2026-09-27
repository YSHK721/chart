"""OHLC 読みの単一点（ohlc_frame_cache・ISSUE-541 段 2）の契約と計算量。

観測の境界: 本モジュールが宣言する注入点（`set_reader`）と発行記録（`parse_log`）だけを
使う（内部名の monkeypatch はしない・絶対命令 2026-09-25）。

計算量: 「発行（parse）− 相異なる実体 = 0」。回数そのものは焼き込まない。
"""
from __future__ import annotations

import pandas as pd
import pytest

from simulator.adapter.repository import ohlc_frame_cache as cache
from simulator.domain.exceptions import ConfigError


@pytest.fixture(autouse=True)
def _fresh_cache():
    cache.clear()
    cache.set_reader(None)
    yield
    cache.clear()
    cache.set_reader(None)


def _write(path, rows=3):
    frame = pd.DataFrame(
        {"time": range(rows), "open": [1.0] * rows, "close": [2.0] * rows}
    )
    frame.to_csv(path, index=False)
    return path


def _counting_reader(counts):
    def reader(path, **kwargs):
        counts.append(str(path))
        return pd.read_csv(path, **kwargs)
    return reader


def test_same_source_is_parsed_once_and_projections_add_no_parse(tmp_path):
    # Arrange
    path = _write(tmp_path / "a.csv")
    counts = []
    cache.set_reader(_counting_reader(counts))

    # Act: 全列 2 回・射影（usecols）2 回
    full1 = cache.read_frame(path)
    full2 = cache.read_frame(path)
    times = cache.read_frame(path, usecols=["time"])
    cache.read_frame(path, usecols=["time", "close"])

    # Assert: 発行 − 相異なる実体 = 0
    assert len(counts) - 1 == 0
    assert len(cache.parse_log) - 1 == 0
    assert full2 is full1, "同じ実体は同じ DataFrame を返す（写しを作らない）"
    assert list(times.columns) == ["time"]
    assert times["time"].tolist() == [0, 1, 2]


def test_distinct_sources_and_sep_are_distinct_parses(tmp_path):
    a = _write(tmp_path / "a.csv")
    b = _write(tmp_path / "b.csv")
    counts = []
    cache.set_reader(_counting_reader(counts))

    cache.read_frame(a)
    cache.read_frame(b)
    cache.read_frame(a, sep=",")   # sep 指定は別の読み方＝別の鍵
    cache.read_frame(a)
    cache.read_frame(b)

    distinct = {(p, sep) for (p, _m, _s, sep) in cache.parse_log}
    assert len(cache.parse_log) - len(distinct) == 0
    assert len(counts) == len(distinct) == 3


def test_a_rewritten_source_is_reparsed(tmp_path):
    import os

    path = _write(tmp_path / "a.csv", rows=2)
    first = cache.read_frame(path)
    assert len(first) == 2
    _write(path, rows=5)
    os.utime(path, ns=(1, 1))   # mtime を確実に変える（同一秒内の書き換えでも鍵が変わる）
    assert len(cache.read_frame(path)) == 5
    assert len(cache.parse_log) == 2


def test_unknown_read_options_are_rejected(tmp_path):
    path = _write(tmp_path / "a.csv")
    with pytest.raises(ConfigError):
        cache.read_frame(path, dtype={"time": "int64"})


def test_missing_file_raises_for_the_caller_to_translate(tmp_path):
    with pytest.raises(OSError):
        cache.read_frame(tmp_path / "missing.csv")
