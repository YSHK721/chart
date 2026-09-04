"""step1/step2/step3/ランナーの合成 DGP テスト（seed 固定・決定論）。"""

from __future__ import annotations

import numpy as np
import pytest

from mp_stats import data_prep as dp
from mp_stats import report as rp
from mp_stats.step1_stop_ratio import rho_estimates, run_step1


# --------------------------------------------------------------------------- #
# 合成 DailyFeatures ヘルパ
# --------------------------------------------------------------------------- #
def _features_returns(r_co: np.ndarray, r_oc: np.ndarray, span=None) -> dp.DailyFeatures:
    D = r_oc.size
    z = np.zeros(D)
    K = dp.K_BRACKETS
    if span is None:
        span = np.ones(D)
        span[0] = np.nan
    return dp.DailyFeatures(
        day=np.arange(D, dtype=np.int64) * 86400, o=z + 1, c=z + 1,
        day_high=z + 2, day_low=z + 1, r_oc=r_oc, r_co=r_co, co_span_days=span,
        rv_oc=z + 1e-4, n_bars=z + 100, bracket_minutes=dp.bracket_minutes(),
        br_ret=np.zeros((D, K)), br_rv=np.zeros((D, K)),
        br_ndistinct=np.ones((D, K)), br_maxrun=np.ones((D, K)),
    )


# --------------------------------------------------------------------------- #
# Step1
# --------------------------------------------------------------------------- #
def test_rho_point_estimates():
    rng = np.random.default_rng(31)
    n = 200_000
    oc = rng.normal(scale=1.0, size=n)
    co = rng.normal(scale=np.sqrt(2.0), size=n)
    est = rho_estimates(co, oc)
    assert est["rho"] == pytest.approx(2.0, abs=0.05)
    assert est["rho_minus"] == pytest.approx(2.0, abs=0.05)
    assert est["kappa"] == pytest.approx(np.sqrt(3.0), abs=0.02)


def test_rho_minus_captures_downside_asymmetry():
    """CO の負側だけ分散 4 倍 → ρ は約 2.5、ρ⁻ は約 4 を回復する。"""
    rng = np.random.default_rng(32)
    n = 400_000
    oc = rng.normal(size=n)
    mag = np.abs(rng.normal(size=n))
    sign = rng.random(n) < 0.5
    co = np.where(sign, -2.0 * mag, 1.0 * mag)  # 下方 2 倍スケール
    est = rho_estimates(co, oc)
    # SV⁻(co) = ½·E[(2Z)²] = 2、SV⁻(oc) = ½·E[Z²] = 0.5 → ρ⁻ = 4
    assert est["rho_minus"] == pytest.approx(4.0, rel=0.05)
    assert est["rho_minus"] > est["rho"]


def test_step1_ci_covers_truth_and_decides():
    rng = np.random.default_rng(33)
    n = 3000
    r_oc = rng.normal(scale=0.01, size=n)
    r_co = np.empty(n)
    r_co[0] = np.nan
    r_co[1:] = rng.normal(scale=0.01 * np.sqrt(2.0), size=n - 1)
    f = _features_returns(r_co, r_oc)
    res = run_step1(f, seed=42, B=300)
    lo, hi = res.statistics["rho_ci"]
    assert lo < 2.0 < hi
    # 真の κ = √3 ≈ 1.732 → κ−1 は δ=0.02 を大きく超える
    assert res.decision == "non_negligible"
    assert "weekdays_only" in res.variants


def test_step1_negligible_when_co_tiny():
    rng = np.random.default_rng(34)
    n = 3000
    r_oc = rng.normal(scale=0.01, size=n)
    r_co = np.empty(n)
    r_co[0] = np.nan
    r_co[1:] = rng.normal(scale=0.0001, size=n - 1)  # CO ≈ 0
    f = _features_returns(r_co, r_oc)
    res = run_step1(f, seed=42, B=300)
    assert res.decision == "negligible"


