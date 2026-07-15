"""統計プリミティブ（純 numpy / stdlib math — scipy 禁止の既存方針に従う）。

提供物:
  - 半分散 semivariance_neg / semivariance_pos（estimate_weekly_band の RS± と同規約: 符号別二乗和/n）
  - OLS + Newey-West HAC 分散 ols / ols_hac
  - 符号検定 sign_test_pvalue（二項・lgamma による厳密両側 p、H0: p=0.5）
  - Wilcoxon 符号順位 wilcoxon_signed_rank（正規近似・タイ補正・連続性補正）
  - 定常ブートストラップ薄ラッパ stationary_bootstrap_indices / pw_block_len
    （simulator/adapter/validation/spa.py の実装を read-only 再利用）

依存注記: spa._stationary_bootstrap_indices / spa._pw_block_len / var_backtests.norm_cdf は
private/adapter 実装の再利用（分析スクリプトに閉じる。レポート md に依存として明記する）。
"""

from __future__ import annotations

import math

import numpy as np

from . import _REPO_ROOT  # noqa: F401  (sys.path 挿入の副作用を保証)

# ISSUE-091 A1: simulator.adapter（他アプリ具象・private）への側方依存を廃し、中立共有核へ。
from common.stats_boot import (  # noqa: E402
    norm_cdf,
    pw_block_len as _pw_block_len,
    stationary_bootstrap_indices as _stationary_bootstrap_indices,
)

__all__ = [
    "semivariance_neg",
    "semivariance_pos",
    "ols",
    "ols_hac",
    "sign_test_pvalue",
    "wilcoxon_signed_rank",
    "stationary_bootstrap_indices",
    "pw_block_len",
    "norm_cdf",
]


# --------------------------------------------------------------------------- #
# 半分散（RS± と同規約: 符号別二乗和を n で割る。r==0 は非寄与）
# --------------------------------------------------------------------------- #
def semivariance_neg(x: "np.ndarray") -> float:
    """下方半分散 SV⁻ = (1/n)·Σ min(x,0)²（n は全観測数）。空配列は 0.0。"""
    x = np.asarray(x, dtype=float)
    if x.size == 0:
        return 0.0
    return float(np.sum(np.minimum(x, 0.0) ** 2) / x.size)


def semivariance_pos(x: "np.ndarray") -> float:
    """上方半分散 SV⁺ = (1/n)·Σ max(x,0)²（n は全観測数）。空配列は 0.0。"""
    x = np.asarray(x, dtype=float)
    if x.size == 0:
        return 0.0
    return float(np.sum(np.maximum(x, 0.0) ** 2) / x.size)


# --------------------------------------------------------------------------- #
# OLS + Newey-West HAC
# --------------------------------------------------------------------------- #
def ols(X: "np.ndarray", y: "np.ndarray") -> "tuple[np.ndarray, np.ndarray, float]":
    """OLS。(beta, resid, r2) を返す。X は (n,k) 計画行列（切片列は呼び手が付す）。"""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    tss = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - float(resid @ resid) / tss if tss > 0 else 0.0
    return beta, resid, r2


