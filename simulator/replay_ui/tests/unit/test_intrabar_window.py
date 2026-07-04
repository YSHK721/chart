"""E-3 IntrabarWindow.window の境界値 AAA（replay.js buildStream:419-430 に一致）。

左ラベル(1m..1D): [t, 次足 or t+dur)。右ラベル(1W,1M): [(prev or t-dur)+DAY, t+DAY)。
"""
from __future__ import annotations

import pytest

from simulator.replay_ui.domain.intrabar_window import window

DAY = 86400


def _bars(times):
    return [{"time": t, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0} for t in times]


def test_left_labeled_uses_next_bar_boundary():
    # Arrange — 1m: [t, 次足)。
    bars = _bars([0, 60, 120])
    # Act
    start, end = window(bars, 1, "1m")
    # Assert
    assert (start, end) == (60, 120)


def test_left_labeled_last_bar_uses_duration_fallback():
    # 末足は次足が無い → t + durationSecs(1m)=60。
    bars = _bars([0, 60, 120])
    start, end = window(bars, 2, "1m")
    assert (start, end) == (120, 180)


def test_left_labeled_1d_duration_fallback():
    bars = _bars([0, DAY, 2 * DAY])
    start, end = window(bars, 2, "1D")
    assert (start, end) == (2 * DAY, 3 * DAY)


def test_right_labeled_week_uses_prev_plus_day_to_t_plus_day():
    # 1W: winStart=(prev.time)+DAY, winEnd=t+DAY。
    w = 604800
    bars = _bars([0, w, 2 * w])
    start, end = window(bars, 1, "1W")
    assert (start, end) == (0 + DAY, w + DAY)


def test_right_labeled_first_bar_uses_duration_fallback_for_start():
    # prev 無し → winStart=(t - durationSecs(1W))+DAY。
    w = 604800
    bars = _bars([w, 2 * w])
    start, end = window(bars, 0, "1W")
    assert (start, end) == ((w - w) + DAY, w + DAY)


def test_right_labeled_month():
    m = 2592000
    bars = _bars([m, 2 * m, 3 * m])
    start, end = window(bars, 1, "1M")
    assert (start, end) == (m + DAY, 2 * m + DAY)


def test_unknown_timeframe_defaults_duration_86400():
    # durationSecs 既定 = 86400（左ラベル扱い）。
    bars = _bars([0])
    start, end = window(bars, 0, "3x")
    assert (start, end) == (0, 86400)


def test_index_zero_left_labeled_has_no_prev_wrap():
    # bar=0 で Python の負 index ラップを起こさない（prev/next を None 扱い）。
    bars = _bars([0, 60])
    start, end = window(bars, 0, "1m")
    assert (start, end) == (0, 60)


def test_out_of_range_index_raises():
    with pytest.raises(IndexError):
        window(_bars([0, 60]), 5, "1m")
