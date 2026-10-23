"""水準に届く確率（RESULTS.md §1 の事前登録どおり）。

観測の境界: 先の T 本の高値の最大・安値の最小は ``reach_counts(forward=...)`` の注入点だけを通る。
計算量検定はこの注入点に Test Spy を差して数える（内部名を差し替えない）。
"""
from __future__ import annotations

import math

import numpy as np

XS = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0)
TS = (1, 5, 20)
WINDOW = 20


def atr(h, l, c, window: int = WINDOW) -> np.ndarray:
    """足 t までの window 本の真の値幅の単純平均。そろわない足は NaN。"""
    prev = np.concatenate([[np.nan], c[:-1]])
    tr = np.nanmax(np.vstack([h - l, np.abs(h - prev), np.abs(l - prev)]), axis=0)
    cs = np.concatenate([[0.0], np.cumsum(tr)])
    out = np.full(len(c), np.nan)
    out[window - 1:] = (cs[window:] - cs[:-window]) / window
    return out


def diff_std(c, window: int = WINDOW) -> np.ndarray:
    """足 t までの window 本の終値の差の標準偏差（ddof=1）。そろわない足は NaN。"""
    d = np.diff(c)
    out = np.full(len(c), np.nan)
    if len(d) >= window:
        win = np.lib.stride_tricks.sliding_window_view(d, window)
        out[window:] = win.std(axis=1, ddof=1)
    return out


def forward_extreme(x: np.ndarray, t: int, kind: str) -> np.ndarray:
    """x[i+1..i+t] の最大（kind="max"）・最小（"min"）。先が足りない足は NaN。"""
    n = len(x)
    out = np.full(n, np.nan)
    if n > t:
        win = np.lib.stride_tricks.sliding_window_view(x[1:], t)
        out[: n - t] = win.max(axis=1) if kind == "max" else win.min(axis=1)
    return out


def reach_counts(h, l, c, start: np.ndarray, day: np.ndarray, n_days: int, xs=XS, ts=TS,
                 forward=forward_extreme):
    """{(T, 向き): (起点の日別件数, x ごとの届いた日別件数 [len(xs) × 日], 式の予測の日別和 [len(xs) × 日])}。

    start: 起点にしてよい足（期間内・ATR がそろう）。距離の比 r =（先の極値 − 終値）÷ ATR を
    T・向きごとに 1 回だけ作り、全 x で使い回す。
    """
    a = atr(h, l, c)
    s = diff_std(c)
    out = {}
    for t in ts:
        for direction, src, kind, sgn in (("up", h, "max", 1.0), ("down", l, "min", -1.0)):
            ext = forward(src, t, kind)
            ok = start & np.isfinite(ext) & np.isfinite(a) & (a > 0) & np.isfinite(s) & (s > 0)
            r = sgn * (ext[ok] - c[ok]) / a[ok]
            d = day[ok]
            n_day = np.bincount(d, minlength=n_days).astype(float)
            hits = np.vstack([np.bincount(d, weights=(r >= x).astype(float), minlength=n_days) for x in xs])
            z = np.outer(xs, a[ok] / (s[ok] * math.sqrt(t)))
            p = np.minimum(np.vectorize(math.erfc)(z / math.sqrt(2)), 1.0) if z.size else z
            formula = np.vstack([np.bincount(d, weights=p[i], minlength=n_days) for i in range(len(xs))])
            out[(t, direction)] = (n_day, hits, formula)
    return out
