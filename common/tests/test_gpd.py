"""GPD 当てはめ・適合度検定・ForwardStop の検証（必須ゲート 2 の道具立て）。

道具を先に検証しておかないと、実データで「GPD が当てはまらない」と出たときに、それが現象
なのか実装バグなのか区別できない。既知パラメータの回復・既知の誤設定の検出・規則そのものの
定義一致を、それぞれ独立に固定する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from common.gpd import (  # noqa: E402
    anderson_darling,
    forward_stop,
    gpd_cdf,
    gpd_fit,
    gpd_gof_pvalue,
    gpd_neg_loglik,
    gpd_rvs,
    select_threshold,
)


# ---------------------------------------------------------------------------
# 分布の定義（手計算可能）
# ---------------------------------------------------------------------------

def test_cdf_matches_closed_form():
    y = np.array([0.0, 1.0, 2.0])
    got = gpd_cdf(y, xi=0.5, beta=2.0)
    expect = 1.0 - (1.0 + 0.5 * y / 2.0) ** (-1.0 / 0.5)
    np.testing.assert_allclose(got, expect)


def test_cdf_degenerates_to_exponential_when_xi_is_zero():
    y = np.array([0.5, 1.0, 3.0])
    np.testing.assert_allclose(gpd_cdf(y, xi=0.0, beta=2.0), 1.0 - np.exp(-y / 2.0))


def test_neg_loglik_is_infinite_outside_the_support():
    """ξ < 0 では台に上限がある。上限を超える観測があれば尤度 0（= −logL は inf）。"""
    y = np.array([0.5, 1.0, 100.0])
    assert np.isinf(gpd_neg_loglik(np.array([np.log(1.0), -0.5]), y))


# ---------------------------------------------------------------------------
# 既知パラメータの回復（推定量の妥当性）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("xi,beta", [(0.0, 1.0), (0.2, 2.0), (0.4, 0.5), (-0.15, 1.5)])
def test_fit_recovers_known_parameters(xi, beta):
    rng = np.random.default_rng(20260731 + int(abs(xi) * 100))
    y = gpd_rvs(20_000, xi, beta, rng=rng)

    fit = gpd_fit(y)

    assert fit.xi == pytest.approx(xi, abs=0.05), f"ξ̂={fit.xi!r} vs {xi}"
    assert fit.beta == pytest.approx(beta, rel=0.10), f"β̂={fit.beta!r} vs {beta}"


def test_fit_returns_nan_for_too_few_excesses():
    fit = gpd_fit(np.array([1.0, 2.0]))
    assert np.isnan(fit.xi) and np.isnan(fit.beta)


# ---------------------------------------------------------------------------
# 適合度: 正しいモデルは通し、誤ったモデルは落とす
# ---------------------------------------------------------------------------

def test_gof_pvalue_is_uniform_ish_when_data_are_gpd():
    """真に GPD なら p 値は棄却されない（α=0.05 で概ね通る）。"""
    rng = np.random.default_rng(5)
    rejects = 0
    trials = 20
    for _ in range(trials):
        y = gpd_rvs(300, 0.2, 1.0, rng=rng)
        p = gpd_gof_pvalue(y, n_boot=99, rng=rng)
        rejects += int(np.isfinite(p) and p < 0.05)
    assert rejects <= 4, f"正しいモデルで {rejects}/{trials} 回棄却＝第一種の過誤が過大"


def test_gof_pvalue_detects_a_clearly_wrong_shape():
    """対数正規（GPD でない）は棄却される。検出力があることの確認。"""
    rng = np.random.default_rng(9)
    y = np.exp(rng.standard_normal(400) * 1.2)
    p = gpd_gof_pvalue(y, n_boot=199, rng=rng)
    assert np.isfinite(p) and p < 0.05, f"誤ったモデルを棄却できていない（p={p!r}）"


def test_anderson_darling_is_smaller_for_the_correct_model():
    """誤設定でも**台が観測を覆う**モデル同士で比べる（A² は小さいほど良い当てはめ）。"""
    rng = np.random.default_rng(3)
    y = gpd_rvs(2000, 0.3, 1.0, rng=rng)
    good = anderson_darling(y, 0.3, 1.0)
    bad = anderson_darling(y, 0.0, 5.0)          # 指数・スケール過大（台は正の全域）
    assert good < bad


def test_anderson_darling_is_nan_when_data_fall_outside_the_support():
    """ξ < 0 は台に上限 β/|ξ| を持つ。それを超える観測があるモデルは「悪い」ではなく**不能**。

    A² を有限値で返すと、実現不可能なモデルが「当てはまりが悪いだけ」として比較に混ざる。
    nan にして選択候補から落とす。
    """
    rng = np.random.default_rng(3)
    y = gpd_rvs(2000, 0.3, 1.0, rng=rng)
    assert float(y.max()) > 5.0 / 0.4                    # 台の上限 12.5 を超える観測がある
    assert np.isnan(anderson_darling(y, -0.4, 5.0))


# ---------------------------------------------------------------------------
# ForwardStop 規則（定義との一致）
# ---------------------------------------------------------------------------

def test_forward_stop_matches_its_definition():
    p = np.array([0.001, 0.002, 0.5, 0.6])
    terms = -np.log1p(-p)
    means = np.cumsum(terms) / np.arange(1, p.size + 1)
    expect = int(np.flatnonzero(means <= 0.05)[-1] + 1)
    assert forward_stop(p, 0.05) == expect


def test_forward_stop_rejects_nothing_when_all_pvalues_are_large():
    assert forward_stop(np.array([0.5, 0.6, 0.7]), 0.05) == 0


def test_forward_stop_rejects_the_leading_run_of_tiny_pvalues():
    """低い閾値ほど当てはまらない（p 小）→ 先頭側が棄却され、その次が採択閾値になる。"""
    p = np.array([1e-6, 1e-6, 1e-6, 0.8, 0.9])
    assert forward_stop(p, 0.05) == 3


def test_forward_stop_handles_nan_as_non_rejection():
    """p 値が計算できない候補（超過不足など）は棄却扱いにしない（安全側）。"""
    assert forward_stop(np.array([np.nan, np.nan]), 0.05) == 0


# ---------------------------------------------------------------------------
# 選択手続き全体
# ---------------------------------------------------------------------------

def test_select_threshold_picks_the_first_threshold_that_fits():
    """低い 2 閾値は汚染で当てはまらず、3 番目以降は純 GPD、という構成で 3 番目を選ぶ。"""
    rng = np.random.default_rng(17)
    pure = gpd_rvs(600, 0.2, 1.0, rng=rng)
    contaminated = np.concatenate([pure[:300], np.full(300, 0.05)])   # 一様な塊で歪める
    candidates = [
        (0.0, contaminated),
        (0.1, contaminated),
        (0.2, pure),
        (0.3, gpd_rvs(500, 0.2, 1.0, rng=rng)),
    ]

    sel = select_threshold(candidates, alpha=0.05, n_boot=99, rng=rng)

    assert sel.index == 2, f"選ばれた index={sel.index}（p={sel.pvalues}）"
    assert sel.threshold == pytest.approx(0.2)


def test_select_threshold_returns_none_when_nothing_fits():
    rng = np.random.default_rng(23)
    bad = np.exp(rng.standard_normal(400) * 1.2)
    sel = select_threshold([(0.0, bad), (0.1, bad)], alpha=0.05, n_boot=99, rng=rng)
    assert sel.threshold is None
