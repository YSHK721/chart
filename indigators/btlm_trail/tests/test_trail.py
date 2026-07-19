"""btlm_trail 計算層の検証（TDD）。

正本仕様: /root/.claude/plans/kind-twirling-hollerith.md ＋
実証知見: /workspaces/app/.doc/BTLM_TRACK_ANALYSIS_FINDINGS.md

検証観点:
    - 非リペイント（確定バー値の不変）
    - 参照実装 build_btlm_bands + OlsBtlmFitter との窓末尾値 数値一致（許容 1e-6）
    - 分位ペア検証（0<q_low<q_high<1）・複数ペア
    - 経験分位バンドの因果性（未来データ遮断）
    - 実現被覆率の算出
    - 8 択ソース解決
    - β（回帰傾き）・残差 σ の算出
"""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_PKG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PKG_DIR))

from src import (  # noqa: E402
    DEFAULT_EMP_N,
    DEFAULT_MAXBARS,
    DEFAULT_N_COV,
    build_btlm_trail,
    realized_coverage_latest,
    resolve_source,
    rolling_coverage,
)


def _load_tgp_reference():
    """参照実装 tgp_btlm/src を一意名でロードする（top-level ``src`` 名衝突を回避）。"""
    ref_dir = _PKG_DIR.parent / "tgp_btlm" / "src"
    name = "_tgp_ref_src"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, ref_dir / "__init__.py", submodule_search_locations=[str(ref_dir)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _price_series(n, seed=0):
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0, 1.0, n)
    return np.cumsum(steps) + 100.0


def _df(n=300, seed=0):
    prices = _price_series(n, seed)
    times = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {
            "time": times,
            "open": prices,
            "high": prices + 0.5,
            "low": prices - 0.5,
            "close": prices + 0.1,
        }
    )


# --- 参照実装との数値一致（窓末尾値） -------------------------------------
def test_ols_window_end_matches_reference_within_1e6():
    ref = _load_tgp_reference()
    df = _df(300, seed=1)
    maxbars = 100
    res = build_btlm_trail(
        df, source="close", maxbars=maxbars,
        q_low=0.05, q_high=0.95, band_method="ols",
    )
    # 各確定バー t で df[:t+1] に参照実装を当て、窓末尾値と一致するか（複数点抽出）。
    close = df["close"].to_numpy(dtype=float)
    for t in (5, 42, 120, 250, 299):
        bands = ref.build_btlm_bands(
            pd.DataFrame({"close": close[: t + 1]}),
            ref.OlsBtlmFitter(), price="close",
            maxbars=maxbars, q_low=0.05, q_high=0.95,
        )
        exp_mean = bands["btlm_mean"].to_numpy()[-1]
        exp_lo = bands["btlm_q5"].to_numpy()[-1]
        exp_hi = bands["btlm_q95"].to_numpy()[-1]
        assert res.mean[t] == pytest.approx(exp_mean, abs=1e-6)
        assert res.band_low[t] == pytest.approx(exp_lo, abs=1e-6)
        assert res.band_high[t] == pytest.approx(exp_hi, abs=1e-6)


def test_beta_matches_reference_slope():
    ref = _load_tgp_reference()
    df = _df(200, seed=2)
    res = build_btlm_trail(df, source="close", maxbars=100)
    close = df["close"].to_numpy(dtype=float)
    for t in (50, 150, 199):
        window = close[max(0, t - 99): t + 1]
        x, z = ref.make_design(window)
        phi = np.column_stack([np.ones(x.size), x])
        beta = np.linalg.inv(phi.T @ phi) @ phi.T @ z
        assert res.beta[t] == pytest.approx(beta[1], abs=1e-9)


# --- 非リペイント（確定バー値の不変） -------------------------------------
def test_non_repaint_confirmed_bars_unchanged_when_future_appended():
    df_full = _df(300, seed=3)
    df_prefix = df_full.iloc[:200].reset_index(drop=True)
    res_full = build_btlm_trail(df_full, source="close", maxbars=100)
    res_prefix = build_btlm_trail(df_prefix, source="close", maxbars=100)
    # 先頭 200 バーの確定値は、後続 100 バーを加えても不変。
    np.testing.assert_allclose(
        res_full.mean[:200], res_prefix.mean, rtol=0, atol=1e-9, equal_nan=True
    )
    np.testing.assert_allclose(
        res_full.beta[:200], res_prefix.beta, rtol=0, atol=1e-9, equal_nan=True
    )


def test_empirical_band_is_non_repaint_and_causal():
    df_full = _df(400, seed=4)
    df_prefix = df_full.iloc[:250].reset_index(drop=True)
    res_full = build_btlm_trail(
        df_full, source="close", maxbars=100,
        q_low=0.05, q_high=0.95, band_method="empirical", empirical_n=200,
    )
    res_prefix = build_btlm_trail(
        df_prefix, source="close", maxbars=100,
        q_low=0.05, q_high=0.95, band_method="empirical", empirical_n=200,
    )
    # 経験分位バンドも確定バーで不変（未来情報を使わない＝因果）。
    np.testing.assert_allclose(res_full.band_low[:250], res_prefix.band_low, atol=1e-9, equal_nan=True)
    np.testing.assert_allclose(res_full.band_high[:250], res_prefix.band_high, atol=1e-9, equal_nan=True)


