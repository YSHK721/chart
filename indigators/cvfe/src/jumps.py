"""ジャンプ分離（仕様 §4.4・Barndorff-Nielsen & Shephard 2006 / Huang & Tauchen 2005）。

層名/責務:
    純粋ロジック層。1 バーのサンプリング済み収益 ``r_1..r_n`` と測定量 ``V_t`` から、
    連続成分 ``C_t``・ジャンプ成分 ``J_t``・検出フラグを返す。

    ``Φ^(-1)`` は共有プリミティブ ``common.normal_dist.norm_ppf``（Acklam 有理近似）を
    **無改変参照**する（本パッケージで再実装しない）。

仕様 §8 K5:
    ``n < 50`` のバーは検出力が未測定のため ``jump_flag = False`` に固定する。

依存: 標準 math / 外部 numpy / プロジェクト内 common.normal_dist, logs, errors。
"""

from __future__ import annotations

import math
from typing import NamedTuple

import numpy as np

from common.normal_dist import norm_ppf

from .errors import W02_BPV_NONPOSITIVE
from .logs import Logger, resolve

#: ``μ1 = sqrt(2/π)``（仕様 §4.4）
MU_1: float = math.sqrt(2.0 / math.pi)

#: ``μ_{4/3} = 2^(2/3) · Γ(7/6) / Γ(1/2)``（仕様 §4.4）
MU_4_3: float = 2.0 ** (2.0 / 3.0) * math.gamma(7.0 / 6.0) / math.gamma(0.5)

#: z 統計量の分散定数 ``(π²/4) + π − 5``（仕様 §4.4）
Z_VARIANCE_CONST: float = math.pi ** 2 / 4.0 + math.pi - 5.0

#: 仕様 §8 K5：この本数未満のバーはジャンプ判定を行わない。
JUMP_MIN_N: int = 50

#: 仕様 §4.4：ジャンプ分離を実行する測定量。これ以外は C_t = V_t / J_t = 0 とする。
JUMP_SEPARABLE_MEASURES: frozenset[str] = frozenset({"RV", "TSRV"})


class JumpResult(NamedTuple):
    """``C_t`` / ``J_t`` / ``jump_flag`` / ``z_t``（判定不能時 ``z`` は ``nan``）。"""

    c: float
    j: float
    flag: bool
    z: float


def bipower_variation(r: np.ndarray) -> float:
    """``BPV_t = μ1^(-2) · (n/(n−1)) · Σ_{i=2}^{n} |r_i|·|r_{i−1}|``（仕様 §4.4）。"""
    n = r.size
    if n < 2:
        return 0.0
    a = np.abs(r)
    s = float((a[1:] * a[:-1]).sum())
    return MU_1 ** -2 * (n / (n - 1)) * s


def tri_power_quarticity(r: np.ndarray) -> float:
    """``TQ_t = n · μ_{4/3}^(-3) · (n/(n−2)) · Σ_{i=3}^{n} |r_i|^{4/3}|r_{i−1}|^{4/3}|r_{i−2}|^{4/3}``。

    仕様 §4.4 の定義そのもの。
    """
    n = r.size
    if n < 3:
        return 0.0
    a = np.abs(r) ** (4.0 / 3.0)
    s = float((a[2:] * a[1:-1] * a[:-2]).sum())
    return n * MU_4_3 ** -3 * (n / (n - 2)) * s


def jump_test(v: float, r: np.ndarray, jump_alpha: float, *,
              logger: Logger | None = None, bar_index: int = -1) -> JumpResult:
    """仕様 §4.4 のジャンプ検定を実行する。

    ``z_t = (ln V_t − ln BPV_t) / sqrt( ((π²/4)+π−5) · (1/n) · max(1, TQ_t/BPV_t²) )``
    が ``c = Φ^(-1)(jump_alpha)`` を上回るとき ``C_t = BPV_t`` / ``J_t = V_t − BPV_t``。

    ``BPV_t <= 0`` のときは ``C_t = V_t`` / ``J_t = 0`` とし WARN を出力する（仕様 §4.4）。
    ``n < 50`` は §8 K5 により無条件で非検出。
    """
    n = int(r.size)
    if n < JUMP_MIN_N:
        return JumpResult(float(v), 0.0, False, float("nan"))

    bpv = bipower_variation(r)
    if not np.isfinite(bpv) or bpv <= 0.0:
        resolve(logger).emit(
            "WARN", W02_BPV_NONPOSITIVE, bar_index,
            f"BPV={bpv!r} <= 0 のため C_t = V_t とした",
        )
        return JumpResult(float(v), 0.0, False, float("nan"))

    if not np.isfinite(v) or v <= 0.0:
        # ln V_t が定義できない。ジャンプ判定を行わず連続成分へ落とす。
        return JumpResult(float(v), 0.0, False, float("nan"))

    tq = tri_power_quarticity(r)
    scale = Z_VARIANCE_CONST * (1.0 / n) * max(1.0, tq / (bpv * bpv))
    if not np.isfinite(scale) or scale <= 0.0:
        return JumpResult(float(v), 0.0, False, float("nan"))

    z = (math.log(v) - math.log(bpv)) / math.sqrt(scale)
    c = float(norm_ppf(jump_alpha))
    if z > c:
        return JumpResult(float(bpv), float(v) - float(bpv), True, z)
    return JumpResult(float(v), 0.0, False, z)
