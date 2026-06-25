"""TDD: adapter/indicator/gk_har_estimator.py（詳細設計 §5.2 / §9.2）。

HAR OLS 点予測 σ̂=√(exp(μ̂))・窓<window→None・RS≤0→None・特異→None。
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from simulator.adapter.indicator.gk_har_estimator import GkHarEstimator


class TestForecastGuards:
    def test_window_not_reached_returns_none(self):
        est = GkHarEstimator()
        rs = [0.01] * 5  # window=10 未満
        sp, sm = est.forecast(rs, rs, window=10, nw_lag=4)
        assert sp is None and sm is None

    def test_non_positive_rs_returns_none(self):
        est = GkHarEstimator()
        rs = [0.01] * 14 + [0.0]  # log 不能（RS<=0）
        sp, sm = est.forecast(rs, rs, window=15, nw_lag=4)
        assert sp is None and sm is None

    def test_constant_series_singular_returns_none(self):
        # 完全定数 → 説明変数が定数列で特異 → None
        est = GkHarEstimator()
        rs = [0.02] * 20
        sp, sm = est.forecast(rs, rs, window=20, nw_lag=4)
        assert sp is None and sm is None


class TestForecastDeterministicValue:
    def test_har_prediction_recovers_known_beta_independent_oracle(self):
        # 独立オラクル（写経禁止・range(12,n) 不使用）:
        # 既知 β を与えゼロ残差の合成系列を生成 → OLS は β を厳密に復元するはずなので
        # σ̂ = √(exp(x_next·β)) を別経路（生成式そのもの）で計算し一致を検証する。
        #
        # 生成式: y[t] = β0 + β1·y[t-1] + β4·mean(y[t-4:t]) + β12·mean(y[t-12:t])（t>=12）
        # 最初の 12 点 y[0..11] は線形独立になるよう非定数で配置（特異回避）。
        beta_true = np.array([0.10, 0.50, 0.20, 0.15])  # [const, 1w, 4w, 12w]
        n = 36
        # seed 固定: 先頭 12 点を非自明（特異列回避）に。
        rng = np.random.default_rng(2024)
        y = np.zeros(n)
        y[:12] = rng.normal(-4.0, 0.5, size=12)
        for t in range(12, n):
            mean4 = y[t - 4:t].mean()
            mean12 = y[t - 12:t].mean()
            y[t] = (
                beta_true[0]
                + beta_true[1] * y[t - 1]
                + beta_true[2] * mean4
                + beta_true[3] * mean12
            )
        rs = np.exp(y)  # 正値系列（estimator は log を取り y に戻す）

        est = GkHarEstimator()
        sp, _ = est.forecast(rs.tolist(), rs.tolist(), window=n, nw_lag=4)
        assert sp is not None

        # 独立オラクル: ゼロ残差生成のため β_true が真。次週予測も生成式で直接計算。
        mean4_next = y[-4:].mean()
        mean12_next = y[-12:].mean()
        mu_oracle = (
            beta_true[0]
            + beta_true[1] * y[-1]
            + beta_true[2] * mean4_next
            + beta_true[3] * mean12_next
        )
        expected = math.sqrt(math.exp(mu_oracle))
        assert math.isclose(sp, expected, rel_tol=1e-7)

    def test_positive_sigma_returned(self):
        rng = np.random.default_rng(7)
        rs = np.exp(rng.normal(-4.0, 0.2, size=30)).tolist()
        est = GkHarEstimator()
        sp, sm = est.forecast(rs, rs, window=30, nw_lag=4)
        assert sp is not None and sp > 0
        assert sm is not None and sm > 0
