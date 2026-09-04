"""bar_window — 足 1 本の足内データ窓 ``[start, end)`` を足境界から決める（純）。

依存は stdlib と **依存ゼロの時間足台帳** :mod:`marketdata.tf_ledger` のみ（pandas/numpy は
読み込まれない＝``simulator/tests/unit/test_contact_scan_usecase_purity.py`` が実行して固定する）。
期間秒（``TF_SECS``）はかつてここに手書き dict を持っていたが、それは台帳の第 2 定義であり、
台帳へ時間足を足しても追随せず検定も落ちなかった（ISSUE-261。同型の事故が ISSUE-253）。
台帳側の ``bar_sec`` からの導出値へ置換し、時間足の追加は台帳 1 行で完結する。

参照実装 prototype_260626-01/contact_scan/bar_window.py と bit 一致。replay.js buildStream の
ラベル規約ミラー（resample 既定）:
  - 左ラベル(1m..1D・time=期間始端) → ``[t[i], t[i+1] or t[i]+dur)``
  - 右ラベル(1W=W-FRI / 1M=ME・time=期間終端) → ``[t[i-1]+DAY or t[i]-dur+DAY, t[i]+DAY)``
末足は次足が無いため期間長 dur で代用、先頭足（右ラベル）は前足が無いため ``t[i]-dur`` で代用する。
"""
from __future__ import annotations

from marketdata.tf_ledger import TF_BAR_SEC as TF_SECS

DAY = 86400


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
