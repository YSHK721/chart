"""Step3: TPO 集中度が RV の冗長な代理か — H0: max_p N_d(p) ⊥ RV^OC_{d+1} | RV^OC_d。

変数（log 変換は必須: RV は右裾が重く、既存 HAR も y=log(RS) 規約）:
  y_{d+1} = ln RV^OC_{d+1}、c_d = ln(conc_d)。

ベースライン 2 段（判定は M0-HAR）:
  M0-lit: y_{d+1} ~ [1, y_d]                       … 仕様字義どおりの条件付け
  M0-HAR: y_{d+1} ~ [1, y_d, ȳ_{5,d}, ȳ_{22,d}]    … 日次 HAR(1/5/22)・厳しい冗長性検定
  M1 = M0 + β_c·c_d

検定:
  主: OLS+Newey-West HAC t（β_c）。副: ΔR² の定常ブートストラップ percentile CI。
判定（打ち切り規則）:
  主変種で HAC p < α かつ ΔR² CI 下限 > 0 → reject（Step4 へ進む資格）。
  それ以外 → fail_to_reject → MP 探索打ち切り。
"""

from __future__ import annotations

import numpy as np

from . import stats_core as sc
from .data_prep import DailyFeatures, assert_no_lookahead_daily
from .report import StepResult

HAR_LAGS = (1, 5, 22)


def _rolling_mean(y: "np.ndarray", w: int) -> "np.ndarray":
    """末尾 w 観測（自身含む）の移動平均。先頭 w-1 と、窓内に NaN を含む行は NaN。

    素の cumsum は NaN 混入で以降を全汚染する（実データの RV=0 日で顕在化・
    ISSUE-057）ため、有限値のみの cumsum＋有限数カウントで「窓内全て有限」の
    行だけ平均を返す。
    """
    fin = np.isfinite(y)
    y0 = np.where(fin, y, 0.0)
    c = np.cumsum(np.insert(y0, 0, 0.0))
    cnt = np.cumsum(np.insert(fin.astype(float), 0, 0.0))
    out = np.full(y.size, np.nan)
    s = c[w:] - c[:-w]
    k = cnt[w:] - cnt[:-w]
    ok = k == w
    tail = out[w - 1 :]
    tail[ok] = s[ok] / w
    return out


def build_regression_arrays(
    f: DailyFeatures, conc: "np.ndarray", *, use_har: bool
) -> "dict[str, np.ndarray]":
    """(y_{d+1}, X0_d, c_d) の整列済み配列を返す（ルックアヘッド assert 込み）。

    rv_oc<=0 / conc<=0 / HAR ラグ未充足の行は除外。特徴量は day d の完結値のみ、
    目的変数は d+1（添字シフト）で結合する。
    """
    y = np.where(f.rv_oc > 0, np.log(np.maximum(f.rv_oc, 1e-300)), np.nan)
    c = np.where(conc > 0, np.log(np.maximum(conc, 1e-300)), np.nan)
    y5 = _rolling_mean(y, HAR_LAGS[1])
    y22 = _rolling_mean(y, HAR_LAGS[2])

    idx = np.arange(f.n_days - 1)  # 特徴量行 d → 目的 d+1
    y_next = y[idx + 1]
    if use_har:
        X0 = np.column_stack([np.ones(idx.size), y[idx], y5[idx], y22[idx]])
    else:
        X0 = np.column_stack([np.ones(idx.size), y[idx]])
    x_c = c[idx]
    valid = (
        np.isfinite(y_next)
        & np.all(np.isfinite(X0), axis=1)
        & np.isfinite(x_c)
    )
    assert_no_lookahead_daily(f.day[idx[valid]], f.day[idx[valid] + 1])
    return {"y": y_next[valid], "X0": X0[valid], "c": x_c[valid]}


def _fit_models(y: "np.ndarray", X0: "np.ndarray", c: "np.ndarray", lag: int) -> dict:
    """M0/M1 の OLS+HAC・ΔR²・偏相関（残差相関）を返す。"""
    X1 = np.column_stack([X0, c])
    _, _, r2_0 = sc.ols(X0, y)
    beta1, resid1, r2_1 = sc.ols(X1, y)
    _, se1 = sc.ols_hac(X1, y, lag)
    t_c = float(beta1[-1] / se1[-1]) if se1[-1] > 0 else 0.0
    p_c = 2.0 * (1.0 - sc.norm_cdf(abs(t_c)))
    # 偏相関 = 残差相関 corr(resid(y|M0), resid(c|M0))
    _, ry, _ = sc.ols(X0, y)
    _, rc, _ = sc.ols(X0, c)
    denom = float(np.sqrt((ry @ ry) * (rc @ rc)))
    pcorr = float((ry @ rc) / denom) if denom > 0 else 0.0
    return {
        "beta_c": float(beta1[-1]),
        "t_hac": t_c,
        "p_hac": float(p_c),
        "r2_m0": float(r2_0),
        "r2_m1": float(r2_1),
        "delta_r2": float(r2_1 - r2_0),
        "pcorr": pcorr,
    }