# --------------------------------------------------------------------------- #
# ランナー（censoring）
# --------------------------------------------------------------------------- #
def test_censoring_after_step3_fail():
    results = [
        rp.StepResult(step=1, name="stop_width_rho", decision="negligible"),
        rp.StepResult(step=3, name="tpo_concentration_incremental", decision="fail_to_reject"),
    ]
    report = rp.build_report(results, meta={})
    steps = {s["step"]: s for s in report["steps"]}
    assert report["censoring"]["stopped_after"] == 3
    for n in (4, 5, 6, 7, 8):
        assert steps[n]["decision"] == "skipped"
        assert "censored" in steps[n]["notes"]


def test_no_censoring_when_step3_rejects():
    results = [
        rp.StepResult(step=3, name="tpo_concentration_incremental", decision="reject"),
    ]
    report = rp.build_report(results, meta={})
    assert report["censoring"]["stopped_after"] is None
    steps = {s["step"]: s for s in report["steps"]}
    assert "not implemented" in steps[4]["notes"]


def test_step_result_rejects_unknown_decision():
    with pytest.raises(AssertionError):
        rp.StepResult(step=1, name="x", decision="maybe")


# --------------------------------------------------------------------------- #
# Step2
# --------------------------------------------------------------------------- #
import pandas as pd

from mp_stats.step2_seasonality_poc import (
    freeze_diagnostics,
    low_vol_window,
    run_step2,
)

_BASE_DAY = 1704067200  # 2024-01-01 00:00 UTC


def _synth_m1(D: int, seed: int, pin: bool = False, carry: bool = False) -> pd.DataFrame:
    """1 分ランダムウォークの合成 M1。pin=True で mods[500,800) を低ボラ釘付け＋以降ドリフト。

    carry=True は価格を日を跨いで持ち越す（連続 RW・Step4 の H 推定用）。
    既定 False は毎日 20000 リセット（Step2 テストの従来挙動を維持）。
    """
    rng = np.random.default_rng(seed)
    mods = np.arange(61, 1439)
    frames = []
    level = 20000.0
    for d in range(D):
        steps = rng.normal(scale=2.0, size=mods.size)
        if pin:
            pin_mask = (mods >= 500) & (mods < 800)
            steps[pin_mask] = rng.normal(scale=0.4, size=int(pin_mask.sum()))
            steps[mods >= 800] += 0.5  # 釘付け後はドリフトで離れる
        base = level if carry else 20000.0
        closes = base + np.cumsum(steps)
        opens = np.concatenate([[base], closes[:-1]])
        if carry:
            level = closes[-1]
        frames.append(pd.DataFrame({
            "epoch": _BASE_DAY + d * 86400 + mods * 60,
            "open": opens,
            "high": np.maximum(opens, closes),
            "low": np.minimum(opens, closes),
            "close": closes,
        }))
    return pd.concat(frames, ignore_index=True)


def _run_step2_on(df: pd.DataFrame):
    sd = dp.build_session_data(df)
    f = dp.build_daily_features(sd, variants=(dp.PRIMARY,))
    return run_step2(sd, f, variants=(dp.PRIMARY,), seed=7, mc_reps=99)


def test_freeze_diagnostics_detects_frozen_bracket():
    rng = np.random.default_rng(41)
    D, K = 200, dp.K_BRACKETS
    br_rv = np.abs(rng.normal(loc=1e-4, scale=1e-5, size=(D, K)))
    br_rv[:, 7] = 1e-9  # 凍結様
    z = np.zeros(D)
    f = dp.DailyFeatures(
        day=np.arange(D, dtype=np.int64) * 86400, o=z + 1, c=z + 1,
        day_high=z + 2, day_low=z + 1, r_oc=z, r_co=z, co_span_days=z + 1,
        rv_oc=z + 1e-4, n_bars=z + 100, bracket_minutes=dp.bracket_minutes(),
        br_ret=rng.normal(size=(D, K)) * 1e-2, br_rv=br_rv,
        br_ndistinct=np.full((D, K), 10.0), br_maxrun=np.full((D, K), 2.0),
    )
    diag = freeze_diagnostics(f)
    assert diag["frozen_brackets"] == (7,)


def test_low_vol_window_contiguous_min():
    s = np.ones(dp.K_BRACKETS)
    s[12], s[13] = 0.3, 0.35
    assert low_vol_window(s) == (12, 13)


