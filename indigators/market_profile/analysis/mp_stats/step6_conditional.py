"""Step6: VA/POC の予測力 — 条件付き分布比較（Part A）＋ POC* 移動先検定（Part B・依頼者拡張）。

Part A（仕様）: H0「VA 外寄り付きは翌期 RV^OC 分布を変えない」。
  外寄り付きダミー 1{open_{d+1} ∉ VA_d} を、日次 HAR ベースライン付き回帰へ追加:
    位置: ln RV_{d+1} = HAR(d) + γ_loc·1{out} + ε        → HAC t(γ_loc)
    スケール: ln|ε̂| = δ0 + γ_scl·1{out} + u              → HAC t(γ_scl)
  判定 = Bonferroni(2) で min(p)·2 < α なら reject（分布が変わる＝エッジ候補あり）。
  VA は現行指標と同規約（TPO 降順 70% 質量・タイ index 昇順）を raw_r40 の日次
  プロファイルへ適用する。

Part B（依頼者仮説 2026-07-11）: 「不自然な価格から乖離すると、また次の不自然な
価格で滞在する」。
  移動日 = |POC*_{d+1} − POC*_d| > DEPART_ROWS 行（day d+1 の行幅基準）。
  観測停止位置 = POC*_{d+1}。過去受容集合 = {POC*_{d−L..d−1}}（ルックバック L 日・
  当日 d を除く・ルックアヘッドなし）。
  帰無停止位置 = day d+1 の Null B サロゲート停止位置（季節性保存のデタラメ一筆書き
  の最頻行）。u_{d+1} = P(帰無距離 < 観測距離)（PIT。距離 = 最近接過去受容水準への
  相対距離 |Δ|/P）。H0: u ~ 一様(0.5 中心) を Wilcoxon（片側・引き寄せ = u が小さい側）
  で集約。新値圏（過去受容集合が全て範囲外の日）は定義不能のため除外し件数を報告。
"""

from __future__ import annotations

import numpy as np

from . import stats_core as sc
from .data_prep import DailyFeatures, SessionData, calendar_bracket_of_mod
from .report import StepResult
from .step3_incremental_r2 import HAR_LAGS, _rolling_mean
from .step5_null_b import (
    N_ROWS,
    build_step_matrix,
    null_b_day_peaks,
    _grid_mods,
)

VA_PCT = 0.70
DEPART_ROWS = 4          # 移動日判定: POC* が前日から 4 行超（日レンジの 10%）移動
DEPART_SENSITIVITY = (2, 4, 8)
LOOKBACK = 60            # 過去受容集合のルックバック日数
M_MIGRATION = 2_000      # 移動先検定の日次サロゲート数


# --------------------------------------------------------------------------- #
# VA（現行指標と同規約: TPO 降順 70% 質量・タイ index 昇順）
# --------------------------------------------------------------------------- #
def daily_va_bounds(f: DailyFeatures) -> "tuple[np.ndarray, np.ndarray]":
    """raw ブラケット TPO（40 行）から日次 (va_low, va_high) を返す。"""
    assert f.br_hi is not None and f.br_lo is not None
    D = f.n_days
    va_lo = np.full(D, np.nan)
    va_hi = np.full(D, np.nan)
    for d in range(D):
        hi = f.br_hi[d]
        lo = f.br_lo[d]
        ok = np.isfinite(hi) & np.isfinite(lo)
        if not np.any(ok):
            continue
        low, high = float(f.day_low[d]), float(f.day_high[d])
        span = high - low
        row_w = span / N_ROWS if span > 0 else 1.0
        row_lo = low + np.arange(N_ROWS) * row_w
        row_hi = row_lo + row_w
        cover = (lo[ok][:, None] <= row_hi[None, :]) & (hi[ok][:, None] >= row_lo[None, :])
        tpo = cover.sum(axis=0).astype(float)
        total = tpo.sum()
        if total <= 0:
            continue
        order = sorted(range(N_ROWS), key=lambda i: (-tpo[i], i))
        centers = row_lo + row_w / 2.0
        cum = 0.0
        chosen = []
        for i in order:
            chosen.append(centers[i])
            cum += tpo[i]
            if cum >= total * VA_PCT:
                break
        va_lo[d] = min(chosen)
        va_hi[d] = max(chosen)
    return va_lo, va_hi


