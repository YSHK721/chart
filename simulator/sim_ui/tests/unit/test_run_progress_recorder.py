"""RunProgressRecorder / run_progress_file の検定（2026-09-27 依頼者指示）。

状態検証: 足の処理割合が 0〜100 の整数％で単調に知らされ、最後は 100 になる。
計算量: 知らせる回数 − 知らせた値の種類 = 0（同じ値を二度書かない）。足の本数・評価点の数を
    増やしても回数は 101 以下のまま（足 50 本と 20,000 本・1 足 1 点と 1 足 5 点の 2 点で表明）。
    観測点は注入した知らせ先であり、実装の内部名は差し替えない。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from simulator.sim_ui.adapter.run_progress_file import (
    PROGRESS_FILE,
    read_progress,
    write_progress,
)
from simulator.sim_ui.adapter.run_progress_recorder import RunProgressRecorder


def _run(bar_count: int, points_per_bar: int) -> "list[int]":
    published: "list[int]" = []
    rec = RunProgressRecorder(published.append)
    rec.observe_start(bar_count)
    for i in range(bar_count):
        for _ in range(points_per_bar):
            rec.observe(SimpleNamespace(bar_index=i), None, None, False)
    return published


@pytest.mark.parametrize("bar_count, points_per_bar", [(50, 1), (20_000, 5)])
def test_progress_is_monotone_from_zero_to_one_hundred(bar_count, points_per_bar):
    published = _run(bar_count, points_per_bar)
    assert published[0] == 0
    assert published[-1] == 100
    assert published == sorted(published)


@pytest.mark.parametrize("bar_count, points_per_bar", [(50, 1), (20_000, 5)])
def test_each_value_is_published_once_and_count_does_not_grow_with_input(bar_count, points_per_bar):
    published = _run(bar_count, points_per_bar)
    # 書いた回数 − 書いた値の種類 = 0（同じ値を二度書かない）。
    assert len(published) - len(set(published)) == 0
    # 値は 0〜100 の範囲に限られる＝回数は入力の大きさに依らない。
    assert set(published) <= set(range(101))


def test_no_bars_publishes_only_the_start():
    published: "list[int]" = []
    rec = RunProgressRecorder(published.append)
    rec.observe_start(0)
    rec.observe(SimpleNamespace(bar_index=0), None, None, False)
    assert published == [0]


def test_the_file_round_trips_and_rejects_foreign_values(tmp_path):
    assert read_progress(tmp_path) is None
    write_progress(tmp_path, 42)
    assert read_progress(tmp_path) == 42
    for bad in ('{"percent": 101}', '{"percent": "5"}', '{"percent": true}', "not json"):
        (tmp_path / PROGRESS_FILE).write_text(bad, encoding="utf-8")
        assert read_progress(tmp_path) is None, bad
    # 一時ファイルを残さない。
    assert [p.name for p in tmp_path.iterdir()] == [PROGRESS_FILE]
