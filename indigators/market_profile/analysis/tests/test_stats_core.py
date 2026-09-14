"""stats_core の単体テスト（合成データ・seed 固定・決定論）。"""

from __future__ import annotations

import math

import numpy as np
import pytest

from mp_stats import stats_core as sc


# --------------------------------------------------------------------------- #
# 半分散
# --------------------------------------------------------------------------- #
def test_semivariance_identity():
    """恒等式 SV⁻ + SV⁺ = mean(x²)（r==0 非寄与を含め成立）。"""
    rng = np.random.default_rng(7)
    x = rng.normal(size=1000)
    x[::50] = 0.0  # ゼロ観測を混ぜる
    total = sc.semivariance_neg(x) + sc.semivariance_pos(x)
    assert total == pytest.approx(float(np.mean(x**2)), rel=1e-12)


def test_semivariance_signs():
    x = np.array([-2.0, 1.0, 0.0])
    assert sc.semivariance_neg(x) == pytest.approx(4.0 / 3.0)
    assert sc.semivariance_pos(x) == pytest.approx(1.0 / 3.0)
    assert sc.semivariance_neg(np.array([])) == 0.0


# --------------------------------------------------------------------------- #
# OLS + HAC
# --------------------------------------------------------------------------- #
def test_ols_recovers_coefficients():
    rng = np.random.default_rng(11)
    n = 2000
    x = rng.normal(size=n)
    y = 1.5 + 0.8 * x + rng.normal(scale=0.1, size=n)
    X = np.column_stack([np.ones(n), x])
    beta, resid, r2 = sc.ols(X, y)
    assert beta[0] == pytest.approx(1.5, abs=0.02)
    assert beta[1] == pytest.approx(0.8, abs=0.02)
    assert r2 > 0.97
    assert resid.shape == (n,)


def test_hac_size_iid():
    """iid 誤差で HAC t 検定の棄却率 ≈ α（サイズ制御）。"""
    rng = np.random.default_rng(42)
    n, reps, alpha = 500, 500, 0.05
    lag = sc.newey_west_lag(n)
    rejections = 0
    for _ in range(reps):
        x = rng.normal(size=n)
        y = rng.normal(size=n)  # 真の β_x = 0
        X = np.column_stack([np.ones(n), x])
        beta, se = sc.ols_hac(X, y, lag)
        t = beta[1] / se[1]
        p = 2.0 * (1.0 - sc.norm_cdf(abs(t)))
        if p < alpha:
            rejections += 1
    rate = rejections / reps
    assert 0.02 <= rate <= 0.09, f"size distortion: {rate}"


def test_hac_widens_se_under_ar1():
    """AR(1)（φ=0.5）誤差では NW SE（lag=L）> lag=0 SE。"""
    rng = np.random.default_rng(3)
    n = 3000
    e = np.empty(n)
    e[0] = rng.normal()
    for t in range(1, n):
        e[t] = 0.5 * e[t - 1] + rng.normal()
    X = np.ones((n, 1))
    _, se0 = sc.ols_hac(X, e, lag=0)
    _, seL = sc.ols_hac(X, e, lag=sc.newey_west_lag(n))
    assert seL[0] > se0[0] * 1.3


# --------------------------------------------------------------------------- #
# 符号検定（厳密二項）
# --------------------------------------------------------------------------- #
def test_sign_test_exact_points():
    # X~Bin(10,0.5): P(X<=2) = (1+10+45)/1024 = 56/1024
    assert sc.sign_test_pvalue(2, 8) == pytest.approx(2 * 56 / 1024, rel=1e-12)
    assert sc.sign_test_pvalue(8, 2) == pytest.approx(2 * 56 / 1024, rel=1e-12)
    # 完全対称は p=1（min クリップ）
    assert sc.sign_test_pvalue(5, 5) == 1.0
    # n=0 は情報なし
    assert sc.sign_test_pvalue(0, 0) == 1.0
    # 大 n の片寄り（オーバーフローしない）
    assert sc.sign_test_pvalue(2000, 1500) < 1e-15


# --------------------------------------------------------------------------- #
# Wilcoxon 符号順位
# --------------------------------------------------------------------------- #
def test_wilcoxon_symmetric_null():
    rng = np.random.default_rng(5)
    x = rng.normal(size=2000)
    z, p = sc.wilcoxon_signed_rank(x)
    assert p > 0.05


def test_wilcoxon_detects_shift():
    rng = np.random.default_rng(6)
    x = rng.normal(loc=0.2, size=2000)
    z, p = sc.wilcoxon_signed_rank(x)
    assert p < 1e-6
    assert z > 0


def test_wilcoxon_zero_handling():
    z, p = sc.wilcoxon_signed_rank(np.zeros(10))
    assert (z, p) == (0.0, 1.0)


# --------------------------------------------------------------------------- #
# ブートストラップ薄ラッパ
# --------------------------------------------------------------------------- #
def test_stationary_bootstrap_indices_shape_and_range():
    rng = np.random.default_rng(9)
    idx = sc.stationary_bootstrap_indices(100, 5, rng)
    assert idx.shape == (100,)
    assert idx.min() >= 0 and idx.max() < 100


def test_pw_block_len_positive():
    rng = np.random.default_rng(10)
    x = rng.normal(size=500)
    b = sc.pw_block_len(x)
    assert isinstance(b, int) and b >= 1
