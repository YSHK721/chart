"""E-2 FormingBar.apply の境界値 AAA（proto_server._apply_forming に bit 一致）。

forming.time が末尾と同一なら暫定 OHLC で置換、新しければ追加、過去なら無変更。
列名は大小無視。forming 不正/None は無変更。
"""
from __future__ import annotations

from simulator.replay_ui.domain.forming_bar import apply


def _bars():
    return [
        {"time": 0, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0},
        {"time": 60, "open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0, "volume": 20.0},
    ]


def test_forming_none_returns_unchanged():
    bars = _bars()
    out = apply(bars, None)
    assert [b["time"] for b in out] == [0, 60]
    assert out[1]["close"] == 2.0


def test_empty_bars_returns_unchanged():
    assert apply([], {"time": 0, "close": 9.0}) == []


def test_forming_equal_last_replaces_provisional_ohlc():
    # Arrange — forming.time == 末尾 time。
    forming = {"time": 60, "open": 1.6, "high": 3.0, "low": 0.9, "close": 2.8}
    # Act
    out = apply(_bars(), forming)
    # Assert — 末尾のみ置換、過去は不変、件数不変。
    assert len(out) == 2
    assert out[0]["close"] == 1.5
    assert out[1]["open"] == 1.6
    assert out[1]["high"] == 3.0
    assert out[1]["low"] == 0.9
    assert out[1]["close"] == 2.8


def test_forming_newer_than_last_appends_new_bar():
    forming = {"time": 120, "open": 2.0, "high": 3.5, "low": 1.8, "close": 3.2}
    out = apply(_bars(), forming)
    assert [b["time"] for b in out] == [0, 60, 120]
    assert out[2]["close"] == 3.2


def test_forming_older_than_last_untouched():
    # forming.time < 末尾 time（異常）→ 触らない（防御）。
    forming = {"time": 0, "close": 99.0}
    out = apply(_bars(), forming)
    assert [b["time"] for b in out] == [0, 60]
    assert out[0]["close"] == 1.5


def test_column_name_case_insensitive():
    forming = {"time": 60, "Open": 1.7, "HIGH": 4.0, "Low": 0.8, "Close": 3.9}
    out = apply(_bars(), forming)
    assert out[1]["open"] == 1.7
    assert out[1]["high"] == 4.0
    assert out[1]["close"] == 3.9


def test_forming_missing_or_invalid_time_returns_unchanged():
    assert apply(_bars(), {"close": 9.0})[1]["close"] == 2.0
    assert apply(_bars(), {"time": "bad"})[1]["close"] == 2.0


def test_only_provided_keys_are_updated_others_preserved():
    # forming に close のみ → open/high/low は元のまま。
    out = apply(_bars(), {"time": 60, "close": 2.9})
    assert out[1]["open"] == 1.5
    assert out[1]["high"] == 2.5
    assert out[1]["close"] == 2.9