# --- 分位ペア検証 ----------------------------------------------------------
def test_invalid_pair_raises():
    df = _df(120)
    for lo, hi in [(0.9, 0.1), (0.0, 0.95), (0.05, 1.0), (0.5, 0.5)]:
        with pytest.raises(ValueError):
            build_btlm_trail(df, q_low=lo, q_high=hi)


def test_band_low_below_mean_below_band_high():
    df = _df(200, seed=5)
    res = build_btlm_trail(df, source="close", maxbars=100, q_low=0.05, q_high=0.95)
    t = 150
    assert res.band_low[t] < res.mean[t] < res.band_high[t]


# --- 経験分位バンドの因果性（明示的な未来遮断） ---------------------------
def test_empirical_band_ignores_future_deviations():
    df = _df(300, seed=6)
    res = build_btlm_trail(
        df, source="close", maxbars=100,
        q_low=0.05, q_high=0.95, band_method="empirical", empirical_n=150,
    )
    # 未来のデータを差し替えても過去確定バーのバンドは不変。
    df2 = df.copy()
    df2.loc[260:, "close"] = df2.loc[260:, "close"] + 50.0
    res2 = build_btlm_trail(
        df2, source="close", maxbars=100,
        q_low=0.05, q_high=0.95, band_method="empirical", empirical_n=150,
    )
    np.testing.assert_allclose(res.band_low[:260], res2.band_low[:260], atol=1e-9, equal_nan=True)
    np.testing.assert_allclose(res.band_high[:260], res2.band_high[:260], atol=1e-9, equal_nan=True)


# --- 実現被覆率 ------------------------------------------------------------
def test_rolling_coverage_counts_close_inside_band():
    close = np.array([10.0, 11.0, 12.0, 13.0, 14.0])
    low = np.array([9.0, 12.0, 11.0, 12.5, 20.0])
    high = np.array([11.0, 13.0, 13.0, 14.0, 21.0])
    # inside: t0 yes, t1 no(11<12), t2 yes, t3 yes, t4 no(14<20)
    cov = rolling_coverage(close, low, high, n_cov=5)
    assert cov[4] == pytest.approx(3.0 / 5.0)


def test_realized_coverage_latest_uses_confirmed_bars():
    df = _df(400, seed=7)
    res = build_btlm_trail(df, source="close", maxbars=100, q_low=0.05, q_high=0.95)
    cov = realized_coverage_latest(
        df["close"].to_numpy(dtype=float), res.band_low, res.band_high, n_cov=250
    )
    assert 0.0 <= cov <= 1.0


# --- 8 択ソース解決 --------------------------------------------------------
def test_resolve_source_eight_choices():
    df = _df(20, seed=8)
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    lo = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    np.testing.assert_allclose(resolve_source(df, "close"), c)
    np.testing.assert_allclose(resolve_source(df, "open"), o)
    np.testing.assert_allclose(resolve_source(df, "high"), h)
    np.testing.assert_allclose(resolve_source(df, "low"), lo)
    np.testing.assert_allclose(resolve_source(df, "hl2"), (h + lo) / 2)
    np.testing.assert_allclose(resolve_source(df, "hlc3"), (h + lo + c) / 3)
    np.testing.assert_allclose(resolve_source(df, "ohlc4"), (o + h + lo + c) / 4)
    np.testing.assert_allclose(resolve_source(df, "hlcc4"), (h + lo + 2 * c) / 4)


def test_resolve_source_unknown_raises():
    df = _df(20)
    with pytest.raises(ValueError):
        resolve_source(df, "vwap")


def test_source_selection_changes_regression_target():
    df = _df(200, seed=9)
    res_close = build_btlm_trail(df, source="close", maxbars=100)
    res_high = build_btlm_trail(df, source="high", maxbars=100)
    # 異なるソースは異なる回帰当てはめ＝末尾値が異なる。
    assert res_close.mean[150] != res_high.mean[150]


# --- 残差 σ ----------------------------------------------------------------
def test_sigma_is_nonnegative_and_finite_in_window():
    df = _df(200, seed=10)
    res = build_btlm_trail(df, source="close", maxbars=100)
    finite = np.isfinite(res.sigma)
    assert finite[-100:].all()
    assert (res.sigma[finite] >= 0).all()


# --- 既定値 ----------------------------------------------------------------
def test_defaults():
    assert DEFAULT_MAXBARS == 100
    assert DEFAULT_EMP_N == 500
    assert DEFAULT_N_COV == 250


def test_warmup_bars_below_three_are_nan():
    df = _df(100, seed=11)
    res = build_btlm_trail(df, source="close", maxbars=100)
    # 窓 < 3 本（先頭 2 バー）は分散推定不能で NaN。
    assert np.isnan(res.mean[0])
    assert np.isnan(res.mean[1])
    assert np.isfinite(res.mean[2])
