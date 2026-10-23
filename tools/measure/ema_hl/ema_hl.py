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
#: §3.1: −1.00% 〜 +1.00% を 0.05% 刻み（41 本・中央が本物）
RANGE_SHIFTS = tuple(round(k * 0.0005, 6) for k in range(-20, 21))
INNER, OUTER = (0.0, 0.0010), (0.0030, 0.0100)


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


def outcomes(idx, entry, c, line_now, minutes, side: str, end_minute: float, bar_minutes: int = 1):
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
        ok &= (minutes[jj] - minutes[idx] <= n * bar_minutes + MAX_GAP_MIN) & (minutes[jj] < end_minute)
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
                  end_minute: float, aggregate=day_sums, shifts=None, span: int = SPAN, bar_minutes: int = 1):
    """線をずらし幅 shifts の各値でずらし、指標ごとの日別の和・件数を返す。

    shifts を省くと §1 の本物と偽の線 4 本（(0,) + FAKE_SHIFTS）。
    返り値: {指標: [(和, 件数) shifts[0], (和, 件数) shifts[1], ...]}
    """
    o, h, l, c, m = bars["open"], bars["high"], bars["low"], bars["close"], bars["minutes"]
    base = ema(l if side == "lower" else h, span)
    prev = np.concatenate([[np.nan], base[:-1]])
    res = {k: [] for k in MEASURES}
    for shift in ((0.0,) + FAKE_SHIFTS if shifts is None else shifts):
        line_now = base * (1 + shift)
        prev_line = prev * (1 + shift)
        idx = touches(o, h, l, prev_line, side)
        idx = idx[in_period[idx]]
        entry = prev_line[idx]  # §1.3: 始値は線の外側なので、約定は線の値
        oc = outcomes(idx, entry, c, line_now, m, side, end_minute, bar_minutes)
        for k in MEASURES:
            res[k].append(aggregate(day[idx], oc[k], n_days))
    return res


def shuffled_path(bars: dict, day: np.ndarray, rng: np.random.Generator) -> dict:
    """§3.2: 各日の 1 分足を、形（始値からの差）を保って日の中で並べ替え、終値→次の始値でつなぐ。"""
    o, h, l, c = bars["open"], bars["high"], bars["low"], bars["close"]
    order = np.lexsort((rng.random(len(o)), day))
    dh, dl, dc = (h - o)[order], (l - o)[order], (c - o)[order]
    close = o[0] + np.cumsum(dc)
    open_ = np.concatenate([[o[0]], close[:-1]])
    return {"open": open_, "high": open_ + dh, "low": open_ + dl, "close": close, "minutes": bars["minutes"]}


def totals(sums_counts):
    """日別の和・件数のリストから、線ごとの平均と件数。"""
    n = np.array([c.sum() for _, c in sums_counts])
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.array([s.sum() for s, _ in sums_counts]) / n
    return mean, n


def zone_z(excess: np.ndarray, counts: np.ndarray, shifts=RANGE_SHIFTS) -> float:
    """§3.3: 内側の超過の平均 − 外側の超過の平均（事象数で重みづけ）。"""
    a = np.abs(np.asarray(shifts))
    inner = (a >= INNER[0] - 1e-12) & (a <= INNER[1] + 1e-12)
    outer = (a >= OUTER[0] - 1e-12) & (a <= OUTER[1] + 1e-12)
    w = lambda m: np.nansum(excess[m] * counts[m]) / counts[m][np.isfinite(excess[m])].sum()
    return float(w(inner) - w(outer))


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
