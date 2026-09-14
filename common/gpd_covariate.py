"""共変量 GPD（スケールを共変量の関数とする一般化パレート分布）— **研究ゲート専用**。

①層名/責務:
    共有プリミティブ層。``log β = X γ``（ξ は定数）の GPD を最尤当てはめし、最後の共変量の
    係数に対する尤度比検定と、その検出力曲線を提供する。

②本番参照は 0 である:
    本モジュールの 5 名はいずれも本番経路から呼ばれていない（分離時点の実測: 本番参照 0・
    テスト 0）。用途は研究ゲート——「この共変量に情報があるか」を判定して先へ進むかを
    決める段——に限られる。本番が常時 import する当てはめ核（common の gpd）へ探索コードを
    同居させないために分離した（ISSUE-479 Wave2 C-2）。実装は無改変で移した。

    ξ を共変量依存にしないのは、ξ の推定が超過数に対して極めて非効率で、層別すると
    識別不能になるため（設計方針として固定）。

③含む構造:
    covariate_gpd_neg_loglik : 負の対数尤度（params = [γ..., ξ]）。
    CovariateGpdFit          : 当てはめ結果。
    covariate_gpd_fit        : 最尤当てはめ。
    lr_test_last_coefficient : H0（最後の列の係数 = 0）の尤度比検定。
    power_curve              : 想定効果量に対する検出力（ゲート 3）。

④依存:
    標準 __future__ / dataclasses / math / 外部 numpy /
    プロジェクト内 common（gpd の当てはめ核・nelder_mead の最適化・var_backtests の χ²）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from common.gpd import XI_EPS, gpd_fit
from common.nelder_mead import nelder_mead


def covariate_gpd_neg_loglik(params: "np.ndarray", y: "np.ndarray", X: "np.ndarray") -> float:
    """log β = X γ、ξ 一定 の GPD の負の対数尤度。``params = [γ..., ξ]``。"""
    gamma = params[:-1]
    xi = float(params[-1])
    log_beta = X @ gamma
    if not np.all(np.isfinite(log_beta)) or np.any(np.abs(log_beta) > 50.0):
        return float("inf")
    beta = np.exp(log_beta)
    if abs(xi) < XI_EPS:
        return float(np.sum(log_beta + y / beta))
    z = 1.0 + xi * y / beta
    if np.any(z <= 0.0):
        return float("inf")
    return float(np.sum(log_beta + (1.0 + 1.0 / xi) * np.log(z)))


@dataclass(frozen=True)
class CovariateGpdFit:
    """共変量 GPD の当てはめ結果。"""

    gamma: "np.ndarray"
    xi: float
    neg_loglik: float
    n: int


def covariate_gpd_fit(y: "np.ndarray", X: "np.ndarray") -> CovariateGpdFit:
    """``log β = X γ``（ξ 一定）を最尤当てはめする。``X`` の 1 列目は切片（1）を想定。"""
    y = np.asarray(y, dtype=np.float64).ravel()
    X = np.asarray(X, dtype=np.float64)
    if X.ndim == 1:
        X = X[:, None]
    base = gpd_fit(y)
    g0 = np.zeros(X.shape[1])
    g0[0] = math.log(base.beta) if np.isfinite(base.beta) and base.beta > 0 else 0.0
    xi0 = base.xi if np.isfinite(base.xi) else 0.1
    start = np.append(g0, xi0)
    best = nelder_mead(lambda p: covariate_gpd_neg_loglik(p, y, X), start, max_iter=4000)
    return CovariateGpdFit(gamma=best[:-1].copy(), xi=float(best[-1]),
                           neg_loglik=covariate_gpd_neg_loglik(best, y, X), n=int(y.size))


def lr_test_last_coefficient(y: "np.ndarray", X: "np.ndarray") -> "tuple[float, float]":
    """H0: ``X`` の**最後の列**の係数 = 0 の尤度比検定。``(統計量, p 値)`` を返す。

    増分検定に使う（例: 切片・log σ̂ を統制したうえで RSI の係数を検定する）。
    自由度 1 の χ² 分布で評価する。
    """
    from common.var_backtests import chi2_sf_df1

    X = np.asarray(X, dtype=np.float64)
    full = covariate_gpd_fit(y, X)
    null = covariate_gpd_fit(y, X[:, :-1])
    stat = 2.0 * (null.neg_loglik - full.neg_loglik)
    if not np.isfinite(stat) or stat < 0.0:
        return float("nan"), float("nan")
    return float(stat), float(chi2_sf_df1(stat))


def power_curve(
    X: "np.ndarray", xi: float, gamma_null: "np.ndarray", effect_sizes, *,
    n_sim: int = 400, alpha: float = 0.05, rng,
) -> "list[tuple[float, float]]":
    """想定効果量ごとの検出力（ゲート 3）。

    ``X`` の**最後の列**の真の係数を ``effect_sizes`` の各値に置いて GPD 超過を生成し、
    尤度比検定が ``alpha`` で棄却する割合を返す。標本数・共変量の分布は**実データのもの**を
    そのまま使うため、θ 補正後の有効クラスタ数と実際の RSI 分布に対する検出力になる。

    Returns:
        ``[(効果量, 検出力), ...]``。
    """
    X = np.asarray(X, dtype=np.float64)
    out = []
    for g1 in effect_sizes:
        gamma = np.append(np.asarray(gamma_null, dtype=np.float64).ravel(), float(g1))
        beta = np.exp(X @ gamma)
        rej = 0
        for _ in range(n_sim):
            y = np.empty(X.shape[0])
            u = rng.random(X.shape[0])
            if abs(xi) < XI_EPS:
                y = -beta * np.log1p(-u)
            else:
                y = beta * ((1.0 - u) ** (-xi) - 1.0) / xi
            _stat, p = lr_test_last_coefficient(y, X)
            rej += int(np.isfinite(p) and p < alpha)
        out.append((float(g1), rej / n_sim))
    return out
