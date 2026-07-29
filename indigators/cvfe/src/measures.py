"""バー別ボラティリティ測定量（仕様 §4.3）。

層名/責務:
    純粋ロジック層。1 バー分の対数価格から `RV` / `TSRV` / `RRANGE` / `PARK` を返す。
    一括経路・逐次経路の双方が本モジュールの同一関数を呼ぶ（仕様 §6 bit 一致の前提）。

副作用は :class:`~.logs.Logger` プロトコル経由の注入のみで、既定（``logger=None``）では
一切発生しない。仕様 §4.3 が「``TSRV_t <= 0`` の場合は ``RV^avg_t`` で代替し WARN ログ」
を求めるため、当該 1 点のみ注入口を持つ。

依存: 外部 numpy のみ / プロジェクト内 logs, errors。
"""

from __future__ import annotations

import numpy as np

from .errors import W01_TSRV_NONPOSITIVE
from .logs import Logger, resolve

#: 仕様 §4.3 "RRANGE"：バーを等長分割するサブ区間数（K3 で暫定値と明記）。
RRANGE_SUBINTERVALS: int = 12

_FOUR_LN2 = 4.0 * np.log(2.0)


def realized_variance(logp_samples: np.ndarray) -> float:
    """``RV_t = Σ r_i²``（仕様 §4.3 "RV"）。``logp_samples`` はサンプリング済み対数価格。"""
    if logp_samples.size < 2:
        return 0.0
    r = np.diff(logp_samples)
    return float((r ** 2).sum())


def two_scale_rv(logp_samples: np.ndarray, *,
                 logger: Logger | None = None, bar_index: int = -1) -> float:
    """Two-Scale Realized Variance（仕様 §4.3 "TSRV"・Zhang, Mykland & Aït-Sahalia 2005）。

    仕様の定義:
        ``K = ceil(n^(2/3))`` / ``RV^avg = (1/K) Σ_k RV^(k)`` /
        ``n̄ = (n − K + 1) / K`` / ``TSRV = (1 − n̄/n)^(-1) (RV^avg − (n̄/n) RV_all)``

    ``Σ_k RV^(k)`` は「k 番目のサブグリッド内で隣接する 2 点の差の二乗和」を
    k について合計したものであり、これはラグ ``K`` の差の二乗和
    ``Σ_{i=0}^{n-K} (p_{i+K} − p_i)²`` と**集合として同一**である
    （サブグリッド分割は添字を ``mod K`` で類別するため、各ラグ K 対がちょうど 1 回現れる）。
    したがって K 回のループを持たない閉形式で算出する（O(n)・結果は同値）。

    ``TSRV_t <= 0`` の場合は ``RV^avg_t`` を返し WARN ログを出力する（仕様 §4.3）。
    """
    n = logp_samples.size - 1
    if n < 2:
        return realized_variance(logp_samples)

    k = int(np.ceil(n ** (2.0 / 3.0)))
    k = max(1, min(k, n))

    rv_all = realized_variance(logp_samples)
    lag_k = logp_samples[k:] - logp_samples[:-k]
    rv_avg = float((lag_k ** 2).sum()) / k

    n_bar = (n - k + 1) / k
    ratio = n_bar / n
    if ratio >= 1.0:
        return rv_avg

    tsrv = (rv_avg - ratio * rv_all) / (1.0 - ratio)
    if not np.isfinite(tsrv) or tsrv <= 0.0:
        resolve(logger).emit(
            "WARN", W01_TSRV_NONPOSITIVE, bar_index,
            f"TSRV={tsrv!r} <= 0 のため RV^avg={rv_avg!r} で代替",
        )
        return rv_avg
    return tsrv


def realized_range(times: np.ndarray, logp: np.ndarray,
                   t_start: float, t_end: float,
                   m: int = RRANGE_SUBINTERVALS) -> float:
    """``RR_t = (1/(4 ln 2)) Σ_{j=1}^{m} (max_j p − min_j p)²``（仕様 §4.3 "RRANGE"）。

    バーを時間で ``m`` 等分し、各サブ区間 ``[e_j, e_{j+1})`` に含まれるティックの
    対数価格レンジを用いる。ティックが存在しないサブ区間の寄与は 0 とする
    （レンジが定義できないため。仕様は当該条件を定めていない）。
    """
    if times.size == 0 or m < 1:
        return 0.0
    sub_edges = t_start + np.arange(m + 1, dtype=np.float64) * ((t_end - t_start) / m)
    idx = np.searchsorted(times, sub_edges, side="left")
    total = 0.0
    for j in range(m):
        lo, hi = int(idx[j]), int(idx[j + 1])
        if hi - lo < 1:
            continue
        seg = logp[lo:hi]
        rng = float(seg.max() - seg.min())
        total += rng * rng
    return total / _FOUR_LN2


def parkinson(p_high: float, p_low: float) -> float:
    """``PK_t = (ln H_t − ln L_t)² / (4 ln 2)``（仕様 §4.3 "PARK"）。

    引数はすでに対数価格（``p = ln(mid)``）であるため、差がそのまま ``ln H − ln L``。
    """
    d = float(p_high) - float(p_low)
    return d * d / _FOUR_LN2