def test_step2c_size_no_seasonality():
    """一様ボラのランダムウォーク → 校正 p_mc は非棄却（サイズ制御・seed51 は素の検定だと偽陽性）。"""
    res, s = _run_step2_on(_synth_m1(120, seed=51))
    assert res.decision == "fail_to_reject"
    assert "use_time_changed_poc" not in res.flags
    # 素の p_sign_naive は共通項バイアスで小さく出る（ISSUE-056 の実測を固定化）
    assert res.statistics["p_mc"] > res.statistics["p_sign_naive"]


def test_step2c_power_pinned_low_vol():
    """低ボラ帯へ価格釘付けの DGP → 生 POC が釘付け価格へ引かれ校正検定でも棄却（検出力）。"""
    res, s = _run_step2_on(_synth_m1(120, seed=52, pin=True))
    # ŝ は釘付け帯（暦ブラケット 14..24 付近）で明確に低い
    assert s[16] < 0.5
    assert res.decision == "reject"
    assert "use_time_changed_poc" in res.flags
    assert res.statistics["n_pos"] > res.statistics["n_neg"]
    assert res.statistics["p_mc"] < 0.05


# --------------------------------------------------------------------------- #
# Step3
# --------------------------------------------------------------------------- #
from mp_stats.step3_incremental_r2 import build_regression_arrays, run_step3


def _features_rv_conc(y: np.ndarray, c: np.ndarray) -> tuple[dp.DailyFeatures, np.ndarray]:
    """ln RV = y、ln conc = c の合成 DailyFeatures（step3 入力）。"""
    D = y.size
    z = np.zeros(D)
    K = dp.K_BRACKETS
    f = dp.DailyFeatures(
        day=np.arange(D, dtype=np.int64) * 86400, o=z + 1, c=z + 1,
        day_high=z + 2, day_low=z + 1, r_oc=z, r_co=z, co_span_days=z + 1,
        rv_oc=np.exp(y), n_bars=z + 100, bracket_minutes=dp.bracket_minutes(),
        br_ret=np.zeros((D, K)), br_rv=np.zeros((D, K)),
        br_ndistinct=np.ones((D, K)), br_maxrun=np.ones((D, K)),
    )
    return f, np.exp(c)


def _ar1_with_signal(D: int, seed: int, beta_c: float, redundant: bool):
    """y_{t+1} = 0.5·y_t + β_c·c_t + ε。redundant なら c_t = y_t + noise（純冗長）。"""
    rng = np.random.default_rng(seed)
    y = np.empty(D)
    c = np.empty(D)
    y[0] = 0.0
    for t in range(D - 1):
        if redundant:
            c[t] = y[t] + 0.1 * rng.normal()
        else:
            c[t] = rng.normal()
        y[t + 1] = 0.5 * y[t] + beta_c * c[t] + rng.normal(scale=0.5)
    c[-1] = 0.0
    return y, c


def test_step3_detects_incremental_signal():
    y, c = _ar1_with_signal(3000, seed=61, beta_c=0.3, redundant=False)
    f, conc = _features_rv_conc(y, c)
    res = run_step3(f, {"raw_r40": conc}, primary_key="raw_r40", seed=42, B=400)
    assert res.decision == "reject"
    assert res.statistics["p_hac"] < 1e-6
    assert res.statistics["delta_r2_ci"][0] > 0


def test_step3_redundant_proxy_fails_to_reject():
    """c_t が y_t の純冗長コピー → 増分情報なし → fail_to_reject（サイズ制御）。"""
    y, c = _ar1_with_signal(3000, seed=62, beta_c=0.0, redundant=True)
    f, conc = _features_rv_conc(y, c)
    res = run_step3(f, {"raw_r40": conc}, primary_key="raw_r40", seed=42, B=400)
    assert res.decision == "fail_to_reject"


def test_step3_lookahead_alignment():
    """回帰行列の添字シフト（特徴量 d → 目的 d+1）を厳密検証。"""
    D = 30
    y = np.arange(D, dtype=float)  # ln RV = 0..29
    c = 100.0 + np.arange(D, dtype=float)
    f, conc = _features_rv_conc(y, c)
    arr = build_regression_arrays(f, conc, use_har=True)
    # HAR22 で先頭 21 行が落ち、y_next は特徴量行 +1
    assert arr["y"][0] == y[22]
    assert arr["X0"][0, 1] == y[21]
    assert arr["c"][0] == np.log(conc[21])


