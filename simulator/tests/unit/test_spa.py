"""TDD: adapter/validation/spa.py（詳細設計 §5.5 / §9.5・NFR-D3）。

seed 固定再現・方向性（帰無→p 大 / 強優位→p 小）・p∈[0,1]。
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from simulator.adapter.validation.spa import HansenSpa, _pw_block_len


def _matrix(means, n=120, sd=0.01, seed=0):
    rng = np.random.default_rng(seed)
    K = len(means)
    F = np.empty((n, K))
    for k, mu in enumerate(means):
        F[:, k] = rng.normal(mu, sd, size=n)
    return F.tolist()


class TestSpaReproducibility:
    def test_same_seed_identical_pvalue(self):
        spa = HansenSpa()
        fm = _matrix([0.0, 0.0, 0.0])
        p1 = spa.spa_pvalue(fm, seed=42, B=500)
        p2 = spa.spa_pvalue(fm, seed=42, B=500)
        assert p1 == p2

    def test_pvalue_in_unit_interval(self):
        spa = HansenSpa()
        fm = _matrix([0.0, 0.001, -0.001])
        p = spa.spa_pvalue(fm, seed=1, B=500)
        assert 0.0 <= p <= 1.0


class TestSpaDirectionality:
    def test_null_has_large_pvalue(self):
        # 全候補 平均 0（H0: max E[f_k] <= 0 が真）→ p 大
        spa = HansenSpa()
        fm = _matrix([0.0, 0.0, 0.0], seed=11)
        p = spa.spa_pvalue(fm, seed=11, B=800)
        assert p > 0.10

    def test_strong_winner_has_small_pvalue(self):
        # 1 候補が強く正（平均 >> 0）→ p 小
        spa = HansenSpa()
        fm = _matrix([0.02, 0.0, 0.0], n=150, sd=0.01, seed=5)
        p = spa.spa_pvalue(fm, seed=5, B=800)
        assert p < 0.05


# ---------------------------------------------------------------------------
# Politis-White(2004) 自動ブロック長 独立オラクル（写経禁止・別経路で手計算）
# ---------------------------------------------------------------------------


def _pw_oracle_block_len(series: "np.ndarray") -> int:
    """PW(2004) 定常ブート最適ブロック長を実装と独立な別経路で算出する手計算オラクル。

    Politis & White (2004) "Automatic Block-Length Selection for the Dependent
    Bootstrap", Econometric Reviews 23(1):53-70 の固定手続き:
      - 自己相関 ρ̂(k), k=1..K_n を直接（生の自己共分散比）で計算
      - K_n = ⌈2·√(log10 n)⌉ : lag 上限
      - 打切り m̂: |ρ̂(m+k)| < c·√(log10 n / n) が KN 連続する最小 m（c=2）
      - M = 2·m̂（lag window 幅）
      - flat-top λ(s)=1(|s|<=1/2), 2(1-|s|)(1/2<|s|<=1), 0 else
      - Ĝ = Σ_{k=-M..M} λ(k/M)|k|ρ̂(k) ; g0 = 1 + 2Σ_{k=1..M} λ(k/M)ρ̂(k)
      - 定常ブート: D̂ = 2·g0² ; b_opt = (2·Ĝ²/D̂)^(1/3)·n^(1/3)
    実装（spa._pw_block_len）の写経ではなく、上式を素朴に直接展開して照合する。
    """
    x = np.asarray(series, dtype=float)
    n = x.size
    xc = x - x.mean()
    c0 = float(xc @ xc) / n
    if c0 <= 0:
        return 1

    def rho(k: int) -> float:
        return float(xc[:-k] @ xc[k:]) / n / c0 if 0 < k < n else (1.0 if k == 0 else 0.0)

    K_n = max(1, math.ceil(2.0 * math.sqrt(math.log10(n))))
    crit = 2.0 * math.sqrt(math.log10(n) / n)
    KN = max(1, math.ceil(math.sqrt(math.log10(n))) + 1)
    # 打切り m̂: ρ̂(m+1..m+KN) が全て |ρ| < crit になる最小 m
    m_hat = 0
    for m in range(1, n):
        run = [abs(rho(m + j)) < crit for j in range(1, KN + 1) if m + j < n]
        if run and all(run):
            m_hat = m - 1
            break
    else:
        m_hat = K_n
    M = max(1, min(2 * m_hat, n - 1))

    def lam(s: float) -> float:
        a = abs(s)
        if a <= 0.5:
            return 1.0
        if a <= 1.0:
            return 2.0 * (1.0 - a)
        return 0.0

    G = 0.0
    g_sum = 0.0
    for k in range(1, M + 1):
        w = lam(k / M)
        rk = rho(k)
        G += 2.0 * w * k * rk  # 対称性: k と -k を 2 倍で集約
        g_sum += w * rk
    g0 = 1.0 + 2.0 * g_sum
    D = 2.0 * g0 * g0
    if D <= 0 or G == 0.0:
        return 1
    b_opt = (2.0 * G * G / D) ** (1.0 / 3.0) * n ** (1.0 / 3.0)
    return max(1, min(int(round(b_opt)), n))


class TestPolitisWhiteBlockLength:
    def test_matches_independent_oracle_on_ar1(self):
        # 既知 AR(1)（ρ=0.5）系列でブロック長が独立オラクル手計算値と一致。
        rng = np.random.default_rng(7)
        n = 200
        rho = 0.5
        e = rng.normal(0.0, 1.0, size=n)
        x = np.empty(n)
        x[0] = e[0]
        for t in range(1, n):
            x[t] = rho * x[t - 1] + e[t]
        F = x.reshape(-1, 1)  # 単一列 → median == その列の b_opt
        expected = _pw_oracle_block_len(x)
        got = _pw_block_len(F)
        # 整数丸めの ±1 を許容（独立オラクルとの手続き同値性照合）
        assert abs(got - expected) <= 1

    def test_block_len_grows_with_persistence(self):
        # 持続性が高い AR(1)（ρ=0.8）は ρ=0.2 よりブロック長が大きい（理論オーダー）。
        def ar1(rho, seed, n=300):
            rng = np.random.default_rng(seed)
            e = rng.normal(0.0, 1.0, size=n)
            x = np.empty(n)
            x[0] = e[0]
            for t in range(1, n):
                x[t] = rho * x[t - 1] + e[t]
            return x.reshape(-1, 1)

        b_low = _pw_block_len(ar1(0.2, 1))
        b_high = _pw_block_len(ar1(0.8, 1))
        assert b_high > b_low

    def test_block_len_positive_and_bounded(self):
        rng = np.random.default_rng(3)
        F = rng.normal(0.0, 1.0, size=(120, 3))
        b = _pw_block_len(F)
        assert 1 <= b <= 120

    def test_white_noise_block_len_is_small(self):
        # 無相関系列 → 最適ブロック長は小さい（理論: iid なら b≈1 オーダー）。
        rng = np.random.default_rng(99)
        F = rng.normal(0.0, 1.0, size=(250, 1))
        b = _pw_block_len(F)
        assert b <= 5
