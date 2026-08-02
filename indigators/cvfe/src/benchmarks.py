"""比較対象モデル M0〜M3（仕様 §5.3・固定・変更禁止）。

層名/責務:
    純粋ロジック層。「予測器」を提供する。評価手続き（:mod:`.evaluation`）とは
    別のアクターに応答するため分離する（M3 は HAR の一種であり、実装は :mod:`.har` を
    再利用する。評価側に HAR を複製しない）。

    すべての予測器はバー ``t`` の予測を ``t−1`` 以前の情報のみから作る（仕様 §4 柱書と同じ
    因果規約）。予測開始バー ``t0`` より前は ``nan`` を返す。

| ID | モデル | 本モジュールの関数 |
|----|--------|-------------------|
| M0 | 単純移動平均（直近 20 本の ``V_t`` の平均） | :func:`forecast_moving_average` |
| M1 | EWMA（``λ = 0.94``） | :func:`forecast_ewma` |
| M2 | GARCH(1,1)（日次終値収益） | :func:`forecast_garch11` |
| M3 | HAR（ジャンプ・レバレッジ項なし） | :func:`forecast_har_plain` |
| M4 | HAR-CJ-L（本仕様） | :mod:`.engine` の ``compute_cvfe`` |

M2 の最尤推定は ``scipy`` を使わず、Nelder–Mead 法（:func:`_nelder_mead`）を
本モジュールに実装する（仕様 §6「依存: numpy のみ」）。

依存: 外部 numpy / プロジェクト内 dto, har。
"""

from __future__ import annotations

import numpy as np

from .dto import HAR_LAG_MONTH, HAR_LAG_WEEK
from .har import C_FLOOR, ols_with_intercept, sigma_oc_from_log_variance

#: 仕様 §5.3 M0：単純移動平均の本数。
MA_WINDOW: int = 20

#: 仕様 §5.3 M1：EWMA の減衰係数。
EWMA_LAMBDA: float = 0.94


def forecast_moving_average(v: np.ndarray, t0: int, window: int = MA_WINDOW) -> np.ndarray:
    """M0：``σ̂_t = sqrt( mean(V_{t−window} .. V_{t−1}) )``。"""
    v = np.asarray(v, dtype=np.float64)
    out = np.full(v.size, np.nan, dtype=np.float64)
    for t in range(max(t0, window), v.size):
        w = v[t - window:t]
        if np.all(np.isfinite(w)):
            m = float(w.mean())
            if m > 0.0:
                out[t] = np.sqrt(m)
    return out


def forecast_ewma(v: np.ndarray, t0: int, lam: float = EWMA_LAMBDA,
                  window: int = MA_WINDOW) -> np.ndarray:
    """M1：``h_t = λ h_{t−1} + (1 − λ) V_{t−1}``、``σ̂_t = sqrt(h_t)``。

    初期値は先頭 ``window`` 本の ``V`` の平均（有限値のみ）とする。
    """
    v = np.asarray(v, dtype=np.float64)
    out = np.full(v.size, np.nan, dtype=np.float64)
    seed = v[:window][np.isfinite(v[:window])]
    if seed.size == 0:
        return out
    h = float(seed.mean())
    for t in range(1, v.size):
        prev = v[t - 1]
        if np.isfinite(prev):
            h = lam * h + (1.0 - lam) * float(prev)
        if t >= t0 and h > 0.0:
            out[t] = np.sqrt(h)
    return out


def forecast_har_plain(c: np.ndarray, p_close: np.ndarray, t0: int, n_har: int) -> np.ndarray:
    """M3：ジャンプ項・レバレッジ項を除いた HAR（``x1, x2, x3`` のみ）。

    学習標本・予測規約は仕様 §4.5-3・§4.6 と同一。``σ̂ = exp(ŷ/2 + s²/8)``。
    """
    c = np.asarray(c, dtype=np.float64)
    pc = np.asarray(p_close, dtype=np.float64)
    n = c.size
    out = np.full(n, np.nan, dtype=np.float64)

    rows = _har3_rows(c)
    y = np.full(n, np.nan, dtype=np.float64)
    finite = np.isfinite(c)
    y[finite] = np.log(np.maximum(c[finite], C_FLOOR))

    lo, hi = t0 - n_har - 1, t0 - 2
    if lo < HAR_LAG_MONTH - 1 or hi + 1 >= n:
        return out
    x_train = rows[lo:hi + 1]
    y_train = y[lo + 1:hi + 2]
    coef, ssr = ols_with_intercept(x_train, y_train)
    s2 = float(ssr / (x_train.shape[0] - coef.size))

    for t in range(t0, n):
        x_prev = rows[t - 1]
        if not np.all(np.isfinite(x_prev)):
            continue
        y_hat = float(coef[0] + float(np.dot(coef[1:], x_prev)))
        out[t] = sigma_oc_from_log_variance(y_hat, s2)
    return out


