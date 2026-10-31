"""損切りの距離の物差しの比較の計算（事前登録は RESULTS.md §1）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from indigators.btlm_trail.src.core import window_end_scalar
from tools.measure.bar_patterns.patterns import holm


def adverse_moves(high: np.ndarray, low: np.ndarray, close: np.ndarray, t: int) -> tuple[np.ndarray, np.ndarray]:
    """足ごとに、次の t 本の買いの逆行と売りの逆行（終値に対する %）。先が足りない足は NaN。"""
    future_low = pd.Series(low).rolling(t).min().shift(-t).to_numpy()
    future_high = pd.Series(high).rolling(t).max().shift(-t).to_numpy()
    return (close - future_low) / close * 100, (future_high - close) / close * 100


def residual_sigma(close: np.ndarray, n: int, first: int = 0, window=window_end_scalar) -> np.ndarray:
    """first 以降の足で、終値の直近 n 本の OLS の残差の標準偏差。window は計算量検定の注入点。"""
    out = np.full(len(close), np.nan)
    for t in range(max(first, n - 1), len(close)):
        out[t] = window(close[t - n + 1:t + 1])[3]
    return out


def common_events(moves, scales: dict, block: np.ndarray, first: int = 0) -> dict:
    """全物差しで計算できる足から事象を作る。ratio は物差しの名前 → 逆行 ÷ 物差し（買いと売りをつなげる）。"""
    long_, short = moves
    ok = np.isfinite(long_) & np.isfinite(short) & (np.arange(len(long_)) >= first)
    for s in scales.values():
        ok &= np.isfinite(s) & (s > 0)
    adv = np.r_[long_[ok], short[ok]]
    return dict(block=pd.factorize(np.r_[block[ok], block[ok]])[0],
                ratio={k: adv / np.r_[s[ok], s[ok]] for k, s in scales.items()},
                scale={k: np.r_[s[ok], s[ok]] for k, s in scales.items()})


def multiple_for(ratio: np.ndarray, survival: float) -> float:
    """生き残りが survival になる倍数。"""
    return float(np.quantile(ratio, survival))


def hit_rate(ratio: np.ndarray, k: float) -> float:
    """掛かる割合（%）。"""
    return float(np.mean(ratio >= k) * 100)


def distance(ratio: np.ndarray, scale: np.ndarray, survival: float = 0.9) -> float:
    """尺度 1: 同じ生き残りに要る距離の平均（%）。"""
    return multiple_for(ratio, survival) * float(np.mean(scale))


def block_rates(ratio: np.ndarray, block: np.ndarray, k: float) -> np.ndarray:
    """塊ごとの掛かる割合（%）。"""
    n = int(block.max()) + 1
    return np.bincount(block, ratio >= k, n) / np.bincount(block, minlength=n) * 100


def rate_gap(ratio: np.ndarray, block: np.ndarray, k: float, target: float = 10.0) -> float:
    """尺度 2: 塊ごとの掛かる割合と目標の差の絶対値の平均（%ポイント）。"""
    return float(np.mean(np.abs(block_rates(ratio, block, k) - target)))


def two_sided_p(draws: np.ndarray) -> float:
    """再標本の値から、0 を基準にした両側 p。"""
    side = min(np.sum(draws <= 0), np.sum(draws >= 0))
    return float(min(1.0, 2 * (1 + side) / (len(draws) + 1)))


def picks(n_blocks: int, count: int, rng) -> np.ndarray:
    """塊の再標本（行が 1 回の抽出）。"""
    return rng.integers(0, n_blocks, (count, n_blocks))


def compare_distance(ev: dict, name: str, base: str, draws: np.ndarray, survival: float = 0.9, measure=distance) -> dict:
    """尺度 1 の比較: name の距離 ÷ base の距離 − 1（%）。measure は計算量検定の注入点。"""
    block = ev["block"]
    index = [np.flatnonzero(block == b) for b in range(int(block.max()) + 1)]
    value = lambda key, idx: measure(ev["ratio"][key][idx], ev["scale"][key][idx], survival)
    point = (value(name, np.arange(len(block))) / value(base, np.arange(len(block))) - 1) * 100
    out = np.empty(len(draws))
    for i, pick in enumerate(draws):
        idx = np.concatenate([index[b] for b in pick])
        out[i] = (value(name, idx) / value(base, idx) - 1) * 100
    return dict(point=point, lo=float(np.percentile(out, 2.5)), hi=float(np.percentile(out, 97.5)), p=two_sided_p(out))


def compare_gap(ev: dict, name: str, base: str, k_name: float, k_base: float, draws: np.ndarray, target: float = 10.0) -> dict:
    """尺度 2 の比較: name の値 − base の値（%ポイント）。倍数は固定し、四半期の値を再標本する。"""
    block = ev["block"]
    a = np.abs(block_rates(ev["ratio"][name], block, k_name) - target)
    b = np.abs(block_rates(ev["ratio"][base], block, k_base) - target)
    out = (a - b)[draws].mean(axis=1)
    return dict(point=float(a.mean() - b.mean()), lo=float(np.percentile(out, 2.5)), hi=float(np.percentile(out, 97.5)), p=two_sided_p(out))


def judge(df: pd.DataFrame, measure: str, level: float = 0.05) -> pd.DataFrame:
    """§1.5 の判定を列にする。df は period・key と {measure}_point・{measure}_p を持つ（key は比較の名前）。"""
    out = df.copy()
    is_ = out[out.period == "IS"].set_index("key")
    oos = out[out.period == "OOS"].set_index("key")
    is_holm = pd.Series(holm(is_[f"{measure}_p"].to_numpy()), index=is_.index)
    passed = is_holm.index[is_holm < level]
    oos_holm = pd.Series(holm(oos.loc[passed, f"{measure}_p"].to_numpy()), index=passed) if len(passed) else pd.Series(dtype=float)
    same = np.sign(is_.loc[passed, f"{measure}_point"]) == np.sign(oos.loc[passed, f"{measure}_point"])
    final = (oos_holm < level) & same
    is_row = (out.period == "IS").to_numpy()
    out[f"{measure}_holm"] = np.where(is_row, is_holm.reindex(out.key).to_numpy(), oos_holm.reindex(out.key).to_numpy())
    out[f"{measure}_pass"] = final.reindex(out.key).fillna(False).astype(bool).to_numpy() & ~is_row
    return out


def verdict(row, min_distance: float = 5.0, min_gap: float = 1.0) -> str:
    """§1.6 の判定（OOS の行）。"""
    if (row.distance_pass and row.distance_point <= -min_distance) or (row.gap_pass and row.gap_point <= -min_gap):
        return "σ が良い"
    if (row.distance_pass and row.distance_point >= min_distance) or (row.gap_pass and row.gap_point >= min_gap):
        return "ATR が良い"
    return "差があるとは言えない"
