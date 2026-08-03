"""common.forming_window（ISSUE-250 Phase 1）のテスト。

規則は replay の domain/forming_bar からの**移設**であり、契約（proto_server._apply_forming
との bit 一致）を共有核側で固定する。加えて split_prefix_tails の同値性
（prefix + tails[i] == apply_forming(bars, formings[i])）を固定する。
"""

from __future__ import annotations

from common.forming_window import apply_forming, split_prefix_tails

_BARS = [
    {"time": 100, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0},
    {"time": 200, "open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0, "volume": 20.0},
]


def test_replaces_last_bar_when_time_matches():
    got = apply_forming(_BARS, {"time": 200, "close": 9.9})
    assert len(got) == 2
    assert got[-1]["close"] == 9.9
    assert got[-1]["open"] == 1.5 and got[-1]["volume"] == 20.0   # 未指定キーは保存


def test_appends_new_bar_when_time_is_greater():
    got = apply_forming(_BARS, {"time": 300, "open": 3.0, "close": 3.5})
    assert len(got) == 3 and got[-1]["time"] == 300 and got[-1]["close"] == 3.5


def test_ignores_forming_older_than_last_bar():
    assert apply_forming(_BARS, {"time": 150, "close": 9.9}) == _BARS


def test_ignores_invalid_forming():
    for bad in (None, "x", {}, {"time": "abc"}, {"close": 1.0}):
        assert apply_forming(_BARS, bad) == _BARS


def test_matches_column_names_case_insensitively():
    got = apply_forming(_BARS, {"time": 200, "Close": 7.0, "HIGH": 8.0})
    assert got[-1]["close"] == 7.0 and got[-1]["high"] == 8.0


def test_does_not_mutate_input():
    src = [dict(b) for b in _BARS]
    apply_forming(src, {"time": 200, "close": 9.9})
    assert src == _BARS


def test_empty_bars_returns_empty():
    assert apply_forming([], {"time": 1, "close": 1.0}) == []
    assert split_prefix_tails([], [{"time": 1}]) == ([], [])


def test_split_prefix_tails_is_equivalent_to_full_apply():
    formings = [
        {"time": 200, "close": 2.1},
        {"time": 200, "close": 2.4, "high": 3.0},
        {"time": 300, "open": 2.4, "close": 2.6},
    ]
    prefix, tails = split_prefix_tails(_BARS, formings)
    assert prefix == _BARS[:-1]
    for forming, tail in zip(formings, tails):
        assert prefix + tail == apply_forming(_BARS, forming)   # 同値性（ISSUE-233 の不変条件）


def test_split_prefix_tails_with_empty_formings():
    prefix, tails = split_prefix_tails(_BARS, [])
    assert prefix == _BARS[:-1] and tails == []
