"""period_hl core — 暦年内の走行高安（純粋計算・numpy のみ）。

期間境界について:
    各時間足の「期間内の走行高安」はバーの high / low **そのもの**であり、本 core に計算は無い
    （期間境界の定義はロールアップのグリッド＝marketdata が唯一源で、ここへ複製しない）。
    本 core が持つのは、バー 1 本に対応する足が存在しない**暦年**の畳み込みだけである。

年初来（YTD）の定義:
    バー時刻（naive UTC・marketdata の date 列の時刻系）の暦年ごとに、high の走行 max /
    low の走行 min を取る。**窓に現れる最初の年は NaN** とする — その年のバーが年初から
    揃っているかをデータだけからは保証できず、途中からの max/min を「年初来」と偽ると
    無言で誤った水準になるため（値を出すのは、直前の年のバーが窓に入っている＝年境界を
    窓が覆っていると確認できる年だけ）。

計算量:
    各要素はちょうど 1 回だけ畳み込みへ供される（発行した計算 − 出力に使った計算 = 0）。
    畳み込みの供給要素数は ``_ACC_MAX`` / ``_ACC_MIN`` を Test Spy で数えて固定する
    （``tests/test_complexity.py``）。
"""

from __future__ import annotations

import numpy as np

# 走行 max / min の畳み込みプリミティブ（計算量テストが Spy を差す縫い目）。
_ACC_MAX = np.maximum.accumulate
_ACC_MIN = np.minimum.accumulate


def year_runs(years: np.ndarray) -> "list[tuple[int, int]]":
    """同一年が連続する区間 ``[(start, stop), ...]``（stop は排他）を返す。O(n)。

    バーは時刻昇順（serving 側の「厳密増加 time」保証）なので、連続区間＝暦年である。
    """
    values = np.asarray(years)
    n = int(values.size)
    if n == 0:
        return []
    boundaries = np.flatnonzero(values[1:] != values[:-1]) + 1
    starts = np.concatenate(([0], boundaries))
    stops = np.concatenate((boundaries, [n]))
    return list(zip(starts.tolist(), stops.tolist()))


def ytd_running_extremes(
    years: np.ndarray, high: np.ndarray, low: np.ndarray
) -> "tuple[np.ndarray, np.ndarray]":
    """暦年ごとの走行 max（high）/ 走行 min（low）。窓の最初の年は NaN。

    Args:
        years: バーごとの暦年（時刻昇順）。
        high: バーごとの高値。
        low: バーごとの安値。

    Returns:
        ``(ytd_hi, ytd_lo)``（入力と同じ長さ・最初の年の区間は NaN）。
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    if high.shape != low.shape:
        raise ValueError(f"high と low の長さが一致しません: {high.shape} != {low.shape}")
    hi = np.full(high.shape, np.nan, dtype=np.float64)
    lo = np.full(low.shape, np.nan, dtype=np.float64)
    for start, stop in year_runs(years)[1:]:
        hi[start:stop] = _ACC_MAX(high[start:stop])
        lo[start:stop] = _ACC_MIN(low[start:stop])
    return hi, lo