def test_fast_bootstrap_indices_distribution():
    """ベクトル化版の分布特性（範囲・再開率 ≈ 1/block・wrap 連続性）。"""
    rng = np.random.default_rng(71)
    n, block = 5000, 10
    idx = sc_boot = None
    from mp_stats import stats_core as sc
    idx = sc.stationary_bootstrap_indices_fast(n, block, rng)
    assert idx.shape == (n,) and idx.min() >= 0 and idx.max() < n
    cont = np.sum(idx[1:] == (idx[:-1] + 1) % n)
    restart_rate = 1.0 - cont / (n - 1)
    assert 0.07 < restart_rate < 0.14  # ≈ 1/block = 0.10


# --------------------------------------------------------------------------- #
# ランナー統合（合成 CSV → JSON/md 生成・決定論）
# --------------------------------------------------------------------------- #
def test_runner_end_to_end(tmp_path):
    import json
    import run_mp_tests

    df = _synth_m1(80, seed=91)
    ts = pd.to_datetime(df["epoch"], unit="s", utc=True).dt.strftime("%Y-%m-%d %H:%M:%S")
    csv = tmp_path / "m1.csv"
    pd.DataFrame({
        "date": ts, "open": df["open"], "high": df["high"],
        "low": df["low"], "close": df["close"], "volume": 1.0,
    }).to_csv(csv, index=False)

    out = tmp_path / "out"
    args = ["--csv", str(csv), "--seed", "5", "--B", "150",
            "--mc-reps", "49", "--m-reps", "60", "--tick-check-days", "0",
            "--out-dir", str(out)]
    report = run_mp_tests.main(args)

    data = json.loads((out / "mp_stats_report.json").read_text())
    assert (out / "mp_stats_report.md").exists()
    steps = {s["step"]: s for s in data["steps"]}
    assert set(steps) == {1, 2, 3, 4, 5, 6, 7, 8}
    assert steps[1]["decision"] in ("negligible", "non_negligible", "inconclusive")
    assert steps[2]["decision"] in ("reject", "fail_to_reject")
    assert steps[3]["decision"] in ("reject", "fail_to_reject")
    if steps[3]["decision"] == "fail_to_reject":
        assert data["censoring"]["stopped_after"] == 3
        assert all(steps[n]["decision"] == "skipped" for n in (4, 5, 6, 7, 8))

    # 同 seed 再実行でバイト同一（決定論）
    txt1 = (out / "mp_stats_report.json").read_text()
    run_mp_tests.main(args)
    assert (out / "mp_stats_report.json").read_text() == txt1


def test_step3_rolling_mean_nan_does_not_poison_tail():
    """RV=0（y=NaN）が窓を過ぎた後の行は有効に戻る（ISSUE-057 回帰テスト）。"""
    from mp_stats.step3_incremental_r2 import _rolling_mean

    y = np.arange(100, dtype=float)
    y[10] = np.nan
    m5 = _rolling_mean(y, 5)
    assert np.all(np.isnan(m5[:4]))          # ウォームアップ
    assert np.all(np.isnan(m5[10:15]))       # NaN を含む窓
    assert m5[9] == pytest.approx(np.mean(y[5:10]))
    assert m5[15] == pytest.approx(np.mean(y[11:16]))  # 窓通過後は回復
    assert np.all(np.isfinite(m5[15:]))


def test_step3_survives_sparse_nan_rv():
    """実データ相当: まばらな RV=0 日があっても n がほぼ全期間残る。"""
    y, c = _ar1_with_signal(3000, seed=63, beta_c=0.3, redundant=False)
    f, conc = _features_rv_conc(y, c)
    f.rv_oc[170] = 0.0
    f.rv_oc[1500] = 0.0
    arr = build_regression_arrays(f, conc, use_har=True)
    assert arr["y"].size > 2900


# --------------------------------------------------------------------------- #
# Step4
# --------------------------------------------------------------------------- #
from test_data_prep import _bar, _m1, _FRI, _MON  # noqa: E402
from mp_stats.step4_hurst import (
    hurst_from_vr,
    run_step4,
    variance_ratio,
    window_peaks,
)


