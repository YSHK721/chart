"""§9 段階 2：モンテカルロ検証。

正本仕様: indigators/cvfe/CVFE_spec_v1.0.md §9 段階 2・§5

検証観点:
    - 確率ボラ DGP（``ln σ_t`` の AR(1)、``φ = 0.98``）上で、M4 の QLIKE 平均が
      M0・M1・M3 のいずれよりも小さい
    - マイクロストラクチャノイズ（``ω/σ = 0.1, 0.5, 1.0``）を注入した条件で、
      §4.1 の判定が ``ω/σ ≥ 0.5`` のとき ``"DEGRADED"`` 以上を返す

``ω/σ`` の解釈:
    仕様は基準となる ``σ`` の尺度を明示しない。本テストは
    「最細格子（Δ = 5 秒）1 サンプルあたりの真の収益の標準偏差」に対する比と解釈する。
    この解釈の下でのみ ``RV(Δ)/IV ≈ 1 + 2(ω/σ)²`` となり、仕様 §4.1-6 の判定閾値
    （0.10 / 0.50）が ``ω/σ`` の 0.1 / 0.5 / 1.0 と整合する尺度になる。
    バー全体の σ に対する比と解釈すると ``ω/σ = 0.1`` でも S ≈ 14 となり、
    3 水準すべてが DEGRADED となって検定が空虚になる（ISSUE-200）。
"""

import sys
from pathlib import Path

import numpy as np
import pytest

_PKG_DIR = Path(__file__).resolve().parents[1]
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from cvfe_synthetic import make_dataset, make_sv_dataset, stochastic_vol_series  # noqa: E402
from src import (  # noqa: E402
    compute_cvfe,
    diagnose_quality,
    forecast_ewma,
    forecast_har_plain,
    forecast_moving_average,
    measure_all_bars,
    qlike,
)
from src.dto import CvfeParams  # noqa: E402
from src.sampling import validate_edges, validate_ticks  # noqa: E402

N_HAR = 500
N_BARS = 1_500
BAR_SEC = 3_600
TICK_SEC = 5
PHI = 0.98


@pytest.fixture(scope="module")
def stochastic_vol_run():
    """確率ボラ DGP 上で M4 と M0・M1・M3 の QLIKE 系列を作る。"""
    sigma = stochastic_vol_series(N_BARS, seed=101, phi=PHI, sd_ln_sigma=0.30)
    ticks, edges = make_dataset(N_BARS, bar_sec=BAR_SEC, tick_sec=TICK_SEC,
                                seed=102, sigma_bar=sigma)
    res = compute_cvfe(ticks, edges, BAR_SEC, n_har=N_HAR)

    times, logp = validate_ticks(ticks)
    e = validate_edges(edges)
    params = CvfeParams(bar_interval_sec=BAR_SEC, n_har=N_HAR)
    quality = diagnose_quality(times, logp, e, params.n_har, params.freeze_thresh)
    measures = measure_all_bars(times, logp, e, quality, params)

    v = np.array([m.v for m in measures], dtype=np.float64)
    c = np.array([m.c for m in measures], dtype=np.float64)
    p_close = np.array([m.p_close for m in measures], dtype=np.float64)
    t0 = params.first_available_index

    # 代理変数（仕様 §5.1）：delta_star_sec での RV ＝ measure_id="RV" の V_t。
    proxy = np.where(res.available, v, np.nan)

    forecasts = {
        "M0": forecast_moving_average(v, t0),
        "M1": forecast_ewma(v, t0),
        "M3": forecast_har_plain(c, p_close, t0, N_HAR),
        "M4": np.where(res.available, res.sigma_hat, np.nan),
    }
    losses = {k: qlike(proxy, f) for k, f in forecasts.items()}
    # 全モデルで有限な同一標本に揃える（比較の同一標本性）。
    common = np.all(np.vstack([np.isfinite(x) for x in losses.values()]), axis=0)
    return {k: x[common] for k, x in losses.items()}, res, int(common.sum())


def test_stage2_sample_is_large_enough(stochastic_vol_run):
    """比較に用いる共通標本が空でないこと（空虚な合格を防ぐ）。"""
    _, res, n = stochastic_vol_run
    assert n >= 500, f"共通標本 {n} 本では §9 段階 2 の比較にならない"
    assert res.measure_id == "RV"


@pytest.mark.parametrize("rival", [
    "M0",
    "M1",
    pytest.param("M3", marks=pytest.mark.xfail(strict=True, reason=(
        "仕様の DGP 過少規定（ISSUE-202）。§9 段階 2 の DGP は『ln σ_t の AR(1)』のみで "
        "ジャンプもレバレッジも含まないため、M4（HAR-CJ-L）の x4・x5 に説明対象が存在せず "
        "M3（項なし HAR）に対する優位は原理的に生じない。実測 QLIKE 平均 "
        "M4=0.0105268 / M3=0.0104954（差 +0.3%）。ジャンプとレバレッジを含む DGP では "
        "M4 が M3 を上回ることを test_m4_beats_m3_when_dgp_has_jumps_and_leverage で実証済み。"))),
])
def test_stage2_m4_qlike_beats_rivals(stochastic_vol_run, rival):
    """M4 の QLIKE 平均が M0・M1・M3 のいずれよりも小さい（§9 段階 2）。"""
    losses, _, _ = stochastic_vol_run
    m4 = float(np.mean(losses["M4"]))
    other = float(np.mean(losses[rival]))
    assert m4 < other, f"QLIKE 平均 M4={m4!r} >= {rival}={other!r}"


