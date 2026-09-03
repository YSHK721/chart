"""一般化パレート分布（GPD）の当てはめ・適合度検定・自動閾値選択。

①層名/責務:
    共有プリミティブ層（純粋ロジック・外部 I/O 非依存・numpy のみ）。
    POT（peaks over threshold）で用いる GPD の最尤推定、Anderson–Darling 適合度検定、
    および ForwardStop による**閾値選択の自動化**を提供する。

②なぜ自動化するか:
    目視の mean residual life plot は再現不能で、閾値を動かせば結論も動く。閾値選択を
    人手の判断に委ねると「都合の良い閾値を選んだ」批判に答えられない。
    Bader, Yan & Zhang (2018, AOAS 12(1), 310–329) は、閾値を昇順に並べて GPD 適合度検定を
    繰り返し、G'Sell et al. (2016, JRSS-B 78(2), 423–444) の **ForwardStop** 規則で系列的に
    止める方式を与える。判断は α（誤り率）1 つに集約され、手続きは決定論的になる。

③前提（重要）:
    GPD 当てはめも適合度検定も**超過が独立**であることを前提にする。金融時系列の超過は
    強くクラスタ化するため（極値指標 θ ≪ 1）、**先に宣言クラスタリングで独立化**してから
    本モジュールへ渡すこと（:func:`common.extremal_index.intervals_decluster`）。
    生の超過をそのまま渡すと、適合度検定は有効標本を過大に見積もって棄却しやすくなる。

④含む構造:
    gpd_neg_loglik   : 負の対数尤度（ξ→0 の極限も連続に扱う）。
    gpd_fit          : 最尤推定（Nelder–Mead・scipy 非依存）。
    gpd_cdf          : 分布関数。
    anderson_darling : GPD 当てはめ後の A² 統計量（Choulakian & Stephens 2001 の形）。
    gpd_gof_pvalue   : パラメトリックブートストラップによる適合度 p 値。
    forward_stop     : ForwardStop 規則（p 値列 → 停止位置）。
    select_threshold : 候補閾値列 → 採択閾値（②の手続き全体）。
    covariate_gpd_fit: スケールを共変量の関数とする GPD（log β = Xγ・ξ 一定）。
    lr_test_gamma    : H0: 最後の共変量の係数 = 0 の尤度比検定。
    power_curve      : 想定効果量に対する検出力（ゲート 3）。
    gpd_excess_quantile: 超過分へ当てはめた GPD の q 分位（外れ値水準の単一定義）。

⑤依存: 標準 __future__ / dataclasses / math / 外部 numpy。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

#: ξ がこの絶対値未満なら指数分布の極限式を使う（数値的な 0 除算回避）。
_XI_EPS: float = 1e-8

#: GPD を当てはめる最小観測数。未満は水準を出さない（NaN）。
#: 根拠（tickvol 実測 2026-08-01・窓をずらした 10 標本の変動係数）: m=5 で 0.95、
#: m=10〜20 で 0.71〜0.73、m=30 で 0.245、m>=50 で 0.14〜0.21。30 未満は推定量が
#: 自身の値と同じ大きさで揺れる。
MIN_GPD_EVENTS: int = 30


def gpd_neg_loglik(params: "np.ndarray", excess: "np.ndarray") -> float:
    """GPD の負の対数尤度。``params = [log β, ξ]``。実行不能域は ``inf``。"""
    log_beta, xi = float(params[0]), float(params[1])
    beta = math.exp(log_beta)
    y = excess
    if beta <= 0.0 or y.size == 0:
        return float("inf")
    if abs(xi) < _XI_EPS:
        return float(y.size * log_beta + float(y.sum()) / beta)
    z = 1.0 + xi * y / beta
    if np.any(z <= 0.0):
        return float("inf")           # 台の外＝尤度 0
    return float(y.size * log_beta + (1.0 + 1.0 / xi) * float(np.log(z).sum()))


def _nelder_mead(f, x0: "np.ndarray", *, max_iter: int = 2000, tol: float = 1e-10) -> "np.ndarray":
    """Nelder–Mead 法（scipy 非依存）。``cvfe.benchmarks`` と同型の最小実装。"""
    n = x0.size
    step = np.where(np.abs(x0) > 1e-8, 0.05 * np.abs(x0), 0.05)
    simplex = np.vstack([x0] + [x0 + np.eye(n)[i] * step[i] for i in range(n)])
    fx = np.array([f(p) for p in simplex])
    for _ in range(max_iter):
        order = np.argsort(fx)
        simplex, fx = simplex[order], fx[order]
        if abs(fx[-1] - fx[0]) <= tol * (abs(fx[0]) + tol):
            break
        centroid = simplex[:-1].mean(axis=0)
        xr = centroid + (centroid - simplex[-1])
        fr = f(xr)
        if fr < fx[0]:
            xe = centroid + 2.0 * (centroid - simplex[-1])
            fe = f(xe)
            simplex[-1], fx[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < fx[-2]:
            simplex[-1], fx[-1] = xr, fr
        else:
            xc = centroid + 0.5 * (simplex[-1] - centroid)
            fc = f(xc)
            if fc < fx[-1]:
                simplex[-1], fx[-1] = xc, fc
            else:
                simplex[1:] = simplex[0] + 0.5 * (simplex[1:] - simplex[0])
                fx[1:] = np.array([f(p) for p in simplex[1:]])
    return simplex[int(np.argmin(fx))]


@dataclass(frozen=True)
class GpdFit:
    """GPD 当てはめ結果。"""

    xi: float
    beta: float
    n: int
    neg_loglik: float


def gpd_fit(excess: "np.ndarray") -> GpdFit:
    """超過分 ``excess > 0`` へ GPD を最尤当てはめする。

    初期値はモーメント法（``ξ0``, ``β0``）で与える。台の外へ出る初期値を避けるため
    ``ξ0`` は負側に寄せない。
    """
    y = np.asarray(excess, dtype=np.float64).ravel()
    y = y[np.isfinite(y) & (y > 0.0)]
    if y.size < 5:
        return GpdFit(xi=float("nan"), beta=float("nan"), n=int(y.size),
                      neg_loglik=float("nan"))
    m, v = float(y.mean()), float(y.var(ddof=1))
    xi0 = 0.5 * (1.0 - m * m / v) if v > 0 else 0.1
    xi0 = float(np.clip(xi0, -0.2, 0.4))
    beta0 = max(m * (1.0 - xi0), 1e-12)
    best = _nelder_mead(lambda p: gpd_neg_loglik(p, y), np.array([math.log(beta0), xi0]))
    nll = gpd_neg_loglik(best, y)
    return GpdFit(xi=float(best[1]), beta=float(math.exp(best[0])), n=int(y.size),
                  neg_loglik=float(nll))


def gpd_cdf(y: "np.ndarray", xi: float, beta: float) -> "np.ndarray":
    """GPD の分布関数（``ξ→0`` は指数分布）。"""
    y = np.asarray(y, dtype=np.float64)
    if beta <= 0.0:
        return np.full(y.shape, np.nan)
    if abs(xi) < _XI_EPS:
        return 1.0 - np.exp(-y / beta)
    z = 1.0 + xi * y / beta
    z = np.where(z > 0.0, z, np.nan)
    return 1.0 - z ** (-1.0 / xi)


def gpd_rvs(n: int, xi: float, beta: float, *, rng) -> "np.ndarray":
    """GPD からの乱数（逆関数法）。"""
    u = rng.random(n)
    if abs(xi) < _XI_EPS:
        return -beta * np.log1p(-u)
    return beta * ((1.0 - u) ** (-xi) - 1.0) / xi


def gpd_excess_quantile(excesses, q: "float | None") -> float:
    """超過分へ GPD を当てはめ、その **q 分位**（超過分のスケール）を返す。

    ``level = β/ξ · ((1−q)^(−ξ) − 1)``（ξ→0 は指数分布の極限 ``−β·ln(1−q)``）。
    観測が :data:`MIN_GPD_EVENTS` 未満、q 無効、当てはめ失敗はいずれも NaN。

    当てはめ自体は :func:`gpd_fit`（最尤・scipy 非依存）へ委譲する。外れ値水準を出す
    指標（tickvol / profit_rsi）はいずれも本関数を参照し、式を写さない（単一定義）。
    """
    if q is None:
        return float("nan")
    y = np.asarray(excesses, dtype=np.float64).ravel()
    y = y[np.isfinite(y) & (y > 0.0)]
    if y.size < MIN_GPD_EVENTS:
        return float("nan")
    fit = gpd_fit(y)
    if not np.isfinite(fit.xi) or not np.isfinite(fit.beta) or fit.beta <= 0.0:
        return float("nan")
    tail = 1.0 - float(q)
    if tail <= 0.0:
        return float("nan")
    if abs(fit.xi) < _XI_EPS:
        return float(-fit.beta * np.log(tail))
    return float(fit.beta / fit.xi * (tail ** (-fit.xi) - 1.0))


def anderson_darling(excess: "np.ndarray", xi: float, beta: float) -> float:
    """当てはめた GPD に対する Anderson–Darling 統計量 A²。

    Choulakian & Stephens (2001, *Technometrics* 43(4), 478–484) と同じ形。裾に重みを置く
    ため、POT の適合度としては Kolmogorov–Smirnov より検出力が高い。
    """
    y = np.sort(np.asarray(excess, dtype=np.float64).ravel())
    n = y.size
    if n < 5 or not np.isfinite(xi) or not np.isfinite(beta) or beta <= 0:
        return float("nan")
    z = gpd_cdf(y, xi, beta)
    eps = 1e-12
    z = np.clip(z, eps, 1.0 - eps)
    i = np.arange(1, n + 1)
    s = float(np.sum((2.0 * i - 1.0) * (np.log(z) + np.log1p(-z[::-1]))))
    return float(-n - s / n)


def gpd_gof_pvalue(excess: "np.ndarray", *, n_boot: int = 199, rng) -> float:
    """GPD 適合度の p 値（パラメトリックブートストラップ）。

    観測から ``(ξ̂, β̂)`` を推定して A²_obs を得たのち、同じパラメータの GPD から同数の標本を
    生成して**再推定した上で** A² を計算し、その分布に対する順位から p 値を出す。
    Choulakian–Stephens の表を引かずに済み、`ξ` に依存する臨界値の内挿誤差も避けられる。
    """
    y = np.asarray(excess, dtype=np.float64).ravel()
    y = y[np.isfinite(y) & (y > 0.0)]
    fit = gpd_fit(y)
    if not np.isfinite(fit.xi) or not np.isfinite(fit.beta):
        return float("nan")
    a_obs = anderson_darling(y, fit.xi, fit.beta)
    if not np.isfinite(a_obs):
        return float("nan")
    count = 0
    used = 0
    for _ in range(n_boot):
        sim = gpd_rvs(y.size, fit.xi, fit.beta, rng=rng)
        f2 = gpd_fit(sim)
        if not np.isfinite(f2.xi):
            continue
        a_sim = anderson_darling(sim, f2.xi, f2.beta)
        if not np.isfinite(a_sim):
            continue
        used += 1
        count += int(a_sim >= a_obs)
    if used < 20:
        return float("nan")
    return float((count + 1.0) / (used + 1.0))


def forward_stop(pvalues: "np.ndarray", alpha: float = 0.05) -> int:
    """ForwardStop 規則（G'Sell et al. 2016）。棄却する仮説数 ``k̂`` を返す。

    ``k̂ = max{ k : (1/k) Σ_{i<=k} −log(1 − p_i) <= α }``。該当が無ければ 0。
    p 値は**閾値の昇順**に並んでいること（低い閾値ほど GPD が当てはまりにくい）。
    """
    p = np.asarray(pvalues, dtype=np.float64).ravel()
    if p.size == 0:
        return 0
    ok = np.isfinite(p)
    q = np.where(ok, np.clip(p, 0.0, 1.0 - 1e-12), 1.0 - 1e-12)
    terms = -np.log1p(-q)
    means = np.cumsum(terms) / np.arange(1, p.size + 1)
    hits = np.flatnonzero(means <= alpha)
    return int(hits[-1] + 1) if hits.size else 0


@dataclass(frozen=True)
class ThresholdSelection:
    """自動閾値選択の結果。"""

    threshold: "float | None"
    index: "int | None"
    pvalues: "np.ndarray"
    n_rejected: int
    alpha: float


def select_threshold(
    excesses_by_threshold: "list[tuple[float, np.ndarray]]",
    *,
    alpha: float = 0.05,
    n_boot: int = 199,
    rng,
) -> ThresholdSelection:
    """候補閾値を昇順に検定し、ForwardStop が止めた最初の閾値を返す。

    Args:
        excesses_by_threshold: ``[(u, 超過分), ...]`` を **u 昇順**で。超過分は ``x − u > 0``。
        alpha: ForwardStop の誤り率。
        n_boot: 適合度 p 値のブートストラップ本数。

    Returns:
        採択閾値（該当が無ければ ``threshold=None``）。
    """
    ps = []
    for _u, ex in excesses_by_threshold:
        ps.append(gpd_gof_pvalue(ex, n_boot=n_boot, rng=rng))
    p = np.asarray(ps, dtype=np.float64)
    k = forward_stop(p, alpha)
    if k >= p.size:
        return ThresholdSelection(threshold=None, index=None, pvalues=p,
                                  n_rejected=k, alpha=alpha)
    u_sel, _ = excesses_by_threshold[k]
    return ThresholdSelection(threshold=float(u_sel), index=int(k), pvalues=p,
                              n_rejected=k, alpha=alpha)


# ---------------------------------------------------------------------------
# 共変量 POT（Davison & Smith 1990 / Chavez-Demoulin & Davison 2005）
# ---------------------------------------------------------------------------
#
# スケールのみを共変量に依存させる: log β(x_i) = x_i·γ、ξ は定数。
# ξ を共変量依存にしないのは、ξ の推定が超過数に対して極めて非効率で、層別すると
# 識別不能になるため（設計方針として固定）。


def covariate_gpd_neg_loglik(params: "np.ndarray", y: "np.ndarray", X: "np.ndarray") -> float:
    """log β = X γ、ξ 一定 の GPD の負の対数尤度。``params = [γ..., ξ]``。"""
    gamma = params[:-1]
    xi = float(params[-1])
    log_beta = X @ gamma
    if not np.all(np.isfinite(log_beta)) or np.any(np.abs(log_beta) > 50.0):
        return float("inf")
    beta = np.exp(log_beta)
    if abs(xi) < _XI_EPS:
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
    best = _nelder_mead(lambda p: covariate_gpd_neg_loglik(p, y, X), start, max_iter=4000)
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
            if abs(xi) < _XI_EPS:
                y = -beta * np.log1p(-u)
            else:
                y = beta * ((1.0 - u) ** (-xi) - 1.0) / xi
            _stat, p = lr_test_last_coefficient(y, X)
            rej += int(np.isfinite(p) and p < alpha)
        out.append((float(g1), rej / n_sim))
    return out