def test_variance_ratio_iid_near_one():
    rng = np.random.default_rng(81)
    r = rng.normal(size=20_000)
    for q in (2, 4, 8):
        out = variance_ratio(r, q)
        assert out["vr"] == pytest.approx(1.0, abs=0.05)
        assert abs(out["z_star"]) < 3.0


def test_hurst_iid_half_and_persistent_above():
    rng = np.random.default_rng(82)
    r_iid = rng.normal(size=20_000)
    h_iid = hurst_from_vr(r_iid)
    assert h_iid["h"] == pytest.approx(0.5, abs=0.05)
    assert h_iid["p_joint_bonferroni"] > 0.05
    # AR(1) φ=0.3 の持続リターン → VR>1 → H>0.55・棄却
    r = np.empty(20_000)
    r[0] = rng.normal()
    for t in range(1, r.size):
        r[t] = 0.3 * r[t - 1] + rng.normal()
    h_ar = hurst_from_vr(r)
    assert h_ar["h"] > 0.55
    assert h_ar["p_joint_bonferroni"] < 0.01


def test_window_peaks_exact_small():
    """手組み 2 日: 窓 n=2 で両日のブラケットがプールされる。"""
    rows = [
        _bar(_FRI, 61, o=100.0, h=110.0, lo=100.0, c=105.0),
        _bar(_FRI, 95, o=105.0, h=112.0, lo=104.0, c=106.0),
        _bar(_MON, 61, o=106.0, h=112.0, lo=104.0, c=110.0),
        _bar(_MON, 95, o=110.0, h=120.0, lo=110.0, c=118.0),
    ]
    sd = dp.build_session_data(_m1(rows), min_bars=2)
    f = dp.build_daily_features(sd, variants=(dp.PRIMARY,))
    pk1 = window_peaks(f, np.arange(2), 1)
    pk2 = window_peaks(f, np.arange(2), 2)
    assert pk1.size == 2 and pk2.size == 1
    # n=2 窓は 4 ブラケット([100,110],[104,112],[104,112],[110,120])をプール
    # → 中央帯(104..112)で 3 本以上重なる
    assert pk2[0] >= 3
    assert pk2[0] >= pk1.max()


def test_step4_on_synthetic_rw():
    """RW 合成 M1 → H≈0.5・非棄却・b̂ が CI 内で 1−Ĥ と整合。"""
    df = _synth_m1(240, seed=83, carry=True)
    sd = dp.build_session_data(df)
    f = dp.build_daily_features(sd, variants=(dp.PRIMARY,))
    res = run_step4(f, seed=9, B=120, n_list=(1, 2, 4, 8))
    assert 0.35 < res.statistics["hurst_h"] < 0.65
    assert res.statistics["scaling_b_ci"][0] < res.statistics["scaling_b_hat"] < res.statistics["scaling_b_ci"][1]
    assert res.step == 4


# --------------------------------------------------------------------------- #
# Step5 (Null B)
# --------------------------------------------------------------------------- #
from mp_stats.step5_null_b import (
    N_ROWS,
    build_step_matrix,
    observed_row_counts,
    run_step5,
)


def _synth_ou_m1(D: int, seed: int, theta: float = 0.05) -> pd.DataFrame:
    """日内 OU（当日 open へ平均回帰）合成 M1 — 水準固有の受容が強い DGP。"""
    rng = np.random.default_rng(seed)
    mods = np.arange(61, 1439)
    frames = []
    for d in range(D):
        x = np.empty(mods.size)
        x[0] = 20000.0
        for t in range(1, mods.size):
            x[t] = x[t - 1] + theta * (20000.0 - x[t - 1]) + rng.normal(scale=2.0)
        opens = np.concatenate([[20000.0], x[:-1]])
        frames.append(pd.DataFrame({
            "epoch": _BASE_DAY + d * 86400 + mods * 60,
            "open": opens,
            "high": np.maximum(opens, x),
            "low": np.minimum(opens, x),
            "close": x,
        }))
    return pd.concat(frames, ignore_index=True)