def _bootstrap_ci(
    y: "np.ndarray", X0: "np.ndarray", c: "np.ndarray", *, seed: int, B: int
) -> "dict[str, list[float]]":
    """行 (y_{d+1}, X0_d, c_d) の定常ブートストラップ percentile CI（β_c・ΔR²）。"""
    n = y.size
    block = sc.pw_block_len(np.column_stack([y, c]))
    rng = np.random.default_rng(seed)
    X1 = np.column_stack([X0, c])
    b_c = np.empty(B)
    d_r2 = np.empty(B)
    for i in range(B):
        idx = sc.stationary_bootstrap_indices_fast(n, block, rng)
        yb, X0b, X1b = y[idx], X0[idx], X1[idx]
        _, _, r2_0 = sc.ols(X0b, yb)
        beta1, _, r2_1 = sc.ols(X1b, yb)
        b_c[i] = beta1[-1]
        d_r2[i] = r2_1 - r2_0
    return {
        "beta_c_ci": [float(np.percentile(b_c, 2.5)), float(np.percentile(b_c, 97.5))],
        "delta_r2_ci": [float(np.percentile(d_r2, 2.5)), float(np.percentile(d_r2, 97.5))],
    }


def run_step3(
    f: DailyFeatures,
    conc_by_key: "dict[str, np.ndarray]",
    *,
    primary_key: str,
    seed: int,
    B: int = 10_000,
    alpha: float = 0.05,
) -> StepResult:
    """Step3 実行。conc_by_key は変種 key → conc 日次系列（2c 棄却時は τ 版を渡す）。"""
    variants_out: "dict[str, dict]" = {}
    primary_stats: "dict[str, object]" = {}
    reject_count = 0
    n_variants_tested = 0

    for key, conc in conc_by_key.items():
        arr = build_regression_arrays(f, conc, use_har=True)
        n = arr["y"].size
        if n < 100:
            variants_out[key] = {"n": n, "error": "insufficient_data"}
            continue
        lag = sc.newey_west_lag(n)
        har = _fit_models(arr["y"], arr["X0"], arr["c"], lag)
        ci = _bootstrap_ci(arr["y"], arr["X0"], arr["c"], seed=seed, B=B)
        v_reject = (har["p_hac"] < alpha) and (ci["delta_r2_ci"][0] > 0)
        n_variants_tested += 1
        reject_count += int(v_reject)
        variants_out[key] = {
            "n": n,
            **{k: har[k] for k in ("beta_c", "t_hac", "p_hac", "delta_r2", "pcorr")},
            "delta_r2_ci": ci["delta_r2_ci"],
            "reject": v_reject,
        }
        if key == primary_key:
            lit = _fit_models(
                arr["y"],
                np.column_stack([arr["X0"][:, 0], arr["X0"][:, 1]]),  # [1, y_d]
                arr["c"],
                lag,
            )
            primary_stats = {
                "n": n,
                "nw_lag": lag,
                **har,
                **ci,
                "m0_lit": {k: lit[k] for k in ("beta_c", "t_hac", "p_hac", "delta_r2", "r2_m0")},
            }

    if primary_key not in variants_out or "error" in variants_out[primary_key]:
        return StepResult(
            step=3,
            name="tpo_concentration_incremental",
            decision="fail_to_reject",
            statistics={"error": "primary variant unavailable"},
            variants=variants_out,
            notes="主変種のデータ不足により判定不能 → 保守的に fail_to_reject。",
        )

    primary_reject = bool(variants_out[primary_key]["reject"])
    flags: "list[str]" = []
    if primary_reject and reject_count <= n_variants_tested // 2:
        flags.append("fragile_rejection")

    primary_stats["alpha"] = alpha
    primary_stats["reject_count_variants"] = reject_count
    primary_stats["n_variants_tested"] = n_variants_tested
    notes = (
        "判定 = 主変種の M0-HAR ベースラインで HAC p<α かつ ΔR² ブート CI 下限>0。"
        "fail_to_reject の場合、TPO 集中度は RV の冗長な代理 → MP 探索打ち切り"
        "（Step4 以降は skipped）。m0_lit は仕様字義どおりの条件付け（参考）。"
    )
    return StepResult(
        step=3,
        name="tpo_concentration_incremental",
        decision="reject" if primary_reject else "fail_to_reject",
        statistics=primary_stats,
        variants=variants_out,
        flags=tuple(flags),
        notes=notes,
    )
