"""E-4 TickMidSeries — 窓内の実ティック mid 列算出（domain・依存ゼロ）。

contact_scan.tick_window.window_ticks の純ロジックを移植（parquet IO は adapter へ隔離）。
入力 ``(sec, bid, ask)`` の列に対し、bit 一致で:

    1. 窓 [start, end) フィルタ（secs>=start & secs<end）
    2. mid = (bid + ask) / 2
    3. 窓内 mid の中央値 m を取り、m>0 のとき |mid/m - 1| <= threshold のみ残す（外れ値除去）
    4. cap 無し（接点検証＝全件・絶対仕様）

中央値は偶数個で中央 2 点平均（pandas .median() と一致 = statistics.median）。
pandas/numpy を import しない。
"""
from __future__ import annotations

from statistics import median
from typing import Iterable, Sequence, Tuple

# 外れ値補正の許容相対乖離（0.3=±30%）。proto_server / tick_window と同一基準。
OUTLIER_THRESHOLD = 0.3


def mid_series(
    ticks: "Iterable[Sequence[float]]",
    start: int,
    end: int,
    *,
    threshold: float = OUTLIER_THRESHOLD,
) -> "list[Tuple[int, float]]":
    """``[(sec, mid), ...]`` を時系列順で返す（cap 無し）。"""
    # 窓フィルタ + mid 算出（位置対応を保つ）。
    win_secs: "list[int]" = []
    win_mids: "list[float]" = []
    for row in ticks:
        sec = int(row[0])
        if start <= sec < end:
            win_secs.append(sec)
            win_mids.append((float(row[1]) + float(row[2])) / 2.0)

    if not win_mids:
        return []

    # NaN mid（bid/ask のいずれかが NaN）は中央値算出から除外する＝proto の pandas ``mid.median()``
    #   （skipna）と一致。statistics.median は NaN 混入で破損するため、事前に除く。
    valid = [v for v in win_mids if v == v]  # v == v は NaN で False
    if not valid:
        return []
    m = float(median(valid))
    if m > 0:
        # NaN 行は ``abs(nan) <= threshold`` が False で自然に落ちるが、明示して意図を固定する。
        kept = [
            (s, v)
            for s, v in zip(win_secs, win_mids)
            if v == v and abs(v / m - 1.0) <= threshold
        ]
        return kept
    return [(s, v) for s, v in zip(win_secs, win_mids) if v == v]
