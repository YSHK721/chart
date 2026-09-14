"""HAR-CJ-L の説明変数・推定・予測の検証（仕様 §4.5・§4.6）。"""

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
from src.dto import HAR_LAG_MONTH, HAR_LAG_WEEK, HAR_N_COEF  # noqa: E402
from src.errors import W04_HAR_JUMP_COLUMN_CONSTANT  # noqa: E402
from src.har import (  # noqa: E402
    C_FLOOR,
    har_feature_row,
    har_features,
    har_fit,
    har_predict,
    sigma_oc_from_log_variance,
)


def test_feature_row_matches_specification_formulas():
    """x1..x5 が仕様 §4.5-1 の 5 式そのものであること。"""
    rng = np.random.default_rng(0)
    c = np.abs(rng.standard_normal(HAR_LAG_MONTH)) * 1e-4 + 1e-5
    j, rho = 3.0e-5, -0.004
    got = har_feature_row(c, j, rho)

    assert got[0] == pytest.approx(np.log(c[-1]))
    assert got[1] == pytest.approx(np.log(c[-HAR_LAG_WEEK:].mean()))
    assert got[2] == pytest.approx(np.log(c.mean()))
    assert got[3] == pytest.approx(np.log(1.0 + j / c[-1]))
    assert got[4] == pytest.approx(min(rho, 0.0))


def test_leverage_term_is_zero_for_non_negative_return():
    """x5 = min(ρ_t, 0)：正の収益では 0（仕様 §4.5-1）。"""
    c = np.full(HAR_LAG_MONTH, 1e-4)
    assert har_feature_row(c, 0.0, +0.01)[4] == 0.0
    assert har_feature_row(c, 0.0, -0.01)[4] == pytest.approx(-0.01)


def test_c_is_clipped_at_1e_minus_16():
    """C_t < 1e-16 は 1e-16 にクリップする（仕様 §4.5-1）。"""
    c = np.full(HAR_LAG_MONTH, 1e-30)
    assert har_feature_row(c, 0.0, 0.0)[0] == pytest.approx(np.log(C_FLOOR))
    assert C_FLOOR == 1e-16


def test_features_are_nan_before_22_bars():
    """遡及 22 本に満たないバーの説明変数は nan（仕様 §4.5-1・§3.1 の N 制約の根拠）。"""
    c = np.full(40, 1e-4)
    j = np.zeros(40)
    pc = np.cumsum(np.full(40, 0.001))
    feats = har_features(c, j, pc)
    assert np.all(np.isnan(feats[:HAR_LAG_MONTH - 1]))
    assert np.all(np.isfinite(feats[HAR_LAG_MONTH - 1:]))


def test_bulk_features_agree_with_row_builder():
    """一括構成と 1 行構成が同一値（両経路が同じ関数を通ることの確認）。"""
    rng = np.random.default_rng(1)
    c = np.abs(rng.standard_normal(60)) * 1e-4 + 1e-6
    j = np.abs(rng.standard_normal(60)) * 1e-6
    pc = np.cumsum(rng.standard_normal(60) * 0.001)
    feats = har_features(c, j, pc)
    for t in (30, 45, 59):
        row = har_feature_row(c[t - HAR_LAG_MONTH + 1:t + 1], j[t], pc[t] - pc[t - 1])
        assert feats[t].tobytes() == row.tobytes()


def test_fit_recovers_known_coefficients():
    """ノイズなしの線形データで β を厳密に復元する。"""
    rng = np.random.default_rng(2)
    x = rng.standard_normal((400, 5))
    beta_true = np.array([0.5, -0.3, 0.2, 0.1, 0.4, -0.6])
    y = beta_true[0] + x @ beta_true[1:]
    beta, s2 = har_fit(x, y)
    np.testing.assert_allclose(beta, beta_true, atol=1e-10)
    assert s2 == pytest.approx(0.0, abs=1e-18)


def test_residual_variance_uses_dof_t_minus_6():
    """s² は自由度 T − 6 の不偏推定量（仕様 §4.5-5）。"""
    rng = np.random.default_rng(3)
    t_obs = 300
    x = rng.standard_normal((t_obs, 5))
    beta_true = np.array([0.1, 0.2, -0.1, 0.05, 0.3, -0.2])
    eps = rng.standard_normal(t_obs) * 0.5
    y = beta_true[0] + x @ beta_true[1:] + eps
    beta, s2 = har_fit(x, y)
    design = np.column_stack([np.ones(t_obs), x])
    ssr = float(((y - design @ beta) ** 2).sum())
    assert s2 == pytest.approx(ssr / (t_obs - HAR_N_COEF), rel=1e-12)


def test_constant_jump_column_is_dropped_with_warning():
    """x4 が定数のとき β4 = 0 に固定し W04 を出す（ISSUE-205）。"""
    rng = np.random.default_rng(4)
    x = rng.standard_normal((200, 5))
    x[:, 3] = 0.0                                  # ジャンプ未検出の学習窓
    y = 0.2 + x @ np.array([0.3, -0.2, 0.1, 0.0, 0.4])
    stream = io.StringIO()
    beta, _s2 = har_fit(x, y, logger=JsonlLogger(stream))

    assert beta.shape == (HAR_N_COEF,)
    assert beta[4] == 0.0
    records = [json.loads(line) for line in stream.getvalue().splitlines() if line]
    assert [r["code"] for r in records] == [W04_HAR_JUMP_COLUMN_CONSTANT]
    assert records[0]["level"] == "WARN"


def test_predict_is_linear_in_previous_features():
    """ŷ_t = β0 + Σ β_i x_i,{t−1}（仕様 §4.6）。"""
    beta = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    x_prev = np.array([0.1, 0.2, 0.3, 0.4, 0.5])
    assert har_predict(beta, x_prev) == pytest.approx(1.0 + float(np.dot(beta[1:], x_prev)))


def test_predict_returns_nan_for_nonfinite_features():
    beta = np.zeros(HAR_N_COEF)
    assert np.isnan(har_predict(beta, np.array([np.nan, 0.0, 0.0, 0.0, 0.0])))


def test_sigma_oc_applies_jensen_correction():
    """σ̂_OC = exp(ŷ/2 + s²/8)（仕様 §4.6）。"""
    assert sigma_oc_from_log_variance(-9.0, 0.184) == pytest.approx(np.exp(-9.0 / 2 + 0.184 / 8))
    # s² = 0 のときは補正なし。
    assert sigma_oc_from_log_variance(-9.0, 0.0) == pytest.approx(np.exp(-4.5))
