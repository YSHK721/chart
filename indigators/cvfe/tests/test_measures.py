"""CVFE 仕様 §4.3 ボラティリティ測定量の検証。

正本仕様: indigators/cvfe/CVFE_spec_v1.0.md §4.3・§9 段階 1

検証観点:
    - RV / RRANGE / PARK が仕様の式そのものであること（決定論的な同値検証）
    - 前値補間カレンダーサンプリング（§4.1-2）の定義一致
    - σ = 1 の合成 GBM（バー内 1440 ステップ・100,000 バー・シード固定）に対する
      RV の平均が 1.000 ± 0.005、TSRV の平均が 1.000 ± 0.010（§9 段階 1）
    - TSRV ≤ 0 のとき RV^avg で代替し WARN を残す（§4.3）
"""

import io
import json
import sys
from pathlib import Path

import numpy as np
import pytest

_PKG_DIR = Path(__file__).resolve().parents[1]
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

from src import JsonlLogger  # noqa: E402
from src.measures import (  # noqa: E402
    RRANGE_SUBINTERVALS,
    parkinson,
    realized_range,
    realized_variance,
    two_scale_rv,
)
from src.sampling import previous_tick_sample  # noqa: E402

_LN2_4 = 4.0 * np.log(2.0)


def test_realized_variance_is_sum_of_squared_log_returns():
    """RV_t = Σ r_i²（§4.3 "RV"）。"""
    logp = np.array([0.0, 0.1, 0.05, 0.2, 0.19])
    r = np.diff(logp)
    assert realized_variance(logp) == pytest.approx(float((r ** 2).sum()), rel=0, abs=0)


def test_parkinson_matches_specification_formula():
    """PK_t = (ln H − ln L)² / (4 ln 2)（§4.3 "PARK"）。"""
    p_high, p_low = 0.37, -0.11
    assert parkinson(p_high, p_low) == pytest.approx((p_high - p_low) ** 2 / _LN2_4)


def test_realized_range_splits_bar_into_twelve_equal_subintervals():
    """RR_t = (1/(4 ln 2)) Σ_j (max_j p − min_j p)²、m = 12（§4.3 "RRANGE"）。"""
    assert RRANGE_SUBINTERVALS == 12
    t0, t1 = 0.0, 1200.0                       # 12 等分 → 各 100 秒
    times = np.arange(0.0, 1200.0, 10.0)       # 各サブ区間に 10 点
    logp = np.zeros(times.size)
    # サブ区間 j の中で 0 → j の振幅を作る（j 番目のレンジ = j）。
    for j in range(12):
        sel = (times >= t0 + j * 100.0) & (times < t0 + (j + 1) * 100.0)
        logp[sel] = np.linspace(0.0, float(j), int(sel.sum()))
    expect = sum(float(j) ** 2 for j in range(12)) / _LN2_4
    assert realized_range(times, logp, t0, t1, m=12) == pytest.approx(expect)


def test_previous_tick_sampling_holds_last_observed_price():
    """カレンダー時間 Δ の格子で直前ティック値を保持する（§4.1-2 前値補間）。"""
    times = np.array([0.0, 7.0, 9.0, 26.0])
    logp = np.array([1.0, 2.0, 3.0, 4.0])
    got = previous_tick_sample(times, logp, 0.0, 30.0, 10.0)
    # 格子 = 0, 10, 20, 30 → 直前ティックは 1.0（t=0）, 3.0（t=9）, 3.0（t=9）, 4.0（t=26）
    np.testing.assert_array_equal(got, np.array([1.0, 3.0, 3.0, 4.0]))


def test_two_scale_rv_matches_specification_formula():
    """TSRV の閉形式が §4.3 の定義（端点欠損補正を含む）と一致すること（決定論的照合）。"""
    rng = np.random.default_rng(3)
    logp = np.concatenate([[0.0], np.cumsum(rng.standard_normal(400) * 0.001)])
    n = logp.size - 1
    K = int(np.ceil(n ** (2.0 / 3.0)))

    rv_sub = 0.0
    for k in range(K):                        # k 番目のサブグリッド（仕様どおり素直に）
        sub = logp[k::K]
        rv_sub += float((np.diff(sub) ** 2).sum())
    rv_avg = rv_sub / K
    n_bar = (n - K + 1) / K
    rv_all = float((np.diff(logp) ** 2).sum())
    # 端点欠損補正 n/(n−K+1)（ISSUE-204 の裁定で §4.3 へ追加）。
    expect = (rv_avg - (n_bar / n) * rv_all) / (1.0 - n_bar / n) * (n / (n - K + 1))

    assert two_scale_rv(logp) == pytest.approx(expect, rel=1e-12)


def test_two_scale_rv_edge_correction_is_actually_applied():
    """補正係数 ``n/(n−K+1)`` が実際に乗じられていることを独立に固定する（ISSUE-204）。

    上の同値検証は期待値側にも同じ式を書くため、「実装と期待値の双方から補正を落とす」
    変異を検出できない。ここでは補正の**向きと大きさ**を式と独立に判定する。
    """
    rng = np.random.default_rng(5)
    logp = np.concatenate([[0.0], np.cumsum(rng.standard_normal(400) * 0.001)])
    n = logp.size - 1
    K = int(np.ceil(n ** (2.0 / 3.0)))
    factor = n / (n - K + 1)
    assert factor > 1.0, "補正係数は 1 より大きい（端点欠損を埋める向き）"

    # 補正前の v1.0 形を素直に組み立てて比較する（実装の式を再利用しない）。
    rv_avg = sum(float((np.diff(logp[k::K]) ** 2).sum()) for k in range(K)) / K
    n_bar = (n - K + 1) / K
    rv_all = float((np.diff(logp) ** 2).sum())
    v1_form = (rv_avg - (n_bar / n) * rv_all) / (1.0 - n_bar / n)

    got = two_scale_rv(logp)
    assert got > v1_form, "補正後は補正前より大きい（過小バイアスを埋める）"
    assert got / v1_form == pytest.approx(factor, rel=1e-12)