def _har3_rows(c: np.ndarray) -> np.ndarray:
    """``[x1, x2, x3] = [ln C_t, ln mean_5 C, ln mean_22 C]``（仕様 §4.5-1 の前 3 項）。"""
    n = c.size
    rows = np.full((n, 3), np.nan, dtype=np.float64)
    cc = np.maximum(np.asarray(c, dtype=np.float64), C_FLOOR)
    for t in range(HAR_LAG_MONTH - 1, n):
        w = cc[t - HAR_LAG_MONTH + 1:t + 1]
        if not np.all(np.isfinite(w)):
            continue
        rows[t, 0] = np.log(w[-1])
        rows[t, 1] = np.log(w[-HAR_LAG_WEEK:].mean())
        rows[t, 2] = np.log(w.mean())
    return rows


# --------------------------------------------------------------------------------------
# M2: GARCH(1,1)
# --------------------------------------------------------------------------------------

def forecast_garch11(returns: np.ndarray, t0: int, n_har: int) -> np.ndarray:
    """M2：日次終値収益に対する GARCH(1,1)。

    ``h_t = ω + α r²_{t−1} + β h_{t−1}``。パラメータは仕様 §4.5-3 と同じ学習窓
    （``t ∈ [t0 − n_har − 1, t0 − 2]``）で最尤推定し、以降は凍結して前進フィルタする
    （HAR 側の ``refit_every = 0`` と対称）。
    """
    r = np.asarray(returns, dtype=np.float64)
    n = r.size
    out = np.full(n, np.nan, dtype=np.float64)
    lo, hi = max(t0 - n_har - 1, 1), t0 - 2
    train = r[lo:hi + 1]
    train = train[np.isfinite(train)]
    if train.size < 50:
        return out

    omega, alpha, beta = _fit_garch11(train)

    var_uncond = float(np.var(train))
    h = var_uncond if var_uncond > 0.0 else 1e-12
    for t in range(1, n):
        prev = r[t - 1]
        if np.isfinite(prev):
            h = omega + alpha * float(prev) ** 2 + beta * h
        if t >= t0 and h > 0.0:
            out[t] = np.sqrt(h)
    return out


def _garch11_neg_loglik(theta: np.ndarray, r: np.ndarray) -> float:
    """GARCH(1,1) の負の対数尤度（正規）。制約違反は大きな値で罰する。"""
    omega, alpha, beta = float(theta[0]), float(theta[1]), float(theta[2])
    if omega <= 0.0 or alpha < 0.0 or beta < 0.0 or alpha + beta >= 0.999:
        return 1e12
    var0 = float(np.var(r))
    if not np.isfinite(var0) or var0 <= 0.0:
        return 1e12
    h = var0
    total = 0.0
    for x in r:
        if h <= 0.0 or not np.isfinite(h):
            return 1e12
        total += np.log(h) + x * x / h
        h = omega + alpha * x * x + beta * h
    return 0.5 * float(total)


def _fit_garch11(r: np.ndarray) -> tuple[float, float, float]:
    """Nelder–Mead で ``(ω, α, β)`` を最尤推定する（scipy 非依存・決定論的）。"""
    var0 = float(np.var(r))
    start = np.array([var0 * 0.05, 0.08, 0.88], dtype=np.float64)
    best = _nelder_mead(lambda th: _garch11_neg_loglik(th, r), start)
    omega, alpha, beta = (max(float(best[0]), 1e-18),
                          min(max(float(best[1]), 0.0), 0.999),
                          min(max(float(best[2]), 0.0), 0.999))
    if alpha + beta >= 0.999:                       # 定常性へ射影する
        scale = 0.998 / (alpha + beta)
        alpha, beta = alpha * scale, beta * scale
    return omega, alpha, beta


def _nelder_mead(f, x0: np.ndarray, *, max_iter: int = 2000, tol: float = 1e-10) -> np.ndarray:
    """Nelder–Mead 単体法（Nelder & Mead 1965）。乱数を使わず決定論的に動く。"""
    n = x0.size
    simplex = np.tile(x0.astype(np.float64), (n + 1, 1))
    for i in range(n):
        step = 0.05 * abs(x0[i]) if x0[i] != 0.0 else 0.00025
        simplex[i + 1, i] += step
    fx = np.array([f(p) for p in simplex], dtype=np.float64)

    for _ in range(max_iter):
        order = np.argsort(fx, kind="stable")
        simplex, fx = simplex[order], fx[order]
        if abs(fx[-1] - fx[0]) <= tol * (abs(fx[0]) + tol):
            break
        centroid = simplex[:-1].mean(axis=0)

        xr = centroid + 1.0 * (centroid - simplex[-1])          # 反射
        fr = f(xr)
        if fr < fx[0]:
            xe = centroid + 2.0 * (centroid - simplex[-1])      # 拡張
            fe = f(xe)
            simplex[-1], fx[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < fx[-2]:
            simplex[-1], fx[-1] = xr, fr
        else:
            xc = centroid + 0.5 * (simplex[-1] - centroid)      # 収縮
            fc = f(xc)
            if fc < fx[-1]:
                simplex[-1], fx[-1] = xc, fc
            else:                                               # 縮小
                simplex[1:] = simplex[0] + 0.5 * (simplex[1:] - simplex[0])
                fx[1:] = np.array([f(p) for p in simplex[1:]], dtype=np.float64)
    return simplex[int(np.argmin(fx))]
