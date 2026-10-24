"""値幅の分位による閾値（RESULTS.md §1 の事前登録どおり）。

観測の境界: 分位の計算は ``fit(quantile=...)`` の注入点だけを通る。
計算量検定はこの注入点に Test Spy を差して数える（内部名を差し替えない）。
"""
from __future__ import annotations

import numpy as np

from tools.measure.reach_prob import reach as R

METHODS = ("A", "B", "C", "D")
QS = (0.80, 0.95)


def true_range(h, l, c) -> np.ndarray:
    prev = np.concatenate([[np.nan], c[:-1]])
    return np.nanmax(np.vstack([h - l, np.abs(h - prev), np.abs(l - prev)]), axis=0)


def features(h, l, c):
    """y（次の足の真の値幅 ÷ 終値 %）、v（ATR20 ÷ 終値 %）、r（ATR5 ÷ ATR20）。値がそろう足だけ返す。"""
    tr = true_range(h, l, c)
    y = np.concatenate([tr[1:], [np.nan]]) / c * 100
    a20, a5 = R.atr(h, l, c, 20), R.atr(h, l, c, 5)
    v = a20 / c * 100
    r = a5 / a20
    ok = np.isfinite(y) & np.isfinite(v) & np.isfinite(r) & (v > 0)
    return y[ok], v[ok], r[ok], ok


def cells(method: str, v, r, edges) -> np.ndarray:
    """区画の番号。A・B は 0 だけ、C は 0..4、D は 0..9（区分 × 2 + 荒れてきているか）。"""
    if method in ("A", "B"):
        return np.zeros(len(v), dtype=int)
    b = np.digitize(v, edges)
    return b if method == "C" else b * 2 + (r >= 1).astype(int)


def n_cells(method: str) -> int:
    return {"A": 1, "B": 1, "C": 5, "D": 10}[method]


def fit(method: str, y, v, r, q: float, edges, quantile=np.quantile) -> np.ndarray:
    """区画ごとの分位（B は y ÷ v の分位）。区画に足が無ければ NaN。"""
    target = y / v if method == "B" else y
    k = cells(method, v, r, edges)
    out = np.full(n_cells(method), np.nan)
    for i in range(n_cells(method)):
        m = k == i
        if m.any():
            out[i] = quantile(target[m], q)
    return out


def threshold(method: str, params, v, r, edges) -> np.ndarray:
    """照合する足ごとの閾値（%）。"""
    t = params[cells(method, v, r, edges)]
    return t * v if method == "B" else t
