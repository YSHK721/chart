"""Step4: 窓長横断の比較可能性 — H0: E[N_n(peak)] ∝ n^{1−H}（Hurst H・分散比検定）。

Lo & MacKinlay (1988) の分散比 VR(q)（重複 q 期間・不偏補正・異分散頑健 z*(q)）で
日次 close-to-close リターンの H0: ランダムウォーク（VR=1 ∀q）を検定し、
ln VR(q) = (2H−1)·ln q の OLS 勾配から Hurst Ĥ を推定する。

並行して、多窓 TPO（n 営業日プール・raw ブラケット）のピーク E[N_n(peak)] の
スケーリング b̂（ln E[N_n] ~ b·ln n）を実測し、仕様の関係 b = 1−H との整合を
ブートストラップ CI で確認する。以降のステップは N を n^{1−Ĥ} で除して正規化する
（仕様確定。整合しない場合は flags で warn）。打ち切り条件は無い（推定ステップ）。
"""

from __future__ import annotations

import math

import numpy as np

from . import stats_core as sc
from .data_prep import DailyFeatures
from .report import StepResult

VR_LAGS = (2, 4, 8, 16)
WINDOW_SET = (1, 2, 5, 10, 22)
N_ROWS_WINDOW = 40  # 多窓 TPO の行数（主変種 raw_r40 と同一）


# --------------------------------------------------------------------------- #
# Lo-MacKinlay 分散比
# --------------------------------------------------------------------------- #
def variance_ratio(r: "np.ndarray", q: int) -> "dict[str, float]":
    """VR(q)・異分散頑健 z*(q)・両側 p 値（Lo & MacKinlay 1988）。

    σ̂²(q) は重複 q 期間差分・不偏補正 m = q(n−q+1)(1−q/n)。
    θ̂(q) = Σ_{j=1}^{q−1} [2(q−j)/q]²·δ̂_j（δ̂_j は 4 次モーメント比）。
    """
    r = np.asarray(r, dtype=float)
    r = r[np.isfinite(r)]
    n = r.size
    mu = float(r.mean())
    d = r - mu
    var1 = float(d @ d) / (n - 1)
    p = np.cumsum(np.insert(r, 0, 0.0))  # 累積 log 価格（相対）
    dq = p[q:] - p[:-q] - q * mu
    m = q * (n - q + 1) * (1.0 - q / n)
    varq = float(dq @ dq) / m
    vr = varq / var1 if var1 > 0 else float("nan")
    # 異分散頑健 θ̂
    d2 = d * d
    denom = float(d2.sum()) ** 2
    theta = 0.0
    for j in range(1, q):
        delta_j = float(np.sum(d2[j:] * d2[:-j])) / denom * n
        theta += (2.0 * (q - j) / q) ** 2 * delta_j
    z = math.sqrt(n) * (vr - 1.0) / math.sqrt(theta) if theta > 0 else 0.0
    pval = 2.0 * (1.0 - sc.norm_cdf(abs(z)))
    return {"vr": vr, "z_star": float(z), "p": float(pval)}


def hurst_from_vr(r: "np.ndarray", qs: "tuple[int, ...]" = VR_LAGS) -> "dict[str, object]":
    """VR 系列から Ĥ（ln VR = (2H−1)·ln q の OLS）と各 q の検定量を返す。"""
    per_q = {q: variance_ratio(r, q) for q in qs}
    lnq = np.log(np.array(qs, dtype=float))
    lnvr = np.log(np.array([per_q[q]["vr"] for q in qs]))
    X = np.column_stack([np.ones(lnq.size), lnq])
    beta, _, _ = sc.ols(X, lnvr)
    h = 0.5 * (1.0 + float(beta[1]))
    # Chow-Denning 型: max|z*| の Bonferroni 上界 p
    max_z = max(abs(per_q[q]["z_star"]) for q in qs)
    p_joint = min(1.0, len(qs) * 2.0 * (1.0 - sc.norm_cdf(max_z)))
    return {
        "h": h,
        "vr_by_q": {str(q): round(per_q[q]["vr"], 6) for q in qs},
        "z_by_q": {str(q): round(per_q[q]["z_star"], 4) for q in qs},
        "p_by_q": {str(q): per_q[q]["p"] for q in qs},
        "max_abs_z": float(max_z),
        "p_joint_bonferroni": float(p_joint),
    }


# --------------------------------------------------------------------------- #
# 多窓 TPO ピークのスケーリング
# --------------------------------------------------------------------------- #
def window_peaks(f: DailyFeatures, day_idx: "np.ndarray", n: int) -> "np.ndarray":
    """day_idx の並びを n 日ずつ非重複に区切った各窓の TPO ピークを返す。

    窓 TPO: 窓内全日の raw ブラケット [lo,hi] をプールし、窓レンジを
    N_ROWS_WINDOW 行に等分して行 overlap 本数の最大値をとる。
    """
    assert f.br_hi is not None and f.br_lo is not None
    peaks = []
    for w0 in range(0, day_idx.size - n + 1, n):
        days = day_idx[w0 : w0 + n]
        hi = f.br_hi[days].ravel()
        lo = f.br_lo[days].ravel()
        ok = np.isfinite(hi) & np.isfinite(lo)
        hi, lo = hi[ok], lo[ok]
        if hi.size == 0:
            continue
        w_hi = float(np.max(f.day_high[days]))
        w_lo = float(np.min(f.day_low[days]))
        span = w_hi - w_lo
        row_w = span / N_ROWS_WINDOW if span > 0 else 1.0
        row_lo = w_lo + np.arange(N_ROWS_WINDOW) * row_w
        row_hi = row_lo + row_w
        cover = (lo[:, None] <= row_hi[None, :]) & (hi[:, None] >= row_lo[None, :])
        peaks.append(int(cover.sum(axis=0).max()))
    return np.asarray(peaks, dtype=float)


