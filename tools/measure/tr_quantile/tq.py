"""値幅の分位による閾値（RESULTS.md §1 の事前登録どおり）。

観測の境界: 分位の計算は ``fit(quantile=...)`` の注入点だけを通る。
計算量検定はこの注入点に Test Spy を差して数える（内部名を差し替えない）。
"""
from __future__ import annotations

import numpy as np

from tools.measure.reach_prob import reach as R

METHODS = ("A", "B", "C", "D")
#: §3（荒れ方の見積もりを混ぜる）: E = 見積もり f の段、F = f で割る
METHODS_MIX = METHODS + ("E", "F")
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


def features_mix(h, l, c):
    """§3.1: y、v、r に加えて、見積もりの説明に使う 3 つ（ATR5%・ATR20%・ATR100%）。ATR100 がそろう足だけ返す。"""
    tr = true_range(h, l, c)
    y = np.concatenate([tr[1:], [np.nan]]) / c * 100
    a5, a20, a100 = (R.atr(h, l, c, n) for n in (5, 20, 100))
    x = np.column_stack([a5, a20, a100]) / c[:, None] * 100
    v = x[:, 1]
    r = a5 / a20
    ok = np.isfinite(y) & np.isfinite(x).all(axis=1) & (v > 0)
    return y[ok], v[ok], r[ok], x[ok], ok


def har_fit(y, x) -> np.ndarray:
    """f = b0 + b1·x1 + b2·x2 + b3·x3 の係数（最小二乗）。"""
    a = np.column_stack([np.ones(len(y)), x])
    return np.linalg.lstsq(a, y, rcond=None)[0]


def har_predict(b, x, floor: float) -> np.ndarray:
    """見積もり f。0 以下は floor（IS の f の最小の正の値）に置き換える。"""
    f = b[0] + x @ b[1:]
    return np.where(f > 0, f, floor)


def cells(method: str, v, r, edges, f=None, edges_f=None) -> np.ndarray:
    """区画の番号。A・B・F は 0 だけ、C は 0..4、D は 0..9（区分 × 2 + 荒れてきているか）、E は f の 0..4。"""
    if method in ("A", "B", "F"):
        return np.zeros(len(v), dtype=int)
    if method == "E":
        return np.digitize(f, edges_f)
    b = np.digitize(v, edges)
    return b if method == "C" else b * 2 + (r >= 1).astype(int)


def n_cells(method: str) -> int:
    return {"A": 1, "B": 1, "C": 5, "D": 10, "E": 5, "F": 1}[method]


def _scale(method: str, v, f):
    return v if method == "B" else (f if method == "F" else None)


def fit(method: str, y, v, r, q: float, edges, quantile=np.quantile, f=None, edges_f=None) -> np.ndarray:
    """区画ごとの分位（B は y ÷ v、F は y ÷ f の分位）。区画に足が無ければ NaN。"""
    sc = _scale(method, v, f)
    target = y / sc if sc is not None else y
    k = cells(method, v, r, edges, f, edges_f)
    out = np.full(n_cells(method), np.nan)
    for i in range(n_cells(method)):
        m = k == i
        if m.any():
            out[i] = quantile(target[m], q)
    return out


def threshold(method: str, params, v, r, edges, f=None, edges_f=None) -> np.ndarray:
    """照合する足ごとの閾値（%）。"""
    t = params[cells(method, v, r, edges, f, edges_f)]
    sc = _scale(method, v, f)
    return t * sc if sc is not None else t
