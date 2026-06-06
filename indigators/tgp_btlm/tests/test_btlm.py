"""BTLM 計算層の検証。

元 MQL4 ロジック（窓=直近 maxbars、X=1..n、Open 回帰、窓外 NaN、分位点の符号）と
成果物列の整合を、手計算可能な入力と Fake/参照 Fitter で固定する。
import 規約: sys.path.insert(parents[1]) → from src import ...（ガイド §7）。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    BtlmResult,
    OlsBtlmFitter,
    build_btlm_bands,
    make_design,
    mean_column,
    norm_ppf,
    quantile_column,
)
from src.core import BtlmFitter  # noqa: E402


class FakeFitter:
    """fit_predict の入力を記録し、決定論的な結果を返す Fake。"""

    def __init__(self):
        self.calls = []

    def fit_predict(self, x, z, *, q_low=0.05, q_high=0.95):
        self.calls.append({"x": np.asarray(x), "z": np.asarray(z),
                           "q_low": q_low, "q_high": q_high})
        mean = np.asarray(z, dtype=float)
        return BtlmResult(mean=mean, q_low=mean - 1.0, q_high=mean + 1.0)


def _df(n=120, price="open"):
    return pd.DataFrame({price: np.arange(n, dtype=float) + 10.0})


def test_fake_is_fitter_protocol():
    assert isinstance(FakeFitter(), BtlmFitter)


def test_window_is_last_maxbars_and_design():
    df = _df(120)
    f = FakeFitter()
    build_btlm_bands(df, f, maxbars=100)
    call = f.calls[0]
    # X は 1..100、Z は直近 100 本の open（昇順）。
    assert call["x"].tolist() == list(range(1, 101))
    np.testing.assert_array_equal(call["z"], df["open"].to_numpy()[-100:])


def test_outside_window_is_nan():
    df = _df(120)
    bands = build_btlm_bands(df, FakeFitter(), maxbars=100)
    assert bands.shape == (120, 3)
    # 窓外（先頭 20 本）は NaN、窓内（末尾 100 本）は有限。
    assert np.isnan(bands[mean_column()].to_numpy()[:20]).all()
    assert np.isfinite(bands[mean_column()].to_numpy()[-100:]).all()


def test_column_names_reflect_quantiles():
    df = _df(50)
    bands = build_btlm_bands(df, FakeFitter(), maxbars=100, q_low=0.10, q_high=0.90)
    assert mean_column() == "btlm_mean"
    assert quantile_column(0.10) == "btlm_q10"
    assert set(bands.columns) == {"btlm_mean", "btlm_q10", "btlm_q90"}


def test_band_sign_and_values():
    df = _df(10)
    bands = build_btlm_bands(df, FakeFitter(), maxbars=100)
    mean = bands[mean_column()].to_numpy()
    lo = bands[quantile_column(0.05)].to_numpy()
    hi = bands[quantile_column(0.95)].to_numpy()
    # Fake: lo = mean-1, hi = mean+1。
    np.testing.assert_allclose(lo[-10:], mean[-10:] - 1.0)
    np.testing.assert_allclose(hi[-10:], mean[-10:] + 1.0)
    assert (hi[-10:] > mean[-10:]).all() and (lo[-10:] < mean[-10:]).all()


def test_maxbars_larger_than_data():
    df = _df(30)
    f = FakeFitter()
    bands = build_btlm_bands(df, f, maxbars=100)
    # データ 30 本 < maxbars 100 → 窓=30、全行有限。
    assert f.calls[0]["x"].size == 30
    assert np.isfinite(bands[mean_column()].to_numpy()).all()


def test_price_column_selection_case_insensitive():
    df = pd.DataFrame({"OPEN": np.arange(40.0), "Close": np.arange(40.0) + 1})
    f = FakeFitter()
    build_btlm_bands(df, f, price="open", maxbars=10)
    np.testing.assert_array_equal(f.calls[0]["z"], df["OPEN"].to_numpy()[-10:])


def test_missing_price_column_raises():
    df = pd.DataFrame({"close": np.arange(40.0)})
    with pytest.raises(KeyError):
        build_btlm_bands(df, FakeFitter(), price="open")


def test_invalid_quantiles_raise():
    df = _df(40)
    with pytest.raises(ValueError):
        build_btlm_bands(df, FakeFitter(), q_low=0.9, q_high=0.1)
    with pytest.raises(ValueError):
        build_btlm_bands(df, FakeFitter(), q_low=0.0, q_high=0.95)


def test_make_design_empty_raises():
    with pytest.raises(ValueError):
        make_design(np.array([]))


def test_btlm_result_is_immutable_and_length_checked():
    r = BtlmResult(mean=[1.0, 2.0], q_low=[0.0, 1.0], q_high=[2.0, 3.0])
    with pytest.raises(ValueError):
        BtlmResult(mean=[1.0, 2.0], q_low=[0.0], q_high=[2.0, 3.0])
    with pytest.raises(ValueError):
        r.mean[0] = 99.0  # read-only


def test_norm_ppf_known_values():
    # 標準正規の代表分位点。
    assert norm_ppf(0.5) == pytest.approx(0.0, abs=1e-9)
    assert norm_ppf(0.975) == pytest.approx(1.959963985, abs=1e-6)
    assert norm_ppf(0.95) == pytest.approx(1.644853627, abs=1e-6)
    # 対称性。
    assert norm_ppf(0.025) == pytest.approx(-1.959963985, abs=1e-6)
    with pytest.raises(ValueError):
        norm_ppf(0.0)


def test_ols_reference_fitter_band_order():
    # 参照実装（R 不要）: 単調増加データに対し平均は単調、帯は平均を挟む。
    rng = np.random.default_rng(0)
    z = np.linspace(1.10, 1.12, 60) + rng.normal(0, 0.0005, 60)
    x, zz = make_design(z)
    res = OlsBtlmFitter().fit_predict(x, zz, q_low=0.05, q_high=0.95)
    assert (res.q_high > res.mean).all()
    assert (res.q_low < res.mean).all()
    assert res.mean.size == 60
