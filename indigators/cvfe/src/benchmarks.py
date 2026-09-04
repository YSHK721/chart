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

M2 の最尤推定は ``scipy`` を使わず Nelder–Mead 法で行う（仕様 §6「依存: numpy のみ」）。
単体法そのものは共有プリミティブ :func:`common.nelder_mead` が唯一の実装であり、本モジュールは
GARCH 当てはめ固有の方針（初期単体の刻み・タイブレーク）だけを与えて委譲する
（ISSUE-479 Wave2 追随 C。複製は片方だけ直された日に当てはめ結果を静かに食い違わせる）。

依存: 外部 numpy / 共有 common.nelder_mead / プロジェクト内 dto, har。
"""

from __future__ import annotations

import numpy as np

from common.nelder_mead import nelder_mead

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


#: 初期単体の刻み（GARCH パラメータのスケールに合わせた本モジュールのドメイン知識）。
#:   ω は分散のスケール（1e-6 オーダー）で始まるため、0 のときの既定刻みは十分小さく取る。
_GARCH_ZERO_STEP = 0.00025
_GARCH_STEP_RATIO = 0.05


def _nelder_mead(f, x0: np.ndarray, *, max_iter: int = 2000, tol: float = 1e-10) -> np.ndarray:
    """Nelder–Mead 単体法（Nelder & Mead 1965）— 実装は共有 :func:`common.nelder_mead` 1 本。

    単体法そのもの（反射・拡大・収縮・縮小の 30 行）を本モジュールに持たない。片方だけ直された
    日に当てはめ結果が指標間で静かに食い違うためである（出力は「それらしい値」のままなので
    状態検証では落ちない）。本関数が与えるのは **GARCH 当てはめ固有の方針**だけ:

      - 初期単体の刻み: 0 の軸は ``_GARCH_ZERO_STEP``、それ以外は ``|x0|`` の 5%。
        汎用既定（0 の軸に 0.05）はここでは粗すぎる（ω は 1e-6 オーダー）。
      - タイブレーク: ``kind="stable"``（同値時の並びを固定＝決定論性）。

    これらは委譲前の私有実装と同一であり、数値は bit 一致する
    （``cvfe/tests/test_benchmarks.py`` が凍結 digest で固定する）。
    """
    x0 = np.asarray(x0, dtype=np.float64)
    step = np.where(x0 != 0.0, _GARCH_STEP_RATIO * np.abs(x0), _GARCH_ZERO_STEP)
    return nelder_mead(f, x0, max_iter=max_iter, tol=tol, initial_step=step, sort_kind="stable")