def _step5_on(df: pd.DataFrame, seed=11, m=200):
    sd = dp.build_session_data(df)
    f = dp.build_daily_features(sd, variants=(dp.PRIMARY,))
    return run_step5(sd, f, seed=seed, m_reps=m)


def test_observed_row_counts_total():
    grid_d = np.linspace(100.0, 120.0, 1378)
    counts = observed_row_counts(grid_d, 100.0, 120.0)
    assert counts.sum() == 1378
    assert counts.size == N_ROWS


def test_step_matrix_chains_back_to_grid():
    df = _synth_m1(3, seed=95)
    sd = dp.build_session_data(df)
    f = dp.build_daily_features(sd, variants=(dp.PRIMARY,))
    S = build_step_matrix(sd, f)
    grid = dp.ffill_close_grid(sd)
    rebuilt = np.exp(np.log(f.o)[:, None] + np.cumsum(S, axis=1))
    assert np.allclose(rebuilt, grid, rtol=1e-10)


def test_step5_outputs_and_determinism():
    df = _synth_m1(30, seed=96)
    res1, out1 = _step5_on(df, seed=11, m=200)
    res2, out2 = _step5_on(df, seed=11, m=200)
    assert res1.decision == "estimated"
    assert np.nansum(np.abs(out1["poc_star"] - out2["poc_star"])) == 0.0
    assert np.isfinite(out1["z_max"]).sum() == 30


def test_step5_ou_exceeds_rw_zmax():
    """水準回帰（OU）の日は Null B を超える受容 → z_max が RW より系統的に大きい。"""
    _, out_rw = _step5_on(_synth_m1(40, seed=97), seed=12, m=300)
    _, out_ou = _step5_on(_synth_ou_m1(40, seed=98), seed=12, m=300)
    med_rw = np.nanmedian(out_rw["z_max"])
    med_ou = np.nanmedian(out_ou["z_max"])
    assert med_ou > med_rw * 1.5, (med_rw, med_ou)


# --------------------------------------------------------------------------- #
# Step6
# --------------------------------------------------------------------------- #
from mp_stats.step6_conditional import daily_va_bounds, run_part_a, run_step6
from mp_stats.step5_null_b import run_step5 as _run_step5_for6


def test_daily_va_bounds_exact_small():
    # 3 ブラケット梯子（test_tpo_exact_counts_and_poc と同一・N=[1,2,3,2,1]）
    rows = [
        _bar(_FRI, 61, o=100.0, h=110.0, lo=100.0, c=105.0),
        _bar(_FRI, 95, o=105.0, h=112.0, lo=104.0, c=106.0),
        _bar(_FRI, 121, o=106.0, h=118.0, lo=110.0, c=117.0),
    ]
    sd = dp.build_session_data(_m1(rows), min_bars=2)
    f = dp.build_daily_features(sd, variants=(dp.PRIMARY,))
    va_lo, va_hi = daily_va_bounds(f)
    # 40行TPO合計の70%を降順に積む → VA は中央帯に閉じ、日レンジ全体より狭い
    assert 100.0 <= va_lo[0] < va_hi[0] <= 118.0
    assert (va_hi[0] - va_lo[0]) < 18.0


def test_part_a_detects_injected_location_shift():
    """VA外寄り付き日に翌日RVを人工的に増幅 → 位置シフト検出。"""
    rng = np.random.default_rng(101)
    df = _synth_m1(400, seed=102, carry=True)
    sd = dp.build_session_data(df)
    f = dp.build_daily_features(sd, variants=(dp.PRIMARY,))
    va_lo, va_hi = daily_va_bounds(f)
    idx = np.arange(f.n_days - 1)
    outside = (f.o[idx + 1] < va_lo[idx]) | (f.o[idx + 1] > va_hi[idx])
    f.rv_oc[idx[outside] + 1] *= 3.0  # 外寄り付きの翌日 RV を 3 倍
    res = run_part_a(f, alpha=0.05)
    assert res["n_outside_open"] > 30
    assert res["reject"] and res["p_loc"] < 0.01 and res["gamma_loc"] > 0


