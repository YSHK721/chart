"""TDD: adapter/validation/var_backtests.py（詳細設計 §5.4 / §9.4）。

chi2_sf_df1=erfc(√(x/2))・Kupiec POF・Christoffersen 独立性の論文校正。
"""
from __future__ import annotations

import math

import pytest

from simulator.adapter.validation.var_backtests import (
    VarBacktests,
    chi2_sf_df1,
    norm_cdf,
)


class TestChi2SfDf1:
    def test_zero_returns_one(self):
        assert chi2_sf_df1(0.0) == 1.0

    def test_negative_returns_one(self):
        assert chi2_sf_df1(-5.0) == 1.0

    def test_critical_value_3841_is_005(self):
        # χ²(1) 0.05 臨界値 3.841 → p≈0.05
        p = chi2_sf_df1(3.841)
        assert math.isclose(p, 0.05, abs_tol=1e-3)


class TestNormCdf:
    def test_zero_is_half(self):
        assert math.isclose(norm_cdf(0.0), 0.5, abs_tol=1e-12)

    def test_196_is_0975(self):
        assert math.isclose(norm_cdf(1.96), 0.975, abs_tol=1e-3)


class TestKupiec:
    def test_pi_equals_alpha_high_p(self):
        # 実到達率 = α=0.05（100 週中 5 ヒット）→ LR_POF≈0 → p≈1
        t = VarBacktests()
        hits = [1] * 5 + [0] * 95
        p = t.kupiec(hits, alpha=0.05)
        assert p > 0.9

    def test_far_from_alpha_low_p(self):
        # 実到達率 0.50（>>0.05）→ LR_POF 大 → p 小
        t = VarBacktests()
        hits = [1] * 50 + [0] * 50
        p = t.kupiec(hits, alpha=0.05)
        assert p < 0.05

    def test_kupiec_known_value(self):
        # 手計算 oracle: n=100, x=10, alpha=0.05
        # LR=-2[10 ln0.05 + 90 ln0.95 - 10 ln0.10 - 90 ln0.90]
        n, x, a = 100, 10, 0.05
        pi = x / n
        lr = -2 * (
            x * math.log(a) + (n - x) * math.log(1 - a)
            - x * math.log(pi) - (n - x) * math.log(1 - pi)
        )
        expected = math.erfc(math.sqrt(lr / 2.0))
        t = VarBacktests()
        hits = [1] * x + [0] * (n - x)
        assert math.isclose(t.kupiec(hits, alpha=a), expected, rel_tol=1e-9)

    def test_all_zero_hits_pi_zero_limit(self):
        # pi_hat=0 極限：項 0 扱い（例外を出さない）
        t = VarBacktests()
        p = t.kupiec([0] * 50, alpha=0.05)
        assert 0.0 <= p <= 1.0


class TestChristoffersen:
    def test_independent_sequence_high_p(self):
        # 交互でない散発的ヒットの独立性（決定論一致を手計算と照合）
        t = VarBacktests()
        hits = [0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0, 0]
        p = t.christoffersen_independence(hits)
        assert 0.0 <= p <= 1.0

    def test_clustered_sequence_low_p(self):
        # クラスタ化（11 が連続）→ 独立性棄却方向（p 小）
        t = VarBacktests()
        hits = [0] * 10 + [1] * 10
        p = t.christoffersen_independence(hits)
        assert p < 0.5
