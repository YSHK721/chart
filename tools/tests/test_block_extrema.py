"""ブロック高値・安値ログの状態検証（tools/block_extrema）。"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd

from tools.block_extrema.extrema import block_extrema, block_lengths
from tools.block_extrema.report import write_log
from tools.block_extrema.source import FrameBarSource


def _frame(highs, lows) -> pd.DataFrame:
    index = pd.date_range("2024-01-01 00:00:00", periods=len(highs), freq="min")
    return pd.DataFrame({"high": np.asarray(highs, float), "low": np.asarray(lows, float)}, index=index)


def _log(frame: pd.DataFrame) -> "list[str]":
    source = FrameBarSource(frame)
    out = io.StringIO()
    write_log(block_extrema(source), source, out)
    return out.getvalue().splitlines()


def test_odd_count_puts_remainder_last_and_takes_the_earliest_tie():
    # N=5 → 段のブロック単位数 5, 2, 1。段 2 は [0:2] [2:4] と余り [4:5]。高値 5 は位置 1 と 2 で同値。
    lines = _log(_frame([3, 5, 5, 1, 2], [2, 4, 0, 0, 1]))
    assert lines == [
        "5,2024/01/01T00:01:00,5.0", "5,2024/01/01T00:02:00,0.0",
        "2,2024/01/01T00:01:00,5.0", "2,2024/01/01T00:00:00,2.0",
        "2,2024/01/01T00:02:00,5.0", "2,2024/01/01T00:02:00,0.0",
        "1,2024/01/01T00:04:00,2.0", "1,2024/01/01T00:04:00,1.0",
        "1,2024/01/01T00:00:00,3.0", "1,2024/01/01T00:00:00,2.0",
        "1,2024/01/01T00:01:00,5.0", "1,2024/01/01T00:01:00,4.0",
        "1,2024/01/01T00:02:00,5.0", "1,2024/01/01T00:02:00,0.0",
        "1,2024/01/01T00:03:00,1.0", "1,2024/01/01T00:03:00,0.0",
        "1,2024/01/01T00:04:00,2.0", "1,2024/01/01T00:04:00,1.0",
    ]


def test_power_of_two_has_no_remainder_block():
    assert list(block_lengths(8)) == [8, 4, 2, 1]
    levels = list(block_extrema(FrameBarSource(_frame(range(8), range(8)))))
    assert [lv.bars.tolist() for lv in levels] == [[8], [4, 4], [2, 2, 2, 2], [1] * 8]


def test_matches_a_brute_force_scan():
    rng = np.random.default_rng(0)
    for n in (1, 2, 7, 33, 100):
        highs = rng.integers(0, 10, n).astype(float)
        lows = highs - rng.integers(0, 5, n)
        frame = _frame(highs, lows)
        expected = []
        for length in block_lengths(n):
            for s in range(0, n, length):
                e = min(s + length, n)
                hi = s + int(np.argmax(highs[s:e]))
                lo = s + int(np.argmin(lows[s:e]))
                for at, v in ((hi, highs[hi]), (lo, lows[lo])):
                    expected.append(f"{e - s},{frame.index[at]:%Y/%m/%dT%H:%M:%S},{v}")
        assert _log(frame) == expected
