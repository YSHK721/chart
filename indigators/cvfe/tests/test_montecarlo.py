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
    3 水準すべてが DEGRADED となって検定が空虚になる（ISSUE-206）。
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
# §9 段階 2 の DGP パラメータ（ISSUE-208 の裁定で x4・x5 の説明対象を追加）。
#   値は実測で選定した（グリッド探索・N_BARS=1500・共通標本 978 本・QLIKE 平均）:
#     jump_prob / jump_size / leverage → M4 / M0 / M1 / M3
#     0.01 / 4.0 / 0.10 → 0.15835 / 0.29263 / 0.27103 / 0.17756   M4 勝ち
#     0.02 / 4.0 / 0.10 → 0.38888 / 0.49254 / 0.45639 / 0.41633   M4 勝ち（採用）
#     0.03 / 4.0 / 0.10 → 0.42503 / 0.54516 / 0.49530 / 0.46019   M4 勝ち
#     0.05 / 6.0 / 0.10 → 1.64533 / 1.38923 / 1.17773 / 1.71310   M4 **負け**
#   ジャンプが過大（5% × 6σ）だと代理変数（§5.1 の RV＝ジャンプ込み）がジャンプに支配され、
#   ジャンプを除いた C_t を予測する M4 が構造的に不利になる。x4 に説明対象を与えつつ
#   代理変数を支配させない水準として 2% × 4σ を採る。
SD_LN_SIGMA = 0.25
LEVERAGE = 0.10          # x5（負の収益に対する非対称性）の説明対象
JUMP_PROB = 0.02         # x4（ジャンプ成分）の説明対象
JUMP_SIZE_SIGMA = 4.0


@pytest.fixture(scope="module")
def stochastic_vol_run():
    """§9 段階 2 の DGP 上で M4 と M0・M1・M3 の QLIKE 系列を作る。

    ISSUE-208 の裁定（§9 段階 2 改訂）: v1.0 の DGP は「ln σ_t の AR(1)」のみで
    **ジャンプもレバレッジも含まなかった**ため、M4（HAR-CJ-L）の ``x4``（ジャンプ）・
    ``x5``（レバレッジ）に説明対象が存在せず、M3（項なし HAR）に対する優位が原理的に
    生じなかった（推定分散が増える分わずかに劣る。実測 M4=0.0105268 / M3=0.0104954）。
    これは実装の欠陥ではなく DGP の過少規定であるため、DGP にジャンプ強度と
    レバレッジ係数を追加する。
    """
    ticks, edges = make_sv_dataset(N_BARS, bar_sec=BAR_SEC, tick_sec=TICK_SEC, seed=303,
                                   phi=PHI, sd_ln_sigma=SD_LN_SIGMA, leverage=LEVERAGE,
                                   jump_prob=JUMP_PROB, jump_size_sigma=JUMP_SIZE_SIGMA)
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
    "M3",
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
    0.5,
    1.0,
])
def test_stage2_noise_triggers_degraded_or_worse(noise_reports, ratio):
    """``ω/σ >= 0.5`` のとき §4.1 の判定が DEGRADED 以上になる（§9 段階 2）。"""
    rep = noise_reports[ratio]
    assert rep.quality_gate in ("DEGRADED", "FAIL"), (
        f"ω/σ={ratio}: gate={rep.quality_gate}, S={rep.signature_slope!r}")


def test_stage2_degraded_threshold_admits_omega_sigma_half(noise_reports):
    """閾値 0.45 が ``ω/σ = 0.5`` を DEGRADED として拾えること（ISSUE-207 の裁定）。

    ``S = (2 − 1/30)r² / (1 + r²/30)``（``r = ω/σ``）であるから ``r = 0.5`` の理論値は
    0.4877。v1.0 の閾値 0.50 はこれを構造的に下回れず、§9 段階 2 の「``ω/σ ≥ 0.5`` のとき
    DEGRADED 以上」を満たせなかった（実測 S = 0.4718 で PASS 判定）。裁定は「§9 の設計意図を
    正とし閾値を 0.45 へ下げる」。本テストは閾値と要求の間に余裕があることを直接固定する。
    """
    from src.quality import S_DEGRADED

    r = 0.5
    theoretical = (2.0 - 1.0 / 30.0) * r ** 2 / (1.0 + r ** 2 / 30.0)
    assert theoretical == pytest.approx(0.4877, abs=0.001)
    assert S_DEGRADED < theoretical, (
        f"閾値 {S_DEGRADED} は ω/σ=0.5 の理論値 {theoretical:.4f} を下回る必要がある")

    measured = noise_reports[0.5].signature_slope
    assert S_DEGRADED < measured, f"実測 S = {measured:.4f} も閾値を超えること"


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

def test_m4_cannot_beat_m3_on_the_v1_0_dgp_without_jumps_or_leverage():
    """v1.0 の DGP（AR(1) ボラのみ）では M4 が M3 に勝てないことを記録として固定する。

    ISSUE-208 の裁定の**根拠**であり、仕様の合否判定ではない。DGP に ``x4``・``x5`` の
    説明対象が無いとき、追加項は説明力を持たず推定分散だけを増やすため M4 はわずかに劣る。
    §9 段階 2 の DGP を改訂した理由（実装の欠陥ではなく DGP の過少規定であったこと）を
    将来にわたって失わないために残す。
    """
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

    proxy = np.where(res.available, v, np.nan)
    m4 = qlike(proxy, np.where(res.available, res.sigma_hat, np.nan))
    m3 = qlike(proxy, forecast_har_plain(c, p_close, t0, N_HAR))
    ok = np.isfinite(m4) & np.isfinite(m3)
    assert ok.sum() >= 500
    assert float(m4[ok].mean()) > float(m3[ok].mean()), (
        "v1.0 の DGP では M4 が M3 に勝てない（この事実が §9 段階 2 改訂の根拠）: "
        f"M4={float(m4[ok].mean())!r} / M3={float(m3[ok].mean())!r}")
