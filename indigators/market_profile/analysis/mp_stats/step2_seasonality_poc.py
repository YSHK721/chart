"""Step2: 日中季節性 artifact — 2-0 凍結診断 / 2a ŝ(b) / 2b corr(ŝ,π) / 2c 時間変更 POC 符号検定。

ユーザー再定式化版（χ² 一様性検定は誤設定として撤回済み）:
  2-0: ブローカー気配の凍結チェック（ブラケット別 RV・distinct 数・同一値反復長）。
       近ゼロ RV ブラケットは市場現象でなくデータ生成欠陥 → 全ステップから除外。
  2a : Andersen-Bollerslev 乗法分解の季節性 ŝ(b) 推定（検定でなく推定）。
  2b : π(b) = P(POC 行滞在 | ブラケット b) と ŝ(b) の相関（記述統計・強い負 = 汚染確定）。
  2c : H0: E[sign(POC^raw − POC^τ)·sign(m_d − POC^τ)] = 0。統計量は仕様原文どおり
       T̄ = mean(T_d) を用い、判定は**シミュレーション校正臨界値**（依頼者指示・ISSUE-056）:
       日内バー順列サロゲート（季節性のみ破壊）を M 回生成し、**各標本で ŝ(b) を再推定**
       して T̄ の帰無分布を構成 → p_mc = (1 + #{T̄^(m) ≥ T̄_obs}) / (M+1)（片側上側）。
       共通項バイアス（T_d の両因子が POC^τ を共有）と ŝ プール推定による日跨ぎ従属を、
       帰無分布側に同じ機序を持たせることで相殺する。素の符号検定 p は参考値として併記。
       棄却 → 以降 POC^τ（時間変更 TPO）を使用。
  2d : Null B ブートストラップは Step5 と統合（本フェーズ未実装・I/F 予約のみ）。
"""

from __future__ import annotations

import numpy as np

from . import stats_core as sc
from .data_prep import (
    DailyFeatures,
    SessionData,
    VariantSpec,
    build_daily_features,
    calendar_bracket_of_mod,
    K_BRACKETS,
    permute_bars_within_day,
    PRIMARY,
    s_hat_full,
    tpo_tau_series,
)
from .report import StepResult

FREEZE_REL_THRESHOLD = 0.01     # 中央値ブラケット RV 比の凍結閾値（感度も併記）
FREEZE_SENSITIVITY = (0.005, 0.01, 0.05)
LOW_VOL_WINDOW_BRACKETS = 2     # m_d を測る低ボラ連続窓の幅（= 1 時間）


# --------------------------------------------------------------------------- #
# 2-0 凍結診断
# --------------------------------------------------------------------------- #
def freeze_diagnostics(f: DailyFeatures, *, rel_threshold: float = FREEZE_REL_THRESHOLD) -> dict:
    """ブラケット別の凍結指標と除外対象を返す。

    Returns:
        {"rv_mean": (K,), "rv_rel": (K,), "ndistinct_mean": (K,), "maxrun_p95": (K,),
         "frozen_brackets": tuple[int,...], "sensitivity": {threshold: [brackets]}}
    """
    rv_mean = np.nanmean(f.br_rv, axis=0)
    med = float(np.nanmedian(rv_mean))
    rv_rel = rv_mean / med if med > 0 else np.full(K_BRACKETS, np.nan)
    nd_mean = np.nanmean(np.where(f.br_ndistinct > 0, f.br_ndistinct, np.nan), axis=0)
    mr_p95 = np.nanpercentile(np.where(f.br_maxrun > 0, f.br_maxrun, np.nan), 95, axis=0)
    frozen = tuple(int(b) for b in np.flatnonzero(rv_rel < rel_threshold))
    sens = {
        f"{t:g}": [int(b) for b in np.flatnonzero(rv_rel < t)] for t in FREEZE_SENSITIVITY
    }
    return {
        "rv_mean": rv_mean,
        "rv_rel": rv_rel,
        "ndistinct_mean": nd_mean,
        "maxrun_p95": mr_p95,
        "frozen_brackets": frozen,
        "sensitivity": sens,
    }


# --------------------------------------------------------------------------- #
# 2b π(b)
# --------------------------------------------------------------------------- #
def poc_occupancy_by_bracket(f: DailyFeatures) -> "np.ndarray":
    """π(b) = 日次平均の「ブラケット b が POC 行を覆った」指示（主変種）。"""
    assert f.poc_touch_primary is not None
    return f.poc_touch_primary.mean(axis=0)


# --------------------------------------------------------------------------- #
# 2c 低ボラ窓と m_d
# --------------------------------------------------------------------------- #
def low_vol_window(s_hat: "np.ndarray", *, width: int = LOW_VOL_WINDOW_BRACKETS) -> "tuple[int, ...]":
    """ŝ の連続 width ブラケット移動平均が最小の窓（ブラケット id 列）を返す。

    ŝ==0（データ無し・除外）ブラケットは窓に含めない。
    """
    valid = s_hat > 0
    best, best_val = None, np.inf
    for b0 in range(K_BRACKETS - width + 1):
        seg = slice(b0, b0 + width)
        if not np.all(valid[seg]):
            continue
        v = float(np.mean(s_hat[seg]))
        if v < best_val:
            best, best_val = b0, v
    if best is None:
        return ()
    return tuple(range(best, best + width))


