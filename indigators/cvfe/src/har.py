"""HAR-CJ-L 回帰と場中成分の予測（仕様 §4.5・§4.6）。

層名/責務:
    純粋ロジック層。説明変数の構成・OLS 推定・1 期先予測を担う。

多変量 OLS を本モジュールに置く理由:
    共有プリミティブ ``common/ols_fit.py`` は設計行列を ``Φ = [1, x]`` の 2 列に固定して
    おり（``ols_fit`` の実装）、HAR の 6 係数へは適用できない。同モジュールは
    「bit 一致が保証境界」と明記しており、既存 2 呼び出し元の出力を変えうる一般化は
    行わない（無改変参照の原則）。よって 6 列版をここに実装する。

因果性（仕様 §4 柱書）:
    :func:`har_predict` は ``x_{t−1}`` のみを受け取る。``t`` 時点の特徴量を渡せる
    シグネチャを持たせない。

依存: 外部 numpy / プロジェクト内 dto, errors。
"""

from __future__ import annotations

import numpy as np

from .dto import HAR_LAG_MONTH, HAR_LAG_WEEK, HAR_N_COEF
from .errors import E08_HAR_SINGULAR, W04_HAR_JUMP_COLUMN_CONSTANT, CvfeError
from .logs import Logger, resolve

#: 仕様 §4.5-1：``C_t < 1e-16`` は ``1e-16`` にクリップする。
C_FLOOR: float = 1e-16

#: 仕様 §3.3 E08：条件数の上限。
COND_LIMIT: float = 1e10


def har_feature_row(c_window: np.ndarray, j_last: float, rho_last: float) -> np.ndarray:
    """1 バー分の説明変数 ``[x1..x5]``（仕様 §4.5-1）。

    Parameters
    ----------
    c_window
        当該バー ``t`` を末尾とする直近 ``HAR_LAG_MONTH``（22）本の ``C``。
        本数が 22 未満のときは遡及不足として全要素 ``nan`` を返す。
    j_last
        ``J_t``。
    rho_last
        ``ρ_t = p_close,t − p_close,t−1``。

    一括経路（:func:`har_features`）と逐次経路（``CvfeSequential``）は
    **この関数のみ**で特徴量を作る。両経路で浮動小数の演算が分岐しない。
    """
    out = np.full(5, np.nan, dtype=np.float64)
    w = np.asarray(c_window, dtype=np.float64)
    if w.size != HAR_LAG_MONTH or not np.all(np.isfinite(w)) or not np.isfinite(rho_last):
        return out
    c = np.maximum(w, C_FLOOR)
    out[0] = np.log(c[-1])
    out[1] = np.log(c[-HAR_LAG_WEEK:].mean())
    out[2] = np.log(c.mean())
    out[3] = np.log(1.0 + float(j_last) / c[-1]) if np.isfinite(j_last) else np.nan
    out[4] = min(float(rho_last), 0.0)
    return out


def har_features(c: np.ndarray, j: np.ndarray, p_close: np.ndarray) -> np.ndarray:
    """仕様 §4.5-1 の説明変数 ``[x1..x5]`` を全バーについて構成する（shape ``(N, 5)``）。

    遡及が不足するバー（``t < 21``）と ``ρ`` が定義できないバー（``t = 0``）は ``nan``。
    ``C`` に ``nan`` を含むバー（E06 の無効バー等）を参照する行もすべて ``nan``。
    """
    c = np.asarray(c, dtype=np.float64)
    j = np.asarray(j, dtype=np.float64)
    pc = np.asarray(p_close, dtype=np.float64)
    n = c.size
    out = np.full((n, 5), np.nan, dtype=np.float64)
    for t in range(HAR_LAG_MONTH - 1, n):
        rho = pc[t] - pc[t - 1] if t >= 1 else np.nan
        out[t] = har_feature_row(c[t - HAR_LAG_MONTH + 1:t + 1], j[t], rho)
    return out



def ols_with_intercept(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float]:
    """切片付き通常最小二乗。``(coef[0..k], SSR)`` を返す（``coef[0]`` が切片）。

    設計行列のランクが不足、または条件数が :data:`COND_LIMIT` を超える場合は
    ``E08_HAR_SINGULAR`` を送出する（仕様 §3.3 E08・§4.5-4）。
    残差分散の自由度は呼び出し側が定める（仕様 §4.5-5 は M4 について ``T − 6`` と規定）。
    """
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    t_obs, k = x.shape
    n_coef = k + 1
    if t_obs != y.size:
        raise CvfeError(E08_HAR_SINGULAR, "説明変数と目的変数の本数が一致しない")
    if t_obs <= n_coef:
        raise CvfeError(E08_HAR_SINGULAR, f"自由度が不足する: T={t_obs}")
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise CvfeError(E08_HAR_SINGULAR, "学習標本に非有限値が含まれる")

    design = np.empty((t_obs, n_coef), dtype=np.float64)
    design[:, 0] = 1.0
    design[:, 1:] = x
    if np.linalg.matrix_rank(design) < n_coef:
        raise CvfeError(E08_HAR_SINGULAR, f"設計行列のランクが {n_coef} 未満")
    cond = float(np.linalg.cond(design))
    if not np.isfinite(cond) or cond > COND_LIMIT:
        raise CvfeError(E08_HAR_SINGULAR, f"設計行列の条件数が上限超過: {cond!r}")

    coef, _res, _rank, _sv = np.linalg.lstsq(design, y, rcond=None)
    resid = y - design @ coef
    return np.ascontiguousarray(coef, dtype=np.float64), float((resid ** 2).sum())