# --------------------------------------------------------------------------- #
# Part A: VA 外寄り付き → 翌期 RV^OC の条件付き分布
# --------------------------------------------------------------------------- #
def run_part_a(f: DailyFeatures, *, alpha: float) -> "dict[str, object]":
    va_lo, va_hi = daily_va_bounds(f)
    y = np.where(f.rv_oc > 0, np.log(np.maximum(f.rv_oc, 1e-300)), np.nan)
    y5 = _rolling_mean(y, HAR_LAGS[1])
    y22 = _rolling_mean(y, HAR_LAGS[2])
    idx = np.arange(f.n_days - 1)
    open_next = f.o[idx + 1]
    out_dummy = ((open_next < va_lo[idx]) | (open_next > va_hi[idx])).astype(float)
    y_next = y[idx + 1]
    X0 = np.column_stack([np.ones(idx.size), y[idx], y5[idx], y22[idx]])
    valid = (
        np.isfinite(y_next)
        & np.all(np.isfinite(X0), axis=1)
        & np.isfinite(va_lo[idx])
    )
    yv, X0v, dv = y_next[valid], X0[valid], out_dummy[valid]
    n = yv.size
    lag = sc.newey_west_lag(n)
    # 位置シフト
    X1 = np.column_stack([X0v, dv])
    beta, se = sc.ols_hac(X1, yv, lag)
    t_loc = float(beta[-1] / se[-1]) if se[-1] > 0 else 0.0
    p_loc = 2.0 * (1.0 - sc.norm_cdf(abs(t_loc)))
    # スケールシフト（HAR 残差の対数絶対値）
    _, resid, _ = sc.ols(X0v, yv)
    la = np.log(np.maximum(np.abs(resid), 1e-12))
    Xs = np.column_stack([np.ones(n), dv])
    beta_s, se_s = sc.ols_hac(Xs, la, lag)
    t_scl = float(beta_s[-1] / se_s[-1]) if se_s[-1] > 0 else 0.0
    p_scl = 2.0 * (1.0 - sc.norm_cdf(abs(t_scl)))
    p_joint = min(1.0, 2.0 * min(p_loc, p_scl))  # Bonferroni(2)
    return {
        "n": int(n),
        "n_outside_open": int(dv.sum()),
        "outside_rate": float(dv.mean()),
        "gamma_loc": float(beta[-1]),
        "t_loc": t_loc,
        "p_loc": float(p_loc),
        "gamma_scale": float(beta_s[-1]),
        "t_scale": t_scl,
        "p_scale": float(p_scl),
        "p_joint_bonferroni2": float(p_joint),
        "reject": bool(p_joint < alpha),
    }


# --------------------------------------------------------------------------- #
# Part B: POC* 移動先検定（依頼者仮説）
# --------------------------------------------------------------------------- #
def _nearest_rel_dist(x: float, levels: "np.ndarray") -> float:
    """x から最近接水準への相対距離 |Δ|/x。levels 空は NaN。"""
    if levels.size == 0 or not np.isfinite(x) or x <= 0:
        return float("nan")
    return float(np.min(np.abs(levels - x)) / x)


