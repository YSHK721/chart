"""E-3 IntrabarWindow — 足境界から足内窓 [start,end) を決める（domain・依存ゼロ）。

replay.js buildStream:419-430 と同一規則（TF_SECS も同一）:

    左ラベル(1m..1D・time=期間始端): winStart=t,               winEnd=次足.time or t+dur
    右ラベル(1W,1M・time=期間終端):   winStart=(prev.time or t-dur)+DAY, winEnd=t+DAY

prev/next は隣接バーで、範囲外は None（Python の負 index ラップを起こさない）。
pandas/numpy を import しない。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

DAY = 86400

# replay.js TF_SECS と bit 一致（durationSecs 既定 = 86400）。
TF_SECS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1D": 86400,
    "1W": 604800,
    "1M": 2592000,
}

_RIGHT_LABELED = ("1W", "1M")


def _duration_secs(timeframe: str) -> int:
    """durationSecs(tf)。未知 tf は 86400（replay.js の `|| 86400` と一致）。"""
    return TF_SECS.get(timeframe, DAY)


def window(
    bars: "Sequence[Mapping[str, Any]]", bar_index: int, timeframe: str
) -> "tuple[int, int]":
    """``bars[bar_index]`` の足内窓 ``[start, end)``（UNIX 秒）を返す。

    ``bar_index`` が範囲外なら ``IndexError``（proto/js は候補足のみ渡す前提）。
    """
    n = len(bars)
    if bar_index < 0 or bar_index >= n:
        raise IndexError(f"bar_index {bar_index} out of range (n={n})")
    cd_time = int(bars[bar_index]["time"])
    dur = _duration_secs(timeframe)

    if timeframe in _RIGHT_LABELED:
        prev = bars[bar_index - 1] if bar_index - 1 >= 0 else None
        win_start = (int(prev["time"]) if prev is not None else cd_time - dur) + DAY
        win_end = cd_time + DAY
    else:
        nxt = bars[bar_index + 1] if bar_index + 1 < n else None
        win_start = cd_time
        win_end = int(nxt["time"]) if nxt is not None else cd_time + dur
    return win_start, win_end
