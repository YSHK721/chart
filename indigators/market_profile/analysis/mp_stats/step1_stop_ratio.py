"""Step1: ストップ幅の妥当性 — ρ = Var(r^CO)/Var(r^OC) と下方版 ρ⁻（推定問題）。

v1.0（週次ボラバンド, simulator/domain/volatility_band.py）のストップ S = O·exp(−1.96·σ̂⁻)
は「日中のみ」の半実現分散から校正される（overnight リターン除外）。しかし週保有中は
日次 CO ギャップ（21:00 UTC メンテ跨ぎ・週末）を経路として被る。ρ⁻ が無視できなければ
経路下方分散が過小評価であり、補正係数 κ = √(1+ρ̂⁻)（ストップ距離の過小率 = κ−1）で
再校正が必要になる。本ステップは判定と報告まで（再校正はスコープ外）。
"""

from __future__ import annotations

import numpy as np

from . import stats_core as sc
from .data_prep import DailyFeatures
from .report import StepResult

DELTA_SENSITIVITY = (0.01, 0.02, 0.05)


def rho_estimates(co: "np.ndarray", oc: "np.ndarray") -> "dict[str, float]":
    """ρ / ρ⁻ / κ の点推定。分散は ddof=1、半分散は符号別二乗和/n（RS⁻ 規約）。"""
    var_co = float(np.var(co, ddof=1))
    var_oc = float(np.var(oc, ddof=1))
    sv_co = sc.semivariance_neg(co)
    sv_oc = sc.semivariance_neg(oc)
    rho = var_co / var_oc if var_oc > 0 else float("nan")
    rho_minus = sv_co / sv_oc if sv_oc > 0 else float("nan")
    kappa = float(np.sqrt(1.0 + rho_minus)) if np.isfinite(rho_minus) else float("nan")
    return {"rho": rho, "rho_minus": rho_minus, "kappa": kappa}


def _bootstrap_ci(
    co: "np.ndarray", oc: "np.ndarray", *, seed: int, B: int
) -> "dict[str, tuple[float, float]]":
    """ペア定常ブートストラップ percentile 95% CI（クロス依存・ボラクラスタ保存）。"""
    n = co.size
    block = sc.pw_block_len(np.column_stack([co, oc]))
    rng = np.random.default_rng(seed)
    boots = {"rho": np.empty(B), "rho_minus": np.empty(B), "kappa": np.empty(B)}
    for b in range(B):
        idx = sc.stationary_bootstrap_indices(n, block, rng)
        est = rho_estimates(co[idx], oc[idx])
        for k in boots:
            boots[k][b] = est[k]
    out = {}
    for k, v in boots.items():
        v = v[np.isfinite(v)]
        out[k] = (float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5)))
    return out


def _decide(kappa_ci: "tuple[float, float]", delta: float) -> str:
    lo, hi = kappa_ci
    if lo - 1.0 >= delta:
        return "non_negligible"
    if hi - 1.0 < delta:
        return "negligible"
    return "inconclusive"


def _estimate_block(
    co: "np.ndarray", oc: "np.ndarray", *, seed: int, B: int, delta: float
) -> "dict[str, object]":
    est = rho_estimates(co, oc)
    ci = _bootstrap_ci(co, oc, seed=seed, B=B)
    return {
        "n": int(co.size),
        "rho": est["rho"],
        "rho_ci": list(ci["rho"]),
        "rho_minus": est["rho_minus"],
        "rho_minus_ci": list(ci["rho_minus"]),
        "kappa": est["kappa"],
        "kappa_ci": list(ci["kappa"]),
        "decision": _decide(ci["kappa"], delta),
    }


def run_step1(
    f: DailyFeatures, *, seed: int, B: int = 10_000, delta: float = 0.02
) -> StepResult:
    """Step1 実行。主系列 = CO 全観測（週末込み）。頑健性 = 平日限定（co_span_days==1）。"""
    valid = np.isfinite(f.r_co) & np.isfinite(f.r_oc)
    co, oc = f.r_co[valid], f.r_oc[valid]
    span = f.co_span_days[valid]

    main = _estimate_block(co, oc, seed=seed, B=B, delta=delta)
    wk = span == 1.0
    weekdays = _estimate_block(co[wk], oc[wk], seed=seed + 10, B=B, delta=delta)

    sensitivity = {
        f"delta_{d:g}": _decide(tuple(main["kappa_ci"]), d) for d in DELTA_SENSITIVITY
    }
    kappa = main["kappa"]
    notes = (
        f"補正後ストップの含意: S_corr = O·exp(−1.96·κ̂·σ̂⁻)、κ̂ = {kappa:.4f}"
        f"（現行比ストップ距離 +{(kappa - 1) * 100:.2f}%）。再校正 2 案: "
        "(a) σ̂⁻ 入力の RS⁻ へ日次 CO⁻ の二乗を加算 / (b) SL_Z を 1.96·κ̂ へ置換。"
        "いずれも本フェーズではスコープ外（判定と報告のみ）。"
    )
    stats: "dict[str, object]" = {k: v for k, v in main.items() if k != "decision"}
    stats["delta"] = delta
    stats["delta_sensitivity"] = sensitivity
    return StepResult(
        step=1,
        name="stop_width_rho",
        decision=str(main["decision"]),
        statistics=stats,
        variants={"weekdays_only": weekdays},
        notes=notes,
    )
