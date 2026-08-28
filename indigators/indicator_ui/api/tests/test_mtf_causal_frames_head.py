"""先頭側 C 足の切り出しが、索引の**時刻解像度に依らず**同じ結果になることを固定する。

背景（ISSUE-450 E・2026-08-28 実測）:
    畳みに要る先頭側 C 足を「DataFrame のまま絞ってから dict 化する」最適化を入れた際、
    索引の生値を ``index.asi8`` で ns として読んだ。``asi8`` は **その索引自身の単位**
    （s / ms / us / ns）の生値であり、実データの索引は ``datetime64[us]`` だったため値が
    10^9 倍ずれ、先頭側 C 足が **1 本も選ばれなくなった**（旧 25,096 本 → 新 0 本）。
    出力は「速くなったが値が違う」状態になり、凍結入力での旧/新突合で初めて露見した。

    合成データのテストは ``pd.to_datetime(..., unit="s")`` が作る索引しか使っておらず、
    本番と解像度が違ったため緑のままだった。ここで全解像度を回して固定する。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pandas as pd
import pytest

from adapter.compute.mtf_causal_frames import (
    _head_frame_of_first_period,
    _head_of_first_period,
    bars_from_frame,
)

HOUR = 3600
DAY = 86400

#: pandas がサポートする日時解像度。索引はどれで来てもよい。
_RESOLUTIONS = ["s", "ms", "us", "ns"]


def _label(tf: str, unix_sec: int) -> int:
    """1D セッション足と同型のラベル（期間の右端の深夜）。"""
    return ((int(unix_sec) + 3 * HOUR) // DAY) * DAY


def _frame(resolution: str) -> "pd.DataFrame":
    """3 期間 × 6 本の C 足を、指定解像度の DatetimeIndex で作る。"""
    times = []
    for d in range(3):
        base = (9 + d) * DAY - 3 * HOUR
        times.extend(base + i * HOUR // 2 for i in range(6))
    index = pd.to_datetime(times, unit="s").as_unit(resolution)
    return pd.DataFrame(
        {"open": [1.0] * len(times), "high": [2.0] * len(times),
         "low": [0.5] * len(times), "close": [1.5] * len(times),
         "volume": [1.0] * len(times)},
        index=index)


@pytest.mark.parametrize("resolution", _RESOLUTIONS)
@pytest.mark.parametrize("window_len", [1, 3, 7, 12])
def test_head_is_the_same_for_every_index_resolution(resolution: str, window_len: int) -> None:
    """DataFrame 経路の切り出しが、bar 列経路と同じ本数・同じ時刻になる。"""
    frame = _frame(resolution)
    window = bars_from_frame(frame.tail(window_len))
    first = int(window[0]["time"])
    label0 = _label("1D", first)
    expected = _head_of_first_period(
        bars_from_frame(frame), first=first, label0=label0,
        compute_tf="1D", bar_time_unix=_label)

    head_frame = _head_frame_of_first_period(
        frame, first=first, label0=label0, compute_tf="1D", bar_time_unix=_label)

    assert head_frame is not None, f"解像度 {resolution} で切り出せなかった"
    got = bars_from_frame(head_frame)
    assert [b["time"] for b in got] == [b["time"] for b in expected], (
        f"解像度 {resolution} / 窓 {window_len} 本で先頭側 C 足がずれた")


@pytest.mark.parametrize("resolution", _RESOLUTIONS)
def test_head_is_empty_when_the_window_starts_a_period(resolution: str) -> None:
    """窓の先頭が期間の先頭なら、先頭側 C 足は無い（余計に拾わない）。"""
    frame = _frame(resolution)
    window = bars_from_frame(frame.iloc[6:])          # 2 期間目の先頭から
    first = int(window[0]["time"])

    head_frame = _head_frame_of_first_period(
        frame, first=first, label0=_label("1D", first),
        compute_tf="1D", bar_time_unix=_label)

    assert head_frame is not None and len(head_frame) == 0


def test_non_datetime_index_falls_back_instead_of_guessing() -> None:
    """DatetimeIndex でない索引では ``None`` を返す（呼び出し側が従来経路へ落ちる）。"""
    frame = pd.DataFrame({"close": [1.0, 2.0]}, index=[0, 1])

    assert _head_frame_of_first_period(
        frame, first=1, label0=0, compute_tf="1D", bar_time_unix=_label) is None