def test_part_a_size_no_effect():
    df = _synth_m1(400, seed=103, carry=True)
    sd = dp.build_session_data(df)
    f = dp.build_daily_features(sd, variants=(dp.PRIMARY,))
    res = run_part_a(f, alpha=0.05)
    assert res["p_joint_bonferroni2"] > 0.05


def test_step6_end_to_end_runs():
    """RW 合成で step5→step6 が結線どおり完走し、構造が正しい。"""
    df = _synth_m1(140, seed=104, carry=True)
    sd = dp.build_session_data(df)
    f = dp.build_daily_features(sd, variants=(dp.PRIMARY,))
    _, out5 = _run_step5_for6(sd, f, seed=13, m_reps=150)
    res = run_step6(sd, f, out5["poc_star"], seed=13, m_reps_migration=150)
    assert res.step == 6
    assert res.decision in ("reject", "fail_to_reject")
    pb = res.statistics["part_b_migration"]
    assert pb["n_migration_days"] > 0
    if "error" not in pb:
        assert 0.0 <= pb["u_mean"] <= 1.0


# --------------------------------------------------------------------------- #
# Step7 / Step8
# --------------------------------------------------------------------------- #
from mp_stats.step7_spa import (
    Rule,
    rule_dummy,
    rule_grid,
    run_step7,
)
from mp_stats.step8_oos import run_step8

_SMALL_RULES = [
    Rule(b, r, v, t, w, bd)
    for b in (30,) for r in (20, 40) for v in (0.6, 0.7)
    for t in ("mid",) for w in (1, 2) for bd in ("open_next", "close_same")
]


def _features_for_spa(D=500, seed=71, inject=False):
    """合成 M1 → features（VA 帯は実 TPO 由来）。rv_oc は定常 AR(1)（対数）へ差し替える。

    価格持ち越し加法 RW のままだと ln RV が価格水準に連動して緩慢トレンドし、
    無条件分位の校正（Step8 Kupiec）が DGP 由来で壊れるため、目的変数だけ定常化する。
    inject=True は「VA外 open の当日 RV×6」の真の効果を対数で加算（因果・t 朝判定可能）。
    """
    df = _synth_m1(D, seed=seed, carry=True)
    sd = dp.build_session_data(df)
    f = dp.build_daily_features(sd, variants=(dp.PRIMARY,))
    rng = np.random.default_rng(seed + 1)
    ln_rv = np.empty(f.n_days)
    ln_rv[0] = -9.0
    for t in range(1, f.n_days):
        ln_rv[t] = -9.0 + 0.6 * (ln_rv[t - 1] + 9.0) + rng.normal(scale=0.3)
    if inject:
        target = Rule(30, 40, 0.7, "mid", 1, "open_next")
        dk = rule_dummy(f, target)
        ln_rv = ln_rv + np.where(dk == 1.0, np.log(6.0), 0.0)
    f.rv_oc[:] = np.exp(ln_rv)
    return f


def test_rule_grid_has_216_rules():
    assert len(rule_grid()) == 216
    assert len({r.key for r in rule_grid()}) == 216


def test_step7_detects_injected_rule_effect():
    f = _features_for_spa(inject=True)
    res, out = run_step7(f, seed=9, B=300, rules=_SMALL_RULES, warmup=150)
    assert res.decision == "reject", res.statistics
    assert res.statistics["best_mean_loss_diff"] > 0


def test_step7_null_no_effect():
    f = _features_for_spa(inject=False)
    res, _ = run_step7(f, seed=9, B=300, rules=_SMALL_RULES, warmup=150)
    assert res.decision == "fail_to_reject", res.statistics


def test_step8_confirms_oos_and_calibration():
    f = _features_for_spa(inject=True)
    _, out = run_step7(f, seed=9, B=100, rules=_SMALL_RULES, warmup=150)
    res = run_step8(f, out)
    assert res.decision == "reject", res.statistics
    assert res.statistics["oos_mean_loss_diff"] > 0
    assert res.statistics["p_kupiec"] >= 0.05


def test_step8_null_fails_to_reject():
    f = _features_for_spa(inject=False)
    _, out = run_step7(f, seed=9, B=100, rules=_SMALL_RULES, warmup=150)
    res = run_step8(f, out)
    assert res.decision == "fail_to_reject"