def mean_price_in_window(sd: SessionData, window: "tuple[int, ...]") -> "np.ndarray":
    """m_d = 低ボラ窓ブラケット内 close の日次平均（バー無しの日は NaN）。"""
    D = sd.n_days
    out = np.full(D, np.nan)
    wset = np.zeros(K_BRACKETS, dtype=bool)
    if window:
        wset[list(window)] = True
    for d in range(D):
        s, e = int(sd.starts[d]), int(sd.ends[d])
        mask = wset[calendar_bracket_of_mod(sd.mod[s:e])]
        if np.any(mask):
            out[d] = float(np.mean(sd.close[s:e][mask]))
    return out


# --------------------------------------------------------------------------- #
# 2c 符号検定
# --------------------------------------------------------------------------- #
def _sign_alignment_test(
    poc_raw: "np.ndarray", poc_tau: "np.ndarray", m: "np.ndarray"
) -> "dict[str, object]":
    """T_d = sign(POC^raw−POC^τ)·sign(m_d−POC^τ) の T̄ と素の符号検定（参考値）。

    素の p_sign は共通項バイアスにより H0 下で過剰棄却する（実測経験サイズ 20%）。
    判定には使わず、シミュレーション校正 p_mc（run_step2）を用いる。
    """
    valid = np.isfinite(poc_raw) & np.isfinite(poc_tau) & np.isfinite(m)
    t = np.sign(poc_raw[valid] - poc_tau[valid]) * np.sign(m[valid] - poc_tau[valid])
    n_pos = int(np.sum(t > 0))
    n_neg = int(np.sum(t < 0))
    n_eff = n_pos + n_neg
    t_bar = float((n_pos - n_neg) / n_eff) if n_eff > 0 else 0.0
    p_sign = sc.sign_test_pvalue(n_pos, n_neg)
    # 副: w_d = (POC^raw−POC^τ)·sign(m_d−POC^τ) の符号順位（同バイアスを持つ参考値）
    w = (poc_raw[valid] - poc_tau[valid]) * np.sign(m[valid] - poc_tau[valid])
    z_w, p_w = sc.wilcoxon_signed_rank(w)
    return {
        "n_effective": n_eff,
        "n_pos": n_pos,
        "n_neg": n_neg,
        "t_bar": t_bar,
        "p_sign_naive": p_sign,
        "wilcoxon_z_naive": z_w,
        "wilcoxon_p_naive": p_w,
    }


def _t_bar_for(
    sd: SessionData, f: DailyFeatures, variant: VariantSpec
) -> "tuple[dict[str, object], np.ndarray]":
    """1 標本（実データ or サロゲート）の T̄ 一式を、ŝ 再推定込みで計算する。

    シミュレーション校正の必須要件（依頼者指示）: サロゲートごとに ŝ(b)・低ボラ窓・
    POC^τ を**すべて再推定**する（真の τ=identity を使わない）。
    """
    s = s_hat_full(f)
    window = low_vol_window(s)
    m = mean_price_in_window(sd, window)
    tau = tpo_tau_series(sd, f, variant, s**2, exclude_brackets=f.excluded_brackets)
    test = _sign_alignment_test(f.poc_price[variant.key], tau["poc_price"], m)
    test["low_vol_window_brackets"] = list(window)
    return test, s


def calibrated_sign_test(
    sd: SessionData,
    f: DailyFeatures,
    variant: VariantSpec,
    *,
    seed: int,
    mc_reps: int = 199,
) -> "dict[str, object]":
    """シミュレーション校正臨界値による 2c 判定量を返す。

    帰無分布: 日内バー順列サロゲート（permute_bars_within_day）× ŝ(b) 再推定で
    T̄^(m) を M 回計算。p_mc = (1 + #{T̄^(m) ≥ T̄_obs}) / (M+1)（片側上側）。
    """
    obs, _ = _t_bar_for(sd, f, variant)
    t_obs = float(obs["t_bar"])
    rng = np.random.default_rng(seed)
    null_t = np.empty(mc_reps)
    for i in range(mc_reps):
        sd_null = permute_bars_within_day(sd, rng)
        f_null = build_daily_features(
            sd_null, variants=(variant,), primary=variant,
            exclude_brackets=f.excluded_brackets,
        )
        null_i, _ = _t_bar_for(sd_null, f_null, variant)
        null_t[i] = float(null_i["t_bar"])
    p_mc = float((1 + np.sum(null_t >= t_obs)) / (mc_reps + 1))
    return {
        **obs,
        "p_mc": p_mc,
        "mc_reps": mc_reps,
        "null_t_bar_mean": float(null_t.mean()),
        "null_t_bar_q95": float(np.percentile(null_t, 95)),
    }


