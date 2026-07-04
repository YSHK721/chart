"""E-1 RevealClock.truncate の境界値 AAA（proto_server._truncate に bit 一致）。

因果不変（未来リーク禁止）: time <= until_t のバーのみ残す。until None は無変更。
"""
from __future__ import annotations

from simulator.replay_ui.domain.reveal_clock import truncate


def _bars():
    return [
        {"time": 0, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
        {"time": 60, "open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0},
        {"time": 120, "open": 2.0, "high": 3.0, "low": 1.5, "close": 2.5},
    ]


def test_until_none_returns_all_bars_unchanged():
    # Arrange
    bars = _bars()
    # Act
    out = truncate(bars, None)
    # Assert
    assert [b["time"] for b in out] == [0, 60, 120]


def test_until_on_bar_boundary_keeps_that_bar_inclusive():
    # Arrange / Act — proto: time <= until を残す（境界は含む）。
    out = truncate(_bars(), 60)
    # Assert
    assert [b["time"] for b in out] == [0, 60]


def test_until_between_bars_drops_future_bars():
    # Arrange / Act
    out = truncate(_bars(), 119)
    # Assert
    assert [b["time"] for b in out] == [0, 60]


def test_until_before_first_bar_returns_empty():
    # Arrange / Act
    out = truncate(_bars(), -1)
    # Assert
    assert out == []


def test_result_is_new_list_and_preserves_order_and_values():
    # Arrange
    bars = _bars()
    # Act
    out = truncate(bars, 120)
    # Assert — 値・順序保存、かつ元 list を破壊しない。
    assert out is not bars
    assert [b["time"] for b in out] == [0, 60, 120]
    assert out[1]["close"] == 2.0
