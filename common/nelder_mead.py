"""nelder_mead — 導関数を使わない最小化（Nelder–Mead 法・scipy 非依存）。

①層名/責務:
    共有プリミティブ層。目的関数と初期点を受け取り、最小点の近似を返す。定義域・尤度・
    分布といったドメインの概念を一切持たない汎用ルーチンである。

②含む構造:
    nelder_mead : 反射・拡大・収縮・縮小の 4 操作による直接探索。

③配置の理由（ISSUE-479 Wave2 C-2）:
    元は GPD 当てはめモジュールの private 実装で、他所から共有できなかった。最適化手続きの
    変更要求は分布の変更要求とは無関係に来る（SRP）ため、独立した名前で公開する。
    旧 private 名は同一オブジェクトの別名として温存する（呼び出し側は無改変）。実装は無改変。

④依存:
    外部: numpy のみ。

出典:
    Nelder & Mead (1965) "A Simplex Method for Function Minimization",
    The Computer Journal 7(4):308-313.
"""
from __future__ import annotations

import numpy as np


def nelder_mead(f, x0: "np.ndarray", *, max_iter: int = 2000, tol: float = 1e-10) -> "np.ndarray":
    """Nelder–Mead 法（scipy 非依存）。cvfe の benchmarks と同型の最小実装。"""
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
