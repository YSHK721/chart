"""1 分足 60EMA 安値・高値の線に触れた事象と、偽の線との差 D（RESULTS.md §1 の事前登録どおり）。

観測の境界: 日ごとの集計は ``measure_lines(aggregate=...)`` の注入点だけを通る。
計算量検定はこの注入点に Test Spy を差して数える（内部名を差し替えない）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SPAN = 60
HORIZONS = (5, 15, 60)
FAKE_SHIFTS = (-0.0010, -0.0005, 0.0005, 0.0010)
MAX_GAP_MIN = 30
MEASURES = tuple(f"pnl{n}" for n in HORIZONS) + ("hold",)


def ema(x: np.ndarray, span: int = SPAN) -> np.ndarray:
    """参照実装 core.exponential_ma_on_buffer と同じ EMA（初期値 = 最初の値・係数 2/(span+1)）。"""
    return pd.Series(np.asarray(x, dtype=float)).ewm(span=span, adjust=False).mean().to_numpy()


def touches(o, h, l, prev_line, side: str) -> np.ndarray:
    """前の足の線の値に、線の外側から触れた足の番号（形成中の EMA に触れる条件と同値）。"""
    if side == "lower":
        hit = (o > prev_line) & (l <= prev_line)
    else:
        hit = (o < prev_line) & (h >= prev_line)
    hit[0] = False
    return np.flatnonzero(hit)


def outcomes(idx, entry, c, line_now, minutes, side: str, end_minute: float):
    """事象ごとの指標（向きつき）。値が無いものは NaN。

    pnl{n}: 建値から n 本後の終値までの対数収益（bp）。窓が MAX_GAP_MIN 分を超える空白をまたぐ、
            または期間の終わりを越えるものは NaN。
    hold: 触れた足の終値が触れた側で終わったら 1、そうでなければ 0。
    """
    sgn = 1.0 if side == "lower" else -1.0
    n_all = len(c)
    out = {}
    for n in HORIZONS:
        j = idx + n
        ok = j < n_all
        jj = np.where(ok, j, 0)
        ok &= (minutes[jj] - minutes[idx] <= n + MAX_GAP_MIN) & (minutes[jj] < end_minute)
        v = sgn * (np.log(c[jj]) - np.log(entry)) * 1e4
        out[f"pnl{n}"] = np.where(ok, v, np.nan)
    if side == "lower":
        out["hold"] = (c[idx] > line_now[idx]).astype(float)
    else:
        out["hold"] = (c[idx] < line_now[idx]).astype(float)
    return out


def day_sums(day: np.ndarray, values: np.ndarray, n_days: int):
    """日ごとの和と件数（NaN は除く）。"""
    ok = np.isfinite(values)
    return (np.bincount(day[ok], weights=values[ok], minlength=n_days),
            np.bincount(day[ok], minlength=n_days).astype(float))


def measure_lines(bars: dict, side: str, in_period: np.ndarray, day: np.ndarray, n_days: int,
                  end_minute: float, aggregate=day_sums):
    """本物の線と偽の線 4 本について、指標ごとの日別の和・件数を返す。

    返り値: {指標: [(和, 件数) 本物, (和, 件数) 偽 1, ...]}
    """
    o, h, l, c, m = bars["open"], bars["high"], bars["low"], bars["close"], bars["minutes"]
    base = ema(l if side == "lower" else h)
    prev = np.concatenate([[np.nan], base[:-1]])
    res = {k: [] for k in MEASURES}
    for shift in (0.0,) + FAKE_SHIFTS:
        line_now = base * (1 + shift)
        prev_line = prev * (1 + shift)
        idx = touches(o, h, l, prev_line, side)
        idx = idx[in_period[idx]]
        entry = prev_line[idx]  # §1.3: 始値は線の外側なので、約定は線の値
        oc = outcomes(idx, entry, c, line_now, m, side, end_minute)
        for k in MEASURES:
            res[k].append(aggregate(day[idx], oc[k], n_days))
    return res


def d_statistic(sums_counts):
    """D = 本物の平均 − 偽の線の平均の平均。"""
    means = [s.sum() / n.sum() for s, n in sums_counts]
    return means[0] - float(np.mean(means[1:])), means


def bootstrap_d(sums_counts, weights: np.ndarray) -> np.ndarray:
    """日の復元抽出の重み（回数 × 日数）から D* を返す。事象は数え直さない。"""
    means = [(weights @ s) / (weights @ n) for s, n in sums_counts]
    return means[0] - np.mean(means[1:], axis=0)


def holm(p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, dtype=float)
    n = len(p)
    order = np.argsort(p)
    adj = np.maximum.accumulate(p[order] * (n - np.arange(n)))
    out = np.empty(n)
    out[order] = np.minimum(adj, 1.0)
    return out
