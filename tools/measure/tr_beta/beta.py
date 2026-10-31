"""真の値幅の β の検定の計算（事前登録は RESULTS.md §1）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from indigators.btlm_trail.src.core import rolling_ols_window_end

H = 20


def range_pct(d: pd.DataFrame, true_range) -> np.ndarray:
    """真の値幅 ÷ 前の足の終値 × 100（先頭の足は前の足が無いので NaN）。"""
    return true_range(d) / d.close.shift(1).to_numpy() * 100


def past_mean(x: np.ndarray, h: int = H) -> np.ndarray:
    """直近 h 本（今の足を含む）の平均。"""
    return pd.Series(x).rolling(h).mean().to_numpy()


def future_mean(x: np.ndarray, h: int = H) -> np.ndarray:
    """次の h 本（今の足を含まない）の平均。"""
    return pd.Series(x).rolling(h).mean().shift(-h).to_numpy()


def beta_sign(x: np.ndarray, n: int, fit=rolling_ols_window_end) -> np.ndarray:
    """x の直近 n 本の OLS の傾きの符号（+1・−1。0 と計算不能は 0）。窓が n 本に満たない足も 0。"""
    b = fit(x, n)[2]
    s = np.sign(np.nan_to_num(b))
    s[:n - 1] = 0
    return s


def hit_rate(sign: np.ndarray, target: np.ndarray, ok: np.ndarray) -> float:
    """検定 1: β > 0 と target（1 = Y > A）の一致率。"""
    return float(np.mean((sign[ok] > 0) == (target[ok] > 0)))


def mean_gap(sign: np.ndarray, target: np.ndarray, ok: np.ndarray) -> float:
    """検定 2: β > 0 の target の平均 − β < 0 の target の平均。"""
    return float(target[ok & (sign > 0)].mean() - target[ok & (sign < 0)].mean())


def fit_level(y: np.ndarray, w: np.ndarray, ok: np.ndarray) -> tuple[float, float]:
    """log y = a + b log w の最小二乗（a, b）。"""
    b, a = np.polyfit(np.log(w[ok]), np.log(y[ok]), 1)
    return float(a), float(b)


def level_residual(y: np.ndarray, w: np.ndarray, a: float, b: float) -> np.ndarray:
    """e = log y −（a + b log w）。"""
    return np.log(y) - (a + b * np.log(w))


def fit_sign(e: np.ndarray, sign: np.ndarray, ok: np.ndarray) -> float:
    """e = c·sign の最小二乗の c（定数項なし。sign は ±1）。"""
    return float(np.mean(e[ok] * sign[ok]))


def relative_error(y: np.ndarray, pred: np.ndarray, ok: np.ndarray) -> float:
    """外れの平均 ÷ y の平均（%）。"""
    return float(np.mean(np.abs(y[ok] - pred[ok])) / np.mean(y[ok]) * 100)


def draw_shifts(n: int, count: int, margin: int, rng: np.random.Generator) -> np.ndarray:
    """円環ずらしの幅（[margin, n − margin] の一様乱数）。"""
    return rng.integers(margin, n - margin + 1, count)


def circular_test(sign: np.ndarray, target: np.ndarray, stat, shifts) -> tuple[float, float, float]:
    """実測・帰無の平均・両側 p を返す。target だけを円環状にずらす。

    stat: 統計量（観測の境界・計算量検定の注入点）。形は (sign, target, ok) → float。
    """
    valid = np.isfinite(target) & (sign != 0)
    obs = stat(sign, target, valid)
    finite = np.isfinite(target)
    vals = np.where(finite, target, 0.0)
    null = np.empty(len(shifts))
    for i, s in enumerate(shifts):
        null[i] = stat(sign, np.roll(vals, s), np.roll(finite, s) & (sign != 0))
    center = null.mean()
    p = (1 + np.sum(np.abs(null - center) >= abs(obs - center))) / (len(shifts) + 1)
    return obs, float(center), float(p)
