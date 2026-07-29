"""評価手続きの検証（仕様 §5）。"""

import math
import sys
from pathlib import Path

import numpy as np
import pytest

_PKG_DIR = Path(__file__).resolve().parents[1]
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

from src.evaluation import (  # noqa: E402
    DM_ALPHA,
    MCS_ALPHA,
    MCS_BLOCK,
    diebold_mariano,
    model_confidence_set,
    mse_loss,
    newey_west_lag,
    qlike,
)


def test_qlike_matches_specification_formula():
    """QLIKE_t = V/σ̂² − ln(V/σ̂²) − 1（仕様 §5.1）。"""
    proxy = np.array([4.0, 1.0])
    sigma = np.array([1.0, 2.0])
    ratio = proxy / sigma ** 2
    np.testing.assert_allclose(qlike(proxy, sigma), ratio - np.log(ratio) - 1.0)


def test_qlike_is_zero_at_perfect_forecast_and_positive_otherwise():
    """完全予測で 0、それ以外で正（QLIKE は非負の乖離尺度）。"""
    assert qlike(np.array([4.0]), np.array([2.0]))[0] == pytest.approx(0.0)
    assert qlike(np.array([4.0]), np.array([1.0]))[0] > 0.0
    assert qlike(np.array([4.0]), np.array([4.0]))[0] > 0.0


def test_qlike_marks_invalid_inputs_as_nan():
    got = qlike(np.array([0.0, -1.0, 1.0]), np.array([1.0, 1.0, 0.0]))
    assert np.all(np.isnan(got))


def test_mse_matches_specification_formula():
    """MSE_t = (V − σ̂²)²（仕様 §5.1）。"""
    np.testing.assert_allclose(mse_loss(np.array([3.0]), np.array([2.0])), np.array([1.0]))


def test_newey_west_lag_uses_floor_rounding():
    """ラグ = floor(4·(T/100)^(2/9))（仕様 §5.2）。ceil ではない。"""
    for t_obs in (50, 100, 250, 500, 1000, 5000):
        assert newey_west_lag(t_obs) == int(math.floor(4.0 * (t_obs / 100.0) ** (2.0 / 9.0)))
    assert newey_west_lag(100) == 4
    # ceil 実装との差が実際に生じる T が存在することを固定する。
    assert newey_west_lag(250) != int(math.ceil(4.0 * (250 / 100.0) ** (2.0 / 9.0)))


def test_diebold_mariano_detects_a_clearly_better_model():
    """一方が明確に優れるとき統計量は負で 5% 有意（仕様 §5.2）。"""
    rng = np.random.default_rng(7)
    t_obs = 800
    better = np.abs(rng.standard_normal(t_obs)) * 0.10
    worse = better + 0.05                        # 常に損失が大きい
    res = diebold_mariano(better, worse)
    assert res.stat < 0.0
    assert res.p_value < DM_ALPHA
    assert res.n_obs == t_obs
    assert res.lag == newey_west_lag(t_obs)


def test_diebold_mariano_is_insignificant_for_identical_models():
    rng = np.random.default_rng(8)
    loss = np.abs(rng.standard_normal(500))
    res = diebold_mariano(loss, loss.copy())
    assert not np.isfinite(res.stat) or abs(res.stat) < 1e-8


def test_diebold_mariano_ignores_nonfinite_pairs():
    a = np.array([1.0, np.nan, 2.0, 3.0, 4.0, 5.0])
    b = np.array([2.0, 1.0, np.nan, 4.0, 5.0, 6.0])
    res = diebold_mariano(a, b)
    assert res.n_obs == 4


def test_mcs_retains_the_single_best_model_and_eliminates_the_worst():
    """明確に劣るモデルが除去され、最良モデルが生存する（仕様 §5.2）。"""
    rng = np.random.default_rng(9)
    t_obs = 600
    base = np.abs(rng.standard_normal(t_obs)) * 0.05
    losses = {
        "best": base,
        "mid": base + 0.20 + rng.standard_normal(t_obs) * 0.01,
        "worst": base + 0.60 + rng.standard_normal(t_obs) * 0.01,
    }
    res = model_confidence_set(losses, n_boot=400, seed=11)
    assert "best" in res.surviving
    assert "worst" in res.eliminated


def test_mcs_is_deterministic_for_a_fixed_seed():
    """乱数シード固定で同一結果（仕様 §5.2「乱数シード固定」）。"""
    rng = np.random.default_rng(10)
    losses = {k: np.abs(rng.standard_normal(300)) + i * 0.1 for i, k in enumerate("abc")}
    a = model_confidence_set(losses, n_boot=300, seed=5)
    b = model_confidence_set(losses, n_boot=300, seed=5)
    assert a == b


def test_mcs_keeps_all_models_when_they_are_equivalent():
    """優劣が付かない場合は全モデルが生存する。"""
    rng = np.random.default_rng(12)
    base = np.abs(rng.standard_normal(400))
    losses = {"a": base, "b": base + rng.standard_normal(400) * 1e-6,
              "c": base + rng.standard_normal(400) * 1e-6}
    res = model_confidence_set(losses, n_boot=300, seed=13)
    assert len(res.surviving) == 3


def test_mcs_defaults_match_specification():
    """α_MCS = 0.10・平均ブロック長 20（仕様 §5.2）。"""
    assert MCS_ALPHA == 0.10
    assert MCS_BLOCK == 20


def test_mcs_runs_at_specification_default_bootstrap_count():
    """既定 B = 10,000 で実行できること（仕様 §5.2）。

    テストが縮小した ``n_boot`` でしか通っていないと、既定値でのメモリ・所要時間の
    問題を見逃す。標本長は短めに取り、既定 B を実際に通す。
    """
    from src.evaluation import MCS_B

    rng = np.random.default_rng(21)
    t_obs = 200
    base = np.abs(rng.standard_normal(t_obs)) * 0.05
    losses = {"best": base,
              "mid": base + 0.30 + rng.standard_normal(t_obs) * 0.02,
              "worst": base + 0.80 + rng.standard_normal(t_obs) * 0.02}
    res = model_confidence_set(losses, n_boot=MCS_B, seed=17)
    assert "best" in res.surviving
    assert "worst" in res.eliminated
    assert len(res.p_values) >= 1


def test_mcs_returns_all_models_when_losses_are_identical():
    """全モデルの損失系列が同一のとき、例外を出さず全モデルを生存とする。

    偏差が厳密に 0 になりブートストラップ標準偏差も 0 となる退化ケース。
    素朴に実装すると ``np.nanargmax`` が全 NaN 配列で ``ValueError`` を送出する。
    仕様 §5.2 はこの条件を規定していないため、判別不能＝除去しないに倒す。
    """
    rng = np.random.default_rng(22)
    base = np.abs(rng.standard_normal(200))
    losses = {"a": base, "b": base.copy(), "c": base.copy()}
    res = model_confidence_set(losses, n_boot=500, seed=3)
    assert len(res.surviving) == 3 and res.eliminated == ()
