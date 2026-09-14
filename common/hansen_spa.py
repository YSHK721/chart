"""hansen_spa — Hansen(2005) SPA_c consistent 検定。

①層名/責務:
    共有プリミティブ層。候補群の損失差行列から SPA（Superior Predictive Ability）の p 値を
    返す。リサンプリングの実体は定常ブートストラップ核（stats_boot）へ委譲する。

②含む構造:
    HansenSpa : SPA_c consistent の p 値算出。

③配置の理由（ISSUE-479 Wave2 C-3）:
    元はブート核（stats_boot）と同居していたが、SPA は「核の利用者」であって核そのもの
    ではない（依存の向きが一方向であり、逆はない）。分割元には後方互換の再エクスポートを
    置かない（同じ実体への入口を 2 つ作ると片方だけが直され取り残しを生む）。実装は無改変。

④依存:
    標準: math / typing / 外部: numpy / プロジェクト内: common.stats_boot（同一パッケージ）。

出典:
    Hansen (2005) "A Test for Superior Predictive Ability", JBES 23(4).
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np

from common.stats_boot import (
    bootstrap_std,
    pw_block_len,
    stationary_bootstrap_indices,
)


class HansenSpa:
    """Hansen(2005) SPA_c consistent（詳細設計 §5.5・D3）。

    定常ブート（Politis-Romano 1994・幾何 block・wrap）・PW(2004) 自動ブロック長・
    studentize（√n·f̄_k/ω̂_k）・consistent 再センタリング（閾値 √(2 log log n)）。
    seed 固定（np.random.default_rng）で決定論再現（NFR-D3）。
    """

    def spa_pvalue(
        self,
        f_matrix: "Sequence[Sequence[float]]",
        *,
        seed: int,
        B: int = 5000,
    ) -> float:
        F = np.asarray([list(r) for r in f_matrix], dtype=float)
        n, K = F.shape
        if n < 2 or K < 1:
            return 1.0
        fbar = F.mean(axis=0)
        block = pw_block_len(F)
        omega = bootstrap_std(F, seed, B, block)
        omega = np.where(omega <= 0, 1e-12, omega)
        z = np.sqrt(n) * fbar / omega
        V = float(np.max(z))
        # consistent 再センタリング閾値 √(2 log log n)
        ll = math.log(math.log(n)) if n > math.e else 0.0
        thr = math.sqrt(2.0 * ll) if ll > 0 else 0.0
        g = np.where(z >= -thr, fbar, 0.0)
        rng = np.random.default_rng(seed)
        exceed = 0
        for _ in range(B):
            idx = stationary_bootstrap_indices(n, block, rng)
            Fb = F[idx]
            fbar_b = Fb.mean(axis=0)
            Vb = float(np.max(np.sqrt(n) * (fbar_b - g) / omega))
            if Vb > V:
                exceed += 1
        return exceed / B