# --------------------------------------------------------------------------------------
# ノイズ注入下の気配品質診断
# --------------------------------------------------------------------------------------

_NOISE_BARS = 520


def _gate_for_noise(ratio: float):
    ticks, edges = make_dataset(_NOISE_BARS, bar_sec=BAR_SEC, tick_sec=TICK_SEC,
                                seed=203, noise_omega_ratio=ratio)
    times, logp = validate_ticks(ticks)
    e = validate_edges(edges)
    return diagnose_quality(times, logp, e, N_HAR, 0.05)


@pytest.fixture(scope="module")
def noise_reports():
    return {r: _gate_for_noise(r) for r in (0.1, 0.5, 1.0)}


@pytest.mark.parametrize("ratio", [
    pytest.param(0.5, marks=pytest.mark.xfail(strict=True, reason=(
        "仕様の内部不整合（ISSUE-201）。ω/σ = 0.5 での実測 S = 0.4718 は §4.1-6 の "
        "DEGRADED 閾値 S > 0.50 に届かず PASS と判定される。一方 §9 段階 2 は "
        "ω/σ >= 0.5 で DEGRADED 以上を要求する。閾値 0.50 は仕様が根拠を示していない "
        "固定値であり、裁定前に変更しない。"))),
    1.0,
])
def test_stage2_noise_triggers_degraded_or_worse(noise_reports, ratio):
    """``ω/σ >= 0.5`` のとき §4.1 の判定が DEGRADED 以上になる（§9 段階 2）。"""
    rep = noise_reports[ratio]
    assert rep.quality_gate in ("DEGRADED", "FAIL"), (
        f"ω/σ={ratio}: gate={rep.quality_gate}, S={rep.signature_slope!r}")


def test_stage2_low_noise_stays_pass(noise_reports):
    """``ω/σ = 0.1`` では PASS に留まる（判定が飽和していないことの確認）。"""
    rep = noise_reports[0.1]
    assert rep.quality_gate == "PASS", f"S={rep.signature_slope!r}"


def test_stage2_signature_slope_increases_with_noise(noise_reports):
    """シグネチャ勾配 S がノイズ水準に対して単調増加する（診断量の妥当性）。"""
    s = [noise_reports[r].signature_slope for r in (0.1, 0.5, 1.0)]
    assert s[0] < s[1] < s[2], f"S = {s!r}"


# --------------------------------------------------------------------------------------
# 補助検証（§9 段階 2 の合否判定ではない）
# --------------------------------------------------------------------------------------

def test_m4_beats_m3_when_dgp_has_jumps_and_leverage():
    """DGP がジャンプとレバレッジを含むとき M4 の QLIKE 平均が M3 を下回る。

    §9 段階 2 の DGP（AR(1) ボラのみ）では M4 が M3 に勝てない（ISSUE-202）。
    その原因が実装の欠陥ではなく DGP の過少規定であることを示すため、
    ``x4``（ジャンプ）・``x5``（レバレッジ）に対応する構造を実際に持つ DGP で
    優劣が反転することを固定する。**本テストは仕様の合否判定ではない。**
    """
    ticks, edges = make_sv_dataset(N_BARS, bar_sec=BAR_SEC, tick_sec=TICK_SEC, seed=303,
                                   phi=PHI, sd_ln_sigma=0.25, leverage=0.10,
                                   jump_prob=0.05, jump_size_sigma=6.0)
    res = compute_cvfe(ticks, edges, BAR_SEC, n_har=N_HAR)

    times, logp = validate_ticks(ticks)
    e = validate_edges(edges)
    params = CvfeParams(bar_interval_sec=BAR_SEC, n_har=N_HAR)
    quality = diagnose_quality(times, logp, e, params.n_har, params.freeze_thresh)
    measures = measure_all_bars(times, logp, e, quality, params)

    v = np.array([m.v for m in measures], dtype=np.float64)
    c = np.array([m.c for m in measures], dtype=np.float64)
    p_close = np.array([m.p_close for m in measures], dtype=np.float64)
    t0 = params.first_available_index

    assert int(np.array([m.jump_flag for m in measures]).sum()) >= 20, "ジャンプが検出されていない"

    proxy = np.where(res.available, v, np.nan)
    m4 = qlike(proxy, np.where(res.available, res.sigma_hat, np.nan))
    m3 = qlike(proxy, forecast_har_plain(c, p_close, t0, N_HAR))
    ok = np.isfinite(m4) & np.isfinite(m3)
    assert ok.sum() >= 500
    assert float(m4[ok].mean()) < float(m3[ok].mean()), (
        f"M4={float(m4[ok].mean())!r} >= M3={float(m3[ok].mean())!r}")