def run_part_b(
    sd: SessionData,
    f: DailyFeatures,
    poc_star: "np.ndarray",
    *,
    seed: int,
    m_reps: int = M_MIGRATION,
    depart_rows: int = DEPART_ROWS,
    lookback: int = LOOKBACK,
) -> "dict[str, object]":
    """移動日の観測停止位置 vs Null B 帰無停止位置の PIT 集約（片側 Wilcoxon）。"""
    D = sd.n_days
    S = build_step_matrix(sd, f)
    b_of_minute = calendar_bracket_of_mod(_grid_mods())
    rng = np.random.default_rng(seed)

    u_vals = []
    n_migration = 0
    n_no_prior = 0
    for e in range(lookback + 1, D):  # e = 停止日 (d+1)、d = e−1
        d = e - 1
        if not (np.isfinite(poc_star[e]) and np.isfinite(poc_star[d])):
            continue
        low, high = float(f.day_low[e]), float(f.day_high[e])
        span = high - low
        if span <= 0:
            continue
        row_w = span / N_ROWS
        if abs(poc_star[e] - poc_star[d]) <= depart_rows * row_w:
            continue  # 移動日でない
        n_migration += 1
        prior = poc_star[e - lookback : e - 1]  # おおよそ d−L..d−1（e−1=d は除く）
        prior = prior[np.isfinite(prior)]
        # 新値圏: 過去受容水準が当日レンジ±1レンジ内に一つも無い日は定義不能
        near = prior[(prior >= low - span) & (prior <= high + span)]
        if near.size == 0:
            n_no_prior += 1
            continue
        d_obs = _nearest_rel_dist(float(poc_star[e]), near)
        peaks = null_b_day_peaks(
            S, b_of_minute, e, float(f.o[e]), low, high, rng=rng, m_reps=m_reps
        )
        peaks = peaks[np.isfinite(peaks)]
        if peaks.size < m_reps // 2:
            continue
        d_null = np.array([_nearest_rel_dist(p, near) for p in peaks])
        d_null = d_null[np.isfinite(d_null)]
        if d_null.size == 0 or not np.isfinite(d_obs):
            continue
        # PIT: 帰無停止位置が観測より近い割合（連続補正で中央 0.5）
        u = (np.sum(d_null < d_obs) + 0.5 * np.sum(d_null == d_obs)) / d_null.size
        u_vals.append(u)

    u = np.asarray(u_vals, dtype=float)
    if u.size < 30:
        return {
            "n_migration_days": n_migration,
            "n_tested": int(u.size),
            "n_no_prior_level": n_no_prior,
            "error": "insufficient_migration_days",
        }
    # 片側: 引き寄せ → 観測距離が帰無より小 → u < 0.5
    z_w, p_two = sc.wilcoxon_signed_rank(u - 0.5)
    p_one = p_two / 2.0 if z_w < 0 else 1.0 - p_two / 2.0
    return {
        "n_migration_days": n_migration,
        "n_tested": int(u.size),
        "n_no_prior_level": n_no_prior,
        "u_mean": float(u.mean()),
        "u_median": float(np.median(u)),
        "wilcoxon_z": float(z_w),
        "p_one_sided": float(p_one),
        "m_reps": m_reps,
        "depart_rows": depart_rows,
        "lookback_days": lookback,
    }


# --------------------------------------------------------------------------- #
# Step6 実行
# --------------------------------------------------------------------------- #
def run_step6(
    sd: SessionData,
    f: DailyFeatures,
    poc_star: "np.ndarray",
    *,
    seed: int,
    alpha: float = 0.05,
    m_reps_migration: int = M_MIGRATION,
) -> StepResult:
    part_a = run_part_a(f, alpha=alpha)
    part_b = run_part_b(sd, f, poc_star, seed=seed, m_reps=m_reps_migration)
    # 感度: 移動日閾値（主判定は DEPART_ROWS）
    sens = {}
    for r in DEPART_SENSITIVITY:
        if r == DEPART_ROWS:
            continue
        sens[f"depart_{r}rows"] = run_part_b(
            sd, f, poc_star, seed=seed + r, m_reps=max(500, m_reps_migration // 4),
            depart_rows=r,
        )

    reject = bool(part_a["reject"])
    flags: "list[str]" = []
    if "error" not in part_b and float(part_b["p_one_sided"]) < alpha and float(part_b["u_mean"]) < 0.5:
        flags.append("migration_confirmed")

    stats: "dict[str, object]" = {
        "part_a": part_a,
        "part_b_migration": part_b,
        "alpha": alpha,
    }
    notes = (
        "decision は Part A（仕様 H0: VA 外寄り付きは翌期 RV^OC 分布を変えない・"
        "位置/スケール Bonferroni(2)）。Part B は依頼者仮説（POC* 離脱後の停止位置が"
        "過去 POC* 集合へ引き寄せられるか）の片側検定で、成立時は flags="
        "migration_confirmed。帰無停止位置は Null B サロゲートの最頻行＝季節性保存の"
        "デタラメ一筆書き。新値圏（過去受容水準がレンジ近傍に無い日）は除外し件数報告。"
    )
    return StepResult(
        step=6,
        name="va_open_conditional",
        decision="reject" if reject else "fail_to_reject",
        statistics=stats,
        variants=sens,
        flags=tuple(flags),
        notes=notes,
    )