def scaling_slope(f: DailyFeatures, day_idx: "np.ndarray", n_list: "tuple[int, ...]") -> "tuple[float, dict[str, float]]":
    """(b̂, {n: mean_peak}) — ln E[N_n(peak)] = a + b·ln n の OLS 勾配。"""
    means = {}
    for n in n_list:
        pk = window_peaks(f, day_idx, n)
        means[str(n)] = float(pk.mean()) if pk.size else float("nan")
    lnn = np.log(np.array([float(k) for k in means], dtype=float))
    lnp = np.log(np.array(list(means.values()), dtype=float))
    ok = np.isfinite(lnp)
    X = np.column_stack([np.ones(int(ok.sum())), lnn[ok]])
    beta, _, _ = sc.ols(X, lnp[ok])
    return float(beta[1]), means


def _scaling_ci(
    f: DailyFeatures, n_list: "tuple[int, ...]", *, seed: int, B: int
) -> "tuple[float, float]":
    """窓ピーク系列の定常ブートによる b̂ の percentile 95% CI。

    日の再抽出で窓を再構成すると非連続日のプールで窓レンジが系統的に歪む
    （観測 b̂ から外れたバイアス分布になる）ため、観測された窓ピーク系列
    そのものを各 n レベルで再抽出し、平均→勾配を再計算する（窓構造保存）。
    レベル間の従属（同じ日を共有）は無視する近似（整合確認用途には十分）。
    """
    day_idx = np.arange(f.n_days)
    peaks_by_n = {n: window_peaks(f, day_idx, n) for n in n_list}
    lnn = np.log(np.array([float(n) for n in n_list]))
    rng = np.random.default_rng(seed)
    slopes = np.empty(B)
    blocks = {
        n: sc.pw_block_len(p) if p.size >= 8 else 1 for n, p in peaks_by_n.items()
    }
    for i in range(B):
        means = []
        for n in n_list:
            p = peaks_by_n[n]
            idx = sc.stationary_bootstrap_indices_fast(p.size, blocks[n], rng)
            means.append(float(p[idx].mean()))
        lnp = np.log(np.asarray(means))
        ok = np.isfinite(lnp)
        X = np.column_stack([np.ones(int(ok.sum())), lnn[ok]])
        beta, _, _ = sc.ols(X, lnp[ok])
        slopes[i] = beta[1]
    return float(np.percentile(slopes, 2.5)), float(np.percentile(slopes, 97.5))


# --------------------------------------------------------------------------- #
# Step4 実行
# --------------------------------------------------------------------------- #
def run_step4(
    f: DailyFeatures,
    *,
    seed: int,
    B: int = 500,
    alpha: float = 0.05,
    n_list: "tuple[int, ...]" = WINDOW_SET,
) -> StepResult:
    """Step4 実行。decision は H0: ランダムウォーク（VR=1）の Chow-Denning/Bonferroni 判定。

    打ち切りは無い。成果は正規化指数 1−Ĥ（以降の N はすべて n^{1−Ĥ} で除する・仕様確定）。
    実測スケーリング b̂ の CI が 1−Ĥ を含まない場合は flags=scaling_inconsistent。
    """
    r_cc = np.diff(np.log(f.c))  # 日次 close-to-close（CO ギャップ込み・窓 TPO と整合）
    vr = hurst_from_vr(r_cc)
    h = float(vr["h"])

    day_idx = np.arange(f.n_days)
    b_hat, means = scaling_slope(f, day_idx, n_list)
    b_lo, b_hi = _scaling_ci(f, n_list, seed=seed, B=B)
    norm_exp = 1.0 - h
    consistent = b_lo <= norm_exp <= b_hi

    flags: "list[str]" = []
    if not consistent:
        flags.append("scaling_inconsistent")

    reject = float(vr["p_joint_bonferroni"]) < alpha
    stats: "dict[str, object]" = {
        "n_returns": int(r_cc.size),
        "hurst_h": h,
        "normalization_exponent": norm_exp,
        **vr,
        "scaling_b_hat": b_hat,
        "scaling_b_ci": [b_lo, b_hi],
        "window_mean_peaks": means,
        "window_set": list(n_list),
        "n_rows_window": N_ROWS_WINDOW,
        "alpha": alpha,
    }
    notes = (
        "推定ステップ（打ち切りなし）。以降の窓横断比較では N を n^{1−Ĥ} で除する"
        "（仕様確定）。decision は H0: ランダムウォーク（VR=1 ∀q・Bonferroni 上界）の"
        "検定結果。scaling_b_hat は多窓 TPO ピークの実測スケーリングで、1−Ĥ との整合"
        "確認（不整合は flags）。"
    )
    return StepResult(
        step=4,
        name="hurst_variance_ratio",
        decision="reject" if reject else "fail_to_reject",
        statistics=stats,
        flags=tuple(flags),
        notes=notes,
    )
