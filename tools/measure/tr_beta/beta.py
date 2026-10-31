"""真の値幅の β の検定の計算（事前登録は RESULTS.md §1）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from indigators.btlm_trail.src.core import window_end_scalar
from tools.measure.bar_patterns.patterns import holm

H = 20


def range_pct(d: pd.DataFrame, true_range) -> np.ndarray:
    """真の値幅 ÷ 前の足の終値 × 100（先頭の足は前の足が無いので NaN）。"""
    return true_range(d) / d.close.shift(1).to_numpy() * 100


def rolling_mean(x: np.ndarray, h: int) -> np.ndarray:
    """直近 h 本の平均。"""
    return pd.Series(x).rolling(h).mean().to_numpy()


def past_mean(x: np.ndarray, h: int = H, mean=rolling_mean) -> np.ndarray:
    """直近 h 本（今の足を含む）の平均。"""
    return mean(x, h)


def future_mean(x: np.ndarray, h: int = H, mean=rolling_mean) -> np.ndarray:
    """次の h 本（今の足を含まない）の平均。"""
    return np.r_[mean(x, h)[h:], np.full(h, np.nan)]


def beta_sign(x: np.ndarray, n: int, first: int = 0, window=window_end_scalar) -> np.ndarray:
    """first 以降の足で、x の直近 n 本の OLS の傾きの符号を返す（長さは len(x) − first）。

    +1・−1。0 と計算不能は 0。窓が n 本に満たない足も計算せず 0。
    window: 1 窓の OLS（観測の境界・計算量検定の注入点）。btlm_trail の窓末尾の計算そのもの。
    """
    out = np.zeros(len(x) - first)
    for t in range(max(first, n - 1), len(x)):
        out[t - first] = np.sign(np.nan_to_num(window(x[t - n + 1:t + 1])[2]))
    return out


def measures(x: np.ndarray, first: int, ns, window=window_end_scalar, mean=rolling_mean) -> dict:
    """評価する足（first 以降）の Y・A・β の符号を返す。助走は窓に要る分だけ読む。"""
    lo = max(first - H + 1, 0)
    out = dict(y=future_mean(x[first:], H, mean), a=past_mean(x[lo:], H, mean)[first - lo:])
    for n in ns:
        out[n] = beta_sign(x, n, first, window)
    return out


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


TERMS = {"w": lambda s, n: np.log(s["w"]), "l": lambda s, n: np.log(s[f"l{n}"]), "b": lambda s, n: s[n]}


def model_terms(s: dict, n: int, terms) -> list:
    """§4 の見積もりの項（log の回帰の説明変数）。頼まれた項だけ作る。"""
    return [TERMS[k](s, n) for k in terms]


def fit_linear(v: np.ndarray, s: dict, n: int, terms, ok: np.ndarray, solve=np.linalg.lstsq) -> np.ndarray:
    """v = 定数 + Σ 係数 × 項 の最小二乗の係数。solve は計算量検定の注入点。"""
    x = np.column_stack([np.ones(ok.sum()), *[t[ok] for t in model_terms(s, n, terms)]])
    return solve(x, v[ok], rcond=None)[0]


def linear_value(s: dict, n: int, terms, coef: np.ndarray) -> np.ndarray:
    """fit_linear の係数で計算した値。"""
    return coef[0] + sum(c * t for c, t in zip(coef[1:], model_terms(s, n, terms)))


def fit_log_model(s: dict, n: int, terms, ok: np.ndarray, solve=np.linalg.lstsq) -> np.ndarray:
    """log Y を項で回帰した係数。"""
    return fit_linear(np.log(s["y"]), s, n, terms, ok, solve)


def predict_log_model(s: dict, n: int, terms, coef: np.ndarray) -> np.ndarray:
    """fit_log_model の係数で Y を見積もる。"""
    return np.exp(linear_value(s, n, terms, coef))


def partial_slope(r: np.ndarray, e: np.ndarray, ok: np.ndarray) -> float:
    """偏回帰の傾き: 他の項を除いた r と e の回帰の傾き。r は circular_test の sign の位置に渡す。"""
    return float(np.sum(r[ok] * e[ok]) / np.sum(r[ok] ** 2))


def exact_partial_test(r: np.ndarray, e: np.ndarray, margin: int, rfft=np.fft.rfft) -> tuple[float, float, float]:
    """偏回帰の傾きの円環ずらしを、[margin, N − margin] の全幅で数える（実測・帰無の平均・両側 p）。

    r は固定し e をずらす。e の NaN と r の 0 は除く。分子・分母を畳み込みで全幅まとめて計算する。
    rfft は計算量検定の注入点。
    """
    n = len(r)
    finite = np.isfinite(e)
    vals = np.where(finite, e, 0.0)
    fr, fr2 = rfft(r), rfft(r * r)
    top = np.fft.irfft(fr * np.conj(rfft(vals)), n)
    bottom = np.fft.irfft(fr2 * np.conj(rfft(finite.astype(float))), n)
    slopes = top / bottom
    null = slopes[margin:n - margin + 1]
    center, p = two_sided_p(slopes[0], null)
    return float(slopes[0]), center, p


def gain_interval(y: np.ndarray, base: np.ndarray, pred: np.ndarray, block: np.ndarray, count: int, rng,
                  level: float = 0.95) -> tuple[float, float]:
    """塊（週や四半期）を単位に再標本した、外れの改善（%ポイント）の幅。"""
    n_blocks = int(block.max()) + 1
    wy = np.bincount(block, y, n_blocks)
    wgain = np.bincount(block, np.abs(y - base) - np.abs(y - pred), n_blocks)
    pick = rng.integers(0, n_blocks, (count, n_blocks))
    gains = wgain[pick].sum(axis=1) / wy[pick].sum(axis=1) * 100
    tail = (1 - level) / 2 * 100
    return float(np.percentile(gains, tail)), float(np.percentile(gains, 100 - tail))


def draw_shifts(n: int, count: int, margin: int, rng: np.random.Generator) -> np.ndarray:
    """円環ずらしの幅（[margin, n − margin] の一様乱数）。"""
    return rng.integers(margin, n - margin + 1, count)


def two_sided_p(obs: float, null: np.ndarray) -> tuple[float, float]:
    """帰無の平均と両側 p（§1.5 の式）を返す。"""
    center = float(null.mean())
    return center, float((1 + np.sum(np.abs(null - center) >= abs(obs - center))) / (len(null) + 1))


def circular_test(sign: np.ndarray, target: np.ndarray, stat, shifts, p_value=two_sided_p) -> tuple[float, float, float]:
    """実測・帰無の平均・両側 p を返す。target だけを円環状にずらす。

    stat: 統計量（形は (sign, target, ok) → float）。p_value: 帰無の受け取り先。どちらも計算量検定の注入点。
    """
    finite = np.isfinite(target)
    obs = stat(sign, target, finite & (sign != 0))
    vals = np.where(finite, target, 0.0)
    null = np.array([stat(sign, np.roll(vals, s), np.roll(finite, s) & (sign != 0)) for s in shifts])
    center, p = p_value(obs, null)
    return obs, center, p


def judge(df: pd.DataFrame, stat: str, level: float = 0.05) -> pd.DataFrame:
    """§1.5 の判定を列にする。df は period・tf・n と {stat}・{stat}_null・{stat}_p を持つ。

    IS は全組で Holm。OOS は IS を通過した組だけで Holm、かつ IS と同じ向きで通過。
    """
    out = df.copy()
    is_ = out[out.period == "IS"].set_index(["tf", "n"])
    oos = out[out.period == "OOS"].set_index(["tf", "n"])
    is_holm = pd.Series(holm(is_[f"{stat}_p"].to_numpy()), index=is_.index)
    passed = is_holm.index[is_holm < level]
    oos_holm = pd.Series(holm(oos.loc[passed, f"{stat}_p"].to_numpy()), index=passed) if len(passed) else pd.Series(dtype=float)
    side = lambda frame: np.sign(frame[stat] - frame[f"{stat}_null"])
    same = side(is_).loc[passed] == side(oos).loc[passed]
    final = (oos_holm < level) & same
    keys = pd.MultiIndex.from_frame(out[["tf", "n"]])
    is_row = (out.period == "IS").to_numpy()
    out[f"{stat}_holm"] = np.where(is_row, is_holm.reindex(keys).to_numpy(), oos_holm.reindex(keys).to_numpy())
    out[f"{stat}_pass"] = final.reindex(keys).fillna(False).astype(bool).to_numpy() & ~is_row
    return out
