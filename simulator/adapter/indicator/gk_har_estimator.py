"""VarianceEstimatorPort 実装：log-semivariance-HAR（詳細設計 §5.2・D2/D4）。

被説明変数 y_t=log(RS_t)。HAR 説明変数 x_t=[1, y_{t-1}, mean(y[t-4:t]), mean(y[t-12:t])]
（全て当週 t を右辺に含めない＝look-ahead 排除）。翌週予測 σ̂=√(exp(μ̂))（Jensen 補正なし）。
窓<window / RS<=0 / NaN / 特異 → None（D4・ノートレード）。

numpy は本 adapter に局所化。
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np


class GkHarEstimator:
    def forecast(
        self,
        rs_plus_series: "Sequence[float]",
        rs_minus_series: "Sequence[float]",
        *,
        window: int = 260,
        nw_lag: int = 4,
    ) -> "tuple[float | None, float | None]":
        sp = self._har_one(rs_plus_series, window, nw_lag)
        sm = self._har_one(rs_minus_series, window, nw_lag)
        if sp is None or sm is None:
            return (None, None)
        return (sp, sm)

    def _har_one(self, rs_series, window, nw_lag) -> "float | None":
        rs = np.asarray(list(rs_series), dtype=float)
        if rs.size < window:
            return None
        rs = rs[-window:]
        if np.any(rs <= 0) or not np.all(np.isfinite(rs)):
            return None
        y = np.log(rs)
        X, yv = self._build_har_design(y)
        if X.shape[0] < X.shape[1] + 1:
            return None
        beta = self._ols(X, yv)
        if beta is None:
            return None
        x_next = np.array([1.0, y[-1], y[-4:].mean(), y[-12:].mean()])
        mu_hat = float(x_next @ beta)
        if not math.isfinite(mu_hat):
            return None
        sigma = math.sqrt(math.exp(mu_hat))
        return sigma if math.isfinite(sigma) and sigma > 0 else None

    @staticmethod
    def _build_har_design(y: "np.ndarray") -> "tuple[np.ndarray, np.ndarray]":
        rows = []
        targets = []
        n = y.size
        for t in range(12, n):  # 12 週平均が確定する t から
            x = [1.0, y[t - 1], y[t - 4:t].mean(), y[t - 12:t].mean()]
            rows.append(x)
            targets.append(y[t])
        return np.asarray(rows, dtype=float), np.asarray(targets, dtype=float)

    @staticmethod
    def _ols(X: "np.ndarray", y: "np.ndarray") -> "np.ndarray | None":
        XtX = X.T @ X
        try:
            return np.linalg.solve(XtX, X.T @ y)
        except np.linalg.LinAlgError:
            return None