def test_two_scale_rv_falls_back_to_rv_avg_and_logs_when_nonpositive():
    """TSRV ≤ 0 のとき RV^avg で代替し WARN を出す（§4.3）。"""
    from src.errors import W01_TSRV_NONPOSITIVE

    # 交互に振動する系列は全体 RV が大きく サブグリッド RV が小さい → TSRV < 0 になる。
    n = 200
    logp = np.zeros(n + 1)
    logp[1::2] = 1e-3
    stream = io.StringIO()
    got = two_scale_rv(logp, logger=JsonlLogger(stream), bar_index=42)

    K = int(np.ceil(n ** (2.0 / 3.0)))
    rv_avg = sum(float((np.diff(logp[k::K]) ** 2).sum()) for k in range(K)) / K
    assert got == pytest.approx(rv_avg, rel=1e-12)

    records = [json.loads(line) for line in stream.getvalue().splitlines() if line]
    warn = [r for r in records if r["code"] == W01_TSRV_NONPOSITIVE]
    assert len(warn) == 1 and warn[0]["level"] == "WARN" and warn[0]["bar_index"] == 42


# --------------------------------------------------------------------------------------
# §9 段階 1：合成 GBM 上の不偏性（仕様が定める本数・許容差をそのまま用いる）
# --------------------------------------------------------------------------------------

_STAGE1_BARS = 100_000
_STAGE1_STEPS = 1_440
_STAGE1_SEED = 11
_CHUNK = 5_000


def _stage1_means():
    """σ = 1 の GBM（バー内 1440 ステップ・100,000 バー）で RV と TSRV の平均を返す。"""
    rng = np.random.default_rng(_STAGE1_SEED)
    rv_sum = 0.0
    ts_sum = 0.0
    sd = 1.0 / np.sqrt(_STAGE1_STEPS)
    for _ in range(_STAGE1_BARS // _CHUNK):
        incr = rng.standard_normal((_CHUNK, _STAGE1_STEPS)) * sd
        paths = np.concatenate([np.zeros((_CHUNK, 1)), np.cumsum(incr, axis=1)], axis=1)
        for row in paths:
            rv_sum += realized_variance(row)
            ts_sum += two_scale_rv(row)
    return rv_sum / _STAGE1_BARS, ts_sum / _STAGE1_BARS


@pytest.fixture(scope="module")
def stage1_means():
    return _stage1_means()


def test_stage1_rv_is_unbiased_on_synthetic_gbm(stage1_means):
    """RV の平均が 1.000 ± 0.005（§9 段階 1）。"""
    rv_mean, _ = stage1_means
    assert rv_mean == pytest.approx(1.0, abs=0.005), f"RV mean = {rv_mean!r}"


def test_stage1_tsrv_is_unbiased_on_synthetic_gbm(stage1_means):
    """TSRV の平均が 1.000 ± 0.010（§9 段階 1）。

    ISSUE-204 の裁定（TBD-1 解決）で §4.3 に端点欠損補正 ``n/(n−K+1)`` を追加した結果、
    v1.0 の 0.9112（不合格）から **0.9993**（合格）へ是正された。
    """
    _, ts_mean = stage1_means
    assert ts_mean == pytest.approx(1.0, abs=0.010), f"TSRV mean = {ts_mean!r}"


def test_stage1_tsrv_bias_without_edge_correction_matches_theory(stage1_means):
    """補正を外すと端点欠損の理論値に一致することを固定する（原因の恒久的な記録）。

    K 個のサブグリッドが覆う増分は全 ``n`` 本のうち ``n − K + 1`` 本にとどまるため
    ``E[RV^avg] = ((n − K + 1)/n)·σ²`` となる。仕様 §4.3 の補正係数 ``(1 − n̄/n)^(-1)`` は
    ノイズ項のみを補正し、この端点欠損（``O(K/n) = n^(-1/3)``）を補正しない。したがって
    補正を外した推定量の期待値は

        ``( (n−K+1)/n − n̄/n ) / (1 − n̄/n)``

    となる。ISSUE-204 の裁定はこの欠損を ``n/(n−K+1)`` で埋めるものであり、本テストは
    「補正が何を埋めているか」を将来にわたって失わないために残す。
    """
    _, ts_mean = stage1_means
    n = _STAGE1_STEPS
    k = int(np.ceil(n ** (2.0 / 3.0)))
    n_bar = (n - k + 1) / k
    predicted_uncorrected = ((n - k + 1) / n - n_bar / n) / (1.0 - n_bar / n)
    uncorrected = ts_mean / (n / (n - k + 1))       # 実測値から補正を外す
    assert uncorrected == pytest.approx(predicted_uncorrected, abs=0.002), (
        f"補正を外した TSRV mean = {uncorrected!r}, 端点欠損理論値 = {predicted_uncorrected!r}")
