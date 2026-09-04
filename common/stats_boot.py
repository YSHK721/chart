"""stats_boot — 定常ブートストラップ統計核（純 numpy/math・共有プリミティブ層）。

Politis-White(2004) 自動ブロック長・Politis-Romano(1994) 定常ブートの単一定義。
simulator（adapter/validation）と mp_stats（分析パイプライン）の両方が本モジュールへ
依存する（ISSUE-091 A1: mp_stats → simulator.adapter private 直結の安定度逆転を、中立共有核への
抽出で解消。simulator 側は従来名の再エクスポートで互換維持）。

本モジュールはブートストラップ核だけを持つ。核の**利用者**である検定は別モジュールにある
（ISSUE-479 Wave2 C-3 で分割・後方互換の再エクスポートは置かない）:
    Hansen SPA           : common の hansen_spa（本モジュールを利用する）。
    VaR 被覆検定         : common の var_backtests（ブートストラップを使わない）。

出典:
    Politis & White (2004) "Automatic Block-Length Selection for the Dependent
    Bootstrap", Econometric Reviews 23(1):53-70.
    Politis & Romano (1994) "The Stationary Bootstrap", JASA 89(428).
"""
from __future__ import annotations

import math

import numpy as np


def norm_cdf(x: float) -> float:
    """標準正規 CDF Φ（SPA studentize 用にも供給）。"""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bootstrap_std(F: "np.ndarray", seed: int, B: int, block: int) -> "np.ndarray":
    """studentize 用 ω̂_k（ブート分布の標準偏差）を別 rng で推定する。"""
    n, K = F.shape
    rng = np.random.default_rng(seed + 1)
    boot_means = np.empty((B, K))
    for b in range(B):
        idx = stationary_bootstrap_indices(n, block, rng)
        boot_means[b] = F[idx].mean(axis=0)
    return np.sqrt(n) * boot_means.std(axis=0, ddof=0)


def flat_top_weight(s: float) -> float:
    """flat-top lag window λ(s) = 1(|s|<=1/2), 2(1-|s|)(1/2<|s|<=1), 0 else（PW2004）。"""
    a = abs(s)
    if a <= 0.5:
        return 1.0
    if a <= 1.0:
        return 2.0 * (1.0 - a)
    return 0.0


def autocorr(xc: "np.ndarray", c0: float, k: int) -> float:
    """中心化系列 xc・分散 c0 の lag-k 標本自己相関 ρ̂(k)（÷n 正規化）。"""
    n = xc.size
    if k <= 0 or k >= n or c0 <= 0:
        return 0.0
    return float(xc[:-k] @ xc[k:]) / n / c0


def pw_block_len_one(x: "np.ndarray") -> float:
    """Politis-White(2004) 定常ブート最適ブロック長 b_opt を 1 系列に対し算出する。

    Politis & White (2004) "Automatic Block-Length Selection for the Dependent
    Bootstrap", Econometric Reviews 23(1):53-70 の固定手続き（D3）:
      1. ρ̂(k) を lag k=1.. で計算（÷n 正規化）
      2. 自己相関打切り m̂: |ρ̂(m+1..m+KN)| が全て c·√(log10 n / n) 未満となる最小 m（c=2,
         KN=⌈√(log10 n)⌉+1 連続規則）。lag window 幅 M = 2·m̂（K_n=⌈2√(log10 n)⌉ で上限）
      3. flat-top λ(s) で Ĝ=Σ_{k=-M..M} λ(k/M)|k|ρ̂(k)、g0=1+2Σ_{k=1..M} λ(k/M)ρ̂(k)
      4. 定常ブート: D̂=2·g0²、b_opt=(2·Ĝ²/D̂)^(1/3)·n^(1/3)
    """
    n = x.size
    xc = x - x.mean()
    c0 = float(xc @ xc) / n
    if c0 <= 0:
        return 1.0
    K_n = max(1, math.ceil(2.0 * math.sqrt(math.log10(n))))
    crit = 2.0 * math.sqrt(math.log10(n) / n)
    KN = max(1, math.ceil(math.sqrt(math.log10(n))) + 1)
    # 自己相関打切り m̂（c=2 規則）
    m_hat = K_n
    for m in range(1, n):
        run = [abs(autocorr(xc, c0, m + j)) < crit for j in range(1, KN + 1) if m + j < n]
        if run and all(run):
            m_hat = m - 1
            break
    M = max(1, min(2 * m_hat, n - 1))
    G = 0.0
    g_sum = 0.0
    for k in range(1, M + 1):
        w = flat_top_weight(k / M)
        rk = autocorr(xc, c0, k)
        G += 2.0 * w * k * rk  # ±k 対称性で 2 倍集約
        g_sum += w * rk
    g0 = 1.0 + 2.0 * g_sum
    D = 2.0 * g0 * g0
    if D <= 0 or G == 0.0:
        return 1.0
    return (2.0 * G * G / D) ** (1.0 / 3.0) * n ** (1.0 / 3.0)


def pw_block_len(F: "np.ndarray") -> int:
    """Politis-White(2004) 自動ブロック長（各候補列の b_opt の中央値・D3）。"""
    n = F.shape[0]
    bs = [pw_block_len_one(F[:, j]) for j in range(F.shape[1])]
    block = int(round(float(np.median(bs))))
    return max(1, min(block, n))


def stationary_bootstrap_indices(n: int, block: int, rng) -> "np.ndarray":
    """Politis-Romano(1994) 定常ブート index 列（幾何 block・wrap）。"""
    p = 1.0 / block if block > 0 else 1.0
    idx = np.empty(n, dtype=int)
    idx[0] = int(rng.integers(n))
    for t in range(1, n):
        if rng.random() < p:
            idx[t] = int(rng.integers(n))
        else:
            idx[t] = (idx[t - 1] + 1) % n
    return idx