#: ``x4`` の列位置（``[x1..x5]`` の 0 起点）。仕様 §4.5-1 の ``ln(1 + J_t/C_t)``。
JUMP_COLUMN: int = 3


def har_fit(x: np.ndarray, y: np.ndarray, *,
            logger: Logger | None = None) -> tuple[np.ndarray, float]:
    """仕様 §4.5-4・5：通常最小二乗で ``β = [β0..β5]`` と残差分散 ``s²`` を返す。

    設計行列のランクが不足、または条件数が ``1e10`` を超える場合は
    ``E08_HAR_SINGULAR`` を送出する。``s²`` は自由度 ``T − 6`` の不偏推定量（§4.5-5）。

    ``x4`` が識別不能な場合の扱い（仕様の欠落への対処・ISSUE-199）:
        説明変数 ``x4 = ln(1 + J_t/C_t)`` が学習標本内で**厳密に定数**になる状況が
        2 通り存在する。

        1. 仕様 §4.4 が ``measure_id ∈ {"RRANGE", "PARK"}`` で ``J_t = 0`` と定めるため
           恒等的に 0 になる（構造的）。一方 §3.3 E07 は ``quality_gate = "FAIL"``
           （→ ``PARK``）で「例外を送出せず縮退」を明示的に保証しており、両者は矛盾する。
        2. 学習窓にジャンプが 1 本も検出されなかった（標本的）。``jump_alpha = 0.999``
           の下では発生率 0.1% 程度であり、``n_har = 1500`` でも約 22% の確率で起こる。

        いずれの場合も定数列の係数 ``β4`` は識別不能であり、この列を含めた設計行列の
        ランクは必ず 5 に落ちて §3.3 E08 が送出される。仕様 §4.5 は ``x4`` が変動する
        ことを暗黙の前提としており、この前提が崩れる場合の規定を欠く。

        本実装は当該列を推定から外し ``β4 = 0`` に固定して残り 5 係数を推定し、
        ``W04_HAR_JUMP_COLUMN_CONSTANT`` を WARN 出力する（``har_coef`` の形状 ``(6,)``
        は §3.2 のまま不変）。除外の判定は「厳密な定数か」という決定論的条件であり、
        条件数の悪化や他列の共線性は従来どおり E08 として送出する。
    """
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if x.ndim != 2 or x.shape[1] != HAR_N_COEF - 1:
        raise CvfeError(E08_HAR_SINGULAR, f"説明変数は shape (T,5) が必要: {x.shape}")
    t_obs = x.shape[0]

    cols = list(range(HAR_N_COEF - 1))
    if t_obs > 0 and bool(np.all(x[:, JUMP_COLUMN] == x[0, JUMP_COLUMN])):
        resolve(logger).emit(
            "WARN", W04_HAR_JUMP_COLUMN_CONSTANT, -1,
            f"x4 が学習標本内で定数（値 {float(x[0, JUMP_COLUMN])!r}）のため β4 = 0 に固定")
        cols.remove(JUMP_COLUMN)

    fitted, ssr = ols_with_intercept(x[:, cols], y)
    if t_obs <= HAR_N_COEF:
        raise CvfeError(E08_HAR_SINGULAR, f"自由度が不足する: T={t_obs}")
    # 仕様 §4.5-5：自由度は n_har − 6（推定した係数数ではなく仕様の規定値を用いる）。
    s2 = float(ssr / (t_obs - HAR_N_COEF))

    beta = np.zeros(HAR_N_COEF, dtype=np.float64)
    beta[0] = fitted[0]
    for k, col in enumerate(cols):
        beta[col + 1] = fitted[k + 1]
    return np.ascontiguousarray(beta, dtype=np.float64), s2


def har_predict(beta: np.ndarray, x_prev: np.ndarray) -> float:
    """仕様 §4.6：``ŷ_t = β0 + Σ_{i=1}^{5} β_i · x_i,{t−1}``。

    引数名が示すとおり、渡せるのは 1 期前の特徴量のみである。
    """
    if x_prev.size != HAR_N_COEF - 1 or not np.all(np.isfinite(x_prev)):
        return float("nan")
    return float(beta[0] + float(np.dot(beta[1:], x_prev)))


def sigma_oc_from_log_variance(y_hat: float, s2: float) -> float:
    """仕様 §4.6：``σ̂_OC,t = exp( ŷ_t / 2 + s² / 8 )``（対数正規の Jensen 補正）。"""
    if not np.isfinite(y_hat) or not np.isfinite(s2):
        return float("nan")
    return float(np.exp(y_hat / 2.0 + s2 / 8.0))