def _effect_size(
    poc_raw: "np.ndarray", poc_tau: "np.ndarray", f: DailyFeatures
) -> "dict[str, float]":
    """Δ_d = (POC^raw−POC^τ)/(√RV_d·P̄_d)。median|Δ| と日次ストップ幅比。"""
    p_bar = (f.day_high + f.day_low) / 2.0
    denom = np.sqrt(f.rv_oc) * p_bar
    valid = np.isfinite(poc_raw) & np.isfinite(poc_tau) & (denom > 0)
    delta = (poc_raw[valid] - poc_tau[valid]) / denom[valid]
    med = float(np.median(np.abs(delta)))
    # 参考: 日次 1.96·√RV（リターン単位）に対する median|Δ| の比
    return {"median_abs_delta": med, "ratio_to_196": med / 1.96}


def run_step2(
    sd: SessionData,
    f: DailyFeatures,
    *,
    variants: "tuple[VariantSpec, ...]",
    primary: VariantSpec = PRIMARY,
    alpha: float = 0.05,
    seed: int = 42,
    mc_reps: int = 199,
) -> "tuple[StepResult, np.ndarray]":
    """Step2 実行（f は 2-0 の除外を反映済みの DailyFeatures を渡す）。

    判定は主変種のシミュレーション校正 p_mc（片側）で行う。他変種の素の統計は
    記述参考値（p_sign_naive）としてのみ併記する。

    Returns:
        (StepResult, s_hat 全標本 (K,))。s_hat は CLI の PNG 描画と Step3 へ流用。
    """
    diag = freeze_diagnostics(f)

    # 2a
    s = s_hat_full(f)
    lunch = list(np.round(s[2:7], 4))  # JST 昼休み近傍（02:00-04:30 UTC）の目視用

    # 2b
    pi = poc_occupancy_by_bracket(f)
    valid = (s > 0) & np.isfinite(pi)
    corr = float(np.corrcoef(s[valid], pi[valid])[0, 1]) if valid.sum() >= 3 else float("nan")

    # 2c 主判定: シミュレーション校正臨界値（ISSUE-056・依頼者指示）
    calib = calibrated_sign_test(sd, f, primary, seed=seed, mc_reps=mc_reps)
    reject = float(calib["p_mc"]) < alpha

    # 変種横断（記述参考値・素の統計のみ）
    window = tuple(calib["low_vol_window_brackets"])
    m = mean_price_in_window(sd, window)
    s2 = s**2
    variants_out: "dict[str, dict]" = {}
    primary_effect: "dict[str, float]" = {}
    for v in variants:
        tau = tpo_tau_series(sd, f, v, s2, exclude_brackets=f.excluded_brackets)
        test = _sign_alignment_test(f.poc_price[v.key], tau["poc_price"], m)
        eff = _effect_size(f.poc_price[v.key], tau["poc_price"], f)
        variants_out[v.key] = {**test, **eff}
        if v == primary:
            primary_effect = eff

    flags: "list[str]" = []
    if diag["frozen_brackets"]:
        flags.append("frozen_brackets_excluded")
    if np.isfinite(corr) and corr < -0.5:
        flags.append("poc_contaminated_by_seasonality")
    if reject:
        flags.append("use_time_changed_poc")

    stats: "dict[str, object]" = {
        "freeze_rv_rel_min": float(np.nanmin(diag["rv_rel"])),
        "freeze_rv_rel_argmin": int(np.nanargmin(diag["rv_rel"])),
        "frozen_brackets": list(diag["frozen_brackets"]),
        "freeze_sensitivity": diag["sensitivity"],
        "s_hat": [round(float(x), 4) for x in s],
        "s_hat_min": float(s[s > 0].min()) if np.any(s > 0) else 0.0,
        "s_hat_max": float(s.max()),
        "s_hat_lunch_2_6": lunch,
        "corr_s_pi": corr,
        **calib,
        **primary_effect,
        "alpha": alpha,
    }
    notes = (
        "2-0: 凍結ブラケットは全ステップ除外（除外済み一覧は meta 参照）。"
        "2c 判定はシミュレーション校正 p_mc（日内順列サロゲート×ŝ 再推定・片側）。"
        "素の p_sign_naive は共通項バイアスで過剰棄却するため参考値（ISSUE-056）。"
        "2c 棄却時は以降の TPO/conc を時間変更（τ）版で使用する。"
        "2d（Null B ブートストラップ z(p)）は Step5 と統合のため本フェーズ未実装。"
    )
    result = StepResult(
        step=2,
        name="seasonality_poc_artifact",
        decision="reject" if reject else "fail_to_reject",
        statistics=stats,
        variants=variants_out,
        flags=tuple(flags),
        notes=notes,
    )
    return result, s
