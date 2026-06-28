"""bar_window — 足 1 本の足内データ窓 ``[start, end)`` を足境界から決める（純）。

replay.js buildStream のラベル規約ミラー（resample 既定）:
  - 左ラベル(1m..1D・time=期間始端) → ``[t[i], t[i+1] or t[i]+dur)``
  - 右ラベル(1W=W-FRI / 1M=ME・time=期間終端) → ``[t[i-1]+DAY or t[i]-dur+DAY, t[i]+DAY)``
末足は次足が無いため期間長 dur で代用、先頭足（右ラベル）は前足が無いため ``t[i]-dur`` で代用する。
"""
from __future__ import annotations

DAY = 86400

# 時間足→期間秒。replay.js TF_SECS と同値（末足の窓近似用）。
TF_SECS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1D": 86400, "1W": 604800, "1M": 2592000,
}


def _dur(timeframe: str) -> int:
    return TF_SECS.get(timeframe, DAY)


def bar_window(bar_times, i: int, timeframe: str):
    """``bar_times[i]``（UNIX 秒・昇順）の足内窓 ``(start, end)`` を返す。"""
    t = bar_times
    dur = _dur(timeframe)
    if timeframe in ("1W", "1M"):                      # 右ラベル
        prev = t[i - 1] if i - 1 >= 0 else None
        start = (prev if prev is not None else t[i] - dur) + DAY
        end = t[i] + DAY
    else:                                              # 左ラベル(1m..1D)
        nxt = t[i + 1] if i + 1 < len(t) else None
        start = t[i]
        end = nxt if nxt is not None else t[i] + dur
    return int(start), int(end)
