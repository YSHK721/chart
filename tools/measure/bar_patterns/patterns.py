"""足の並びの分類と、並びごとの t・円環ずらしの帰無（RESULTS.md §1 の事前登録どおり）。

観測の境界: 群ごとの和の計算は ``null_tstats(moments=...)`` の注入点だけを通る。
計算量検定はこの注入点に Test Spy を差して数える（内部名を差し替えない）。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SIZE_WINDOW = 100
MIN_COUNT = 30
#: 族 → 長さ（RESULTS.md §1.3）。A は向きだけ（底 2）、B は向き × 大きさ（底 4）。
FAMILIES = {"A": (1, 2, 3, 4, 5), "B": (1, 2, 3)}


def classify(o: np.ndarray, c: np.ndarray, size_window: int = SIZE_WINDOW):
    """足ごとの記号を返す。向き記号（陽 1・陰 0・不明 -1）と向き×大きさ記号（0..3・不明 -1）。

    大きさは、その足より前の size_window 本の |実体| の中央値以上を大とする。
    """
    body = c - o
    up = np.where(body > 0, 1, np.where(body < 0, 0, -1))
    absb = np.abs(body)
    n = len(o)
    med = np.full(n, np.nan)
    if n > size_window:
        win = np.lib.stride_tricks.sliding_window_view(absb, size_window)[:-1]
        med[size_window:] = np.median(win, axis=1)
    big = np.where(np.isnan(med), -1, (absb >= med).astype(int))
    sym_a = up
    sym_b = np.where((up < 0) | (big < 0), -1, up * 2 + big)
    return sym_a, sym_b


def pattern_labels(sym: np.ndarray, base: int, k: int) -> np.ndarray:
    """最後の足で確定する長さ k の並びの番号（0..base^k-1）。不明な足を含めば -1。

    番号の桁は古い足から順に上位桁（例 A・k=3 の "UUD" は 1·4 + 1·2 + 0）。
    """
    n = len(sym)
    lab = np.full(n, -1, dtype=np.int64)
    if n < k:
        return lab
    win = np.lib.stride_tricks.sliding_window_view(sym, k)
    ok = (win >= 0).all(axis=1)
    weights = base ** np.arange(k - 1, -1, -1)
    lab[k - 1:] = np.where(ok, win @ weights, -1)
    return lab


def pattern_name(family: str, k: int, code: int) -> str:
    base = 2 if family == "A" else 4
    digits = []
    for _ in range(k):
        digits.append(code % base)
        code //= base
    digits.reverse()
    if family == "A":
        return "".join("U" if d else "D" for d in digits)
    return " ".join(("大" if d % 2 else "小") + ("陽" if d // 2 else "陰") for d in digits)


def forward_returns(c: np.ndarray, h: int) -> np.ndarray:
    """終値から h 本後の終値までの対数収益。末尾 h 本は NaN。"""
    lc = np.log(c)
    f = np.full(len(c), np.nan)
    f[:-h] = lc[h:] - lc[:-h]
    return f


@dataclass(frozen=True)
class Moments:
    count: np.ndarray
    s1: np.ndarray
    s2: np.ndarray


def grouped_moments(labels: np.ndarray, values: np.ndarray, valid: np.ndarray, n_groups: int) -> Moments:
    """群ごとの件数・和・二乗和（1 回の走査で 3 本の bincount）。"""
    sel = (labels >= 0) & valid
    lab = labels[sel]
    v = values[sel]
    return Moments(
        np.bincount(lab, minlength=n_groups).astype(float),
        np.bincount(lab, weights=v, minlength=n_groups),
        np.bincount(lab, weights=v * v, minlength=n_groups),
    )


def tstats(m: Moments, grand_mean: float) -> np.ndarray:
    """t =（群平均 − 全体平均）÷（群の標準偏差 ÷ √n）。n < 2 や分散 0 は NaN。"""
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = m.s1 / m.count
        var = (m.s2 - m.count * mean * mean) / (m.count - 1)
        t = (mean - grand_mean) / np.sqrt(var / m.count)
    t[(m.count < 2) | ~(var > 0)] = np.nan
    return t


def null_tstats(label_sets, fwd: np.ndarray, shifts, moments=grouped_moments):
    """各ずらし幅で先の値動きを円環状にずらし、全並びの t を返す（形 = ずらし数 × 並び総数）。

    label_sets: [(labels, n_groups), ...]。列はこの順に連結する。
    """
    valid = np.isfinite(fwd)
    vals = np.where(valid, fwd, 0.0)
    grand = vals[valid].mean()
    widths = [g for _, g in label_sets]
    out = np.empty((len(shifts), sum(widths)))
    for i, s in enumerate(shifts):
        vr = np.roll(vals, s)
        mr = np.roll(valid, s)
        col = 0
        for (lab, g) in label_sets:
            out[i, col:col + g] = tstats(moments(lab, vr, mr, g), grand)
            col += g
    return out


def observed(label_sets, fwd: np.ndarray, moments=grouped_moments):
    """本物の t と出現数（列は null_tstats と同じ順）。"""
    valid = np.isfinite(fwd)
    vals = np.where(valid, fwd, 0.0)
    grand = vals[valid].mean()
    ts, ns, means = [], [], []
    for lab, g in label_sets:
        m = moments(lab, vals, valid, g)
        ts.append(tstats(m, grand))
        ns.append(m.count)
        with np.errstate(invalid="ignore", divide="ignore"):
            means.append(m.s1 / m.count - grand)
    return np.concatenate(ts), np.concatenate(ns), np.concatenate(means)


def bh_qvalues(p: np.ndarray) -> np.ndarray:
    """Benjamini–Hochberg の q 値。"""
    p = np.asarray(p, dtype=float)
    n = len(p)
    order = np.argsort(p)
    ranked = p[order] * n / np.arange(1, n + 1)
    q = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.minimum(q, 1.0)
    return out


def holm(p: np.ndarray) -> np.ndarray:
    """Holm 法の補正後 p。"""
    p = np.asarray(p, dtype=float)
    n = len(p)
    order = np.argsort(p)
    adj = np.maximum.accumulate(p[order] * (n - np.arange(n)))
    out = np.empty(n)
    out[order] = np.minimum(adj, 1.0)
    return out