def ols_hac(
    X: "np.ndarray", y: "np.ndarray", lag: int
) -> "tuple[np.ndarray, np.ndarray]":
    """OLS + Newey-West(1987) HAC 標準誤差。(beta, hac_se) を返す。

    S = Σ_t u_t²·x_t x_tᵀ + Σ_{l=1..L} w_l·Σ_t u_t u_{t-l}·(x_t x_{t-l}ᵀ + x_{t-l} x_tᵀ)、
    w_l = 1 − l/(L+1)（Bartlett）。V = (XᵀX)⁻¹ S (XᵀX)⁻¹。
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    n, k = X.shape
    beta, u, _ = ols(X, y)
    xu = X * u[:, None]  # (n,k): x_t·u_t
    S = xu.T @ xu
    for l in range(1, lag + 1):
        w = 1.0 - l / (lag + 1.0)
        gamma = xu[l:].T @ xu[:-l]  # Σ_t x_t u_t · (x_{t-l} u_{t-l})ᵀ
        S += w * (gamma + gamma.T)
    xtx_inv = np.linalg.inv(X.T @ X)
    V = xtx_inv @ S @ xtx_inv
    se = np.sqrt(np.maximum(np.diag(V), 0.0))
    return beta, se


def newey_west_lag(n: int) -> int:
    """NW 推奨ラグ L = ⌈4·(n/100)^(2/9)⌉。"""
    return int(math.ceil(4.0 * (n / 100.0) ** (2.0 / 9.0)))


# --------------------------------------------------------------------------- #
# 符号検定（厳密二項・H0: p=0.5・両側）
# --------------------------------------------------------------------------- #
def _log_binom_pmf(n: int, k: int) -> float:
    """log P(X=k), X~Bin(n, 0.5)。lgamma でオーバーフロー回避。"""
    return (
        math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
        - n * math.log(2.0)
    )


def sign_test_pvalue(n_pos: int, n_neg: int) -> float:
    """符号検定の両側厳密 p 値（H0: P(+)=P(−)=0.5、ゼロは事前に除外して渡す）。

    p = min(1, 2·P(X ≤ min(n_pos, n_neg)))、X ~ Bin(n_pos+n_neg, 0.5)。
    n=0 は情報なしとして p=1。
    """
    n = n_pos + n_neg
    if n == 0:
        return 1.0
    k = min(n_pos, n_neg)
    tail = sum(math.exp(_log_binom_pmf(n, j)) for j in range(0, k + 1))
    return min(1.0, 2.0 * tail)


# --------------------------------------------------------------------------- #
# Wilcoxon 符号順位（正規近似・タイ補正・連続性補正）
# --------------------------------------------------------------------------- #
def wilcoxon_signed_rank(x: "np.ndarray") -> "tuple[float, float]":
    """Wilcoxon 符号順位検定（H0: 対称中心 0）。(z, 両側 p) を返す。

    ゼロ観測は除外（Wilcoxon 規約）。|x| の順位は平均順位（タイ対応）、
    分散はタイ補正 Σ(t³−t)/48 を減じ、|z| に 0.5 の連続性補正を入れる。
    m < 10 は近似が粗い旨を呼び手が承知の上で使う（本パイプラインでは m≈3500）。
    """
    x = np.asarray(x, dtype=float)
    x = x[x != 0.0]
    m = x.size
    if m == 0:
        return 0.0, 1.0
    ax = np.abs(x)
    order = np.argsort(ax, kind="stable")
    ranks = np.empty(m, dtype=float)
    sorted_ax = ax[order]
    i = 0
    while i < m:
        j = i
        while j + 1 < m and sorted_ax[j + 1] == sorted_ax[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0  # 平均順位（1 始まり）
        i = j + 1
    w_pos = float(np.sum(ranks[x > 0]))
    mean = m * (m + 1) / 4.0
    var = m * (m + 1) * (2 * m + 1) / 24.0
    # タイ補正: 同一 |x| グループごとに (t³−t)/48 を減じる
    _, counts = np.unique(sorted_ax, return_counts=True)
    var -= float(np.sum(counts.astype(float) ** 3 - counts)) / 48.0
    if var <= 0:
        return 0.0, 1.0
    diff = w_pos - mean
    z = (diff - math.copysign(0.5, diff)) / math.sqrt(var) if diff != 0 else 0.0
    p = 2.0 * (1.0 - norm_cdf(abs(z)))
    return float(z), float(min(1.0, max(0.0, p)))


# --------------------------------------------------------------------------- #
# 定常ブートストラップ薄ラッパ（spa.py 再利用）
# --------------------------------------------------------------------------- #
def pw_block_len(series_matrix: "np.ndarray") -> int:
    """Politis-White(2004) 自動ブロック長（spa._pw_block_len 委譲）。(n,K) 行列を受ける。"""
    F = np.asarray(series_matrix, dtype=float)
    if F.ndim == 1:
        F = F[:, None]
    return _pw_block_len(F)


def stationary_bootstrap_indices(n: int, block: int, rng) -> "np.ndarray":
    """Politis-Romano(1994) 定常ブート index 列（spa._stationary_bootstrap_indices 委譲）。"""
    return _stationary_bootstrap_indices(n, block, rng)


def stationary_bootstrap_indices_fast(n: int, block: int, rng) -> "np.ndarray":
    """定常ブート index 列のベクトル化版（spa 版と同一過程・実装のみ O(n) numpy）。

    各位置で確率 p=1/block により新ブロック開始（一様な再開点）、それ以外は前 index+1
    （wrap）。spa._stationary_bootstrap_indices と同じ幾何ブロック・wrap 過程だが、
    乱数列の消費順が異なるため同 seed でも同一列にはならない（分布は同一）。
    B=10,000 級の反復で python ループがボトルネックになる箇所に使う。
    """
    p = 1.0 / block if block > 0 else 1.0
    restart = rng.random(n) < p
    restart[0] = True
    n_seg = int(restart.sum())
    seg_start_val = rng.integers(0, n, size=n_seg)  # 各セグメントの開始 index
    seg_id = np.cumsum(restart) - 1                 # 各位置の属すセグメント
    seg_start_pos = np.flatnonzero(restart)         # セグメント先頭の位置
    offset = np.arange(n) - seg_start_pos[seg_id]
    return (seg_start_val[seg_id] + offset) % n
