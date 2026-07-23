"""btlm_trail_marod core（marod_series）の単体テスト（TDD）。

MAROD = (source - mean) / mean * 100。source/mean は btlm_trail core を参照実装として
そのまま再利用する（importlib 動的ロード）。因果・非リペイントは btlm_trail core の
機構により成立する。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_PKG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PKG_DIR))

from src import (  # noqa: E402
    DEFAULT_MAXBARS,
    SIGMA_MULT,
    marod_quantile_bands,
    marod_series,
    marod_sigma_band,
)
from src.core import _load_btlm_trail  # noqa: E402
from src.core import _MIN_STAT_OBS  # noqa: E402

# 参照実装（btlm_trail core）を「テスト側でも独立に」動的ロードし、期待値を組む。
#   marod_series の内部実装と同一機構（importlib）だが、期待値算出は完全に独立させる。
_BTLM_TRAIL_SRC = Path(__file__).resolve().parents[2] / "btlm_trail" / "src"


def _ref_btlm_trail():
    spec = importlib.util.spec_from_file_location(
        "_btlm_trail_src_ref_expected",
        _BTLM_TRAIL_SRC / "__init__.py",
        submodule_search_locations=[str(_BTLM_TRAIL_SRC)],
    )
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules["_btlm_trail_src_ref_expected"] = module
    spec.loader.exec_module(module)
    return module


def _ohlc(n: int = 200, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    prices = np.cumsum(rng.normal(0, 1, n)) + 100.0
    return pd.DataFrame({
        "time": pd.date_range("2020-01-01", periods=n, freq="D"),
        "open": prices, "high": prices + 1.0, "low": prices - 1.0, "close": prices + 0.25,
    })


def _expected_marod(df, source="close", maxbars=DEFAULT_MAXBARS):
    bt = _ref_btlm_trail()
    prices = np.asarray(bt.resolve_source(df, source), dtype=np.float64).ravel()
    mean = np.asarray(bt.rolling_ols_window_end(prices, maxbars)[0], dtype=np.float64).ravel()
    with np.errstate(divide="ignore", invalid="ignore"):
        marod = (prices - mean) / mean * 100.0
    return np.where(np.isfinite(marod), marod, np.nan)


def test_load_btlm_trail_exposes_reference_functions():
    # 参照機構（importlib 動的ロード）が btlm_trail core を無改変参照できることの実証。
    bt = _load_btlm_trail()
    assert callable(bt.resolve_source)
    assert callable(bt.rolling_ols_window_end)


def test_marod_matches_definition_and_reuses_btlm_mean():
    # MAROD == (source - btlm_mean)/btlm_mean*100。mean が btlm_trail core と定義上一致する。
    df = _ohlc(200)
    got = marod_series(df, source="close", maxbars=100)
    exp = _expected_marod(df, "close", 100)
    np.testing.assert_allclose(got, exp, rtol=1e-12, atol=1e-12, equal_nan=True)


def test_marod_matches_definition_synthetic_source():
    # 合成ソース（hl2）でも定義式一致（8 択ソースを btlm_trail core の resolve_source に委譲）。
    df = _ohlc(150, seed=3)
    got = marod_series(df, source="hl2", maxbars=60)
    exp = _expected_marod(df, "hl2", 60)
    np.testing.assert_allclose(got, exp, rtol=1e-12, atol=1e-12, equal_nan=True)


def test_marod_warmup_is_nan():
    # 窓 < 3 本（先頭 2 バー）は mean が NaN のため MAROD も NaN。
    df = _ohlc(50)
    got = marod_series(df, source="close", maxbars=100)
    assert np.isnan(got[0])
    assert np.isnan(got[1])
    assert np.isfinite(got[2])  # 3 本目で窓 = 3 → 有限


def test_marod_no_inf_only_nan_for_undefined():
    # 0 除算・未定義は NaN に落ち、inf は残さない（描画除外の前提）。
    df = _ohlc(120, seed=7)
    got = marod_series(df, source="close", maxbars=40)
    assert not np.isinf(got).any()


def test_marod_non_repaint_past_bars_invariant():
    # 非リペイント: df[:k] で計算した過去バーの MAROD が df 全体の同区間と一致する。
    df = _ohlc(200, seed=11)
    k = 120
    full = marod_series(df, source="close", maxbars=100)
    partial = marod_series(df.iloc[:k].reset_index(drop=True), source="close", maxbars=100)
    np.testing.assert_allclose(partial, full[:k], rtol=1e-12, atol=1e-12, equal_nan=True)


def test_marod_maxbars_below_min_raises():
    # maxbars < 3 は btlm_trail core（分散推定）の契約に従い ValueError を伝播する。
    df = _ohlc(20)
    with pytest.raises(ValueError):
        marod_series(df, source="close", maxbars=2)


# --- ローリング σ / 分位バンド（新規・因果ローリング）------------------------------

def _expected_causal(marod, window_n, reducer):
    """当該バー除外・直近 window_n 本・有限 >= 2 で reducer を独立に再計算した期待値。"""
    m = np.asarray(marod, dtype=np.float64)
    n = m.size
    out = np.full(n, np.nan)
    for t in range(n):
        window = m[max(0, t - window_n): t]
        finite = window[np.isfinite(window)]
        if finite.size >= 2:
            out[t] = float(reducer(finite))
    return out


def test_marod_quantile_bands_match_causal_definition():
    # 分位バンド == 当該バー除外・直近 N 本の経験分位（因果・独立再計算と一致）。
    df = _ohlc(400, seed=5)
    m = marod_series(df, source="close", maxbars=100)
    lo, hi = marod_quantile_bands(m, window_n=200, q_low=0.05, q_high=0.95)
    exp_lo = _expected_causal(m, 200, lambda f: np.quantile(f, 0.05))
    exp_hi = _expected_causal(m, 200, lambda f: np.quantile(f, 0.95))
    np.testing.assert_allclose(lo, exp_lo, rtol=1e-12, atol=1e-12, equal_nan=True)
    np.testing.assert_allclose(hi, exp_hi, rtol=1e-12, atol=1e-12, equal_nan=True)


def test_marod_sigma_band_is_rolling_mean_plus_minus_mult_std():
    # σ バンド == ローリング平均 ± SIGMA_MULT·σ（ddof=1・当該バー除外・因果）。
    df = _ohlc(400, seed=9)
    m = marod_series(df, source="close", maxbars=100)
    lo, hi, mean, std = marod_sigma_band(m, window_n=150)
    exp_mean = _expected_causal(m, 150, lambda f: f.mean())
    exp_std = _expected_causal(m, 150, lambda f: f.std(ddof=1))
    np.testing.assert_allclose(mean, exp_mean, rtol=1e-12, atol=1e-12, equal_nan=True)
    np.testing.assert_allclose(std, exp_std, rtol=1e-12, atol=1e-12, equal_nan=True)
    np.testing.assert_allclose(hi, exp_mean + SIGMA_MULT * exp_std, rtol=1e-12, atol=1e-12, equal_nan=True)
    np.testing.assert_allclose(lo, exp_mean - SIGMA_MULT * exp_std, rtol=1e-12, atol=1e-12, equal_nan=True)


def test_marod_bands_warmup_and_min_obs_are_nan():
    # 有限 MAROD が _MIN_STAT_OBS(=2) 本そろう前のバーは σ・分位とも NaN。
    df = _ohlc(60)
    m = marod_series(df, source="close", maxbars=100)  # 先頭 2 本 NaN（marod warm-up）。
    lo, hi = marod_quantile_bands(m, window_n=500)
    slo, shi, _, _ = marod_sigma_band(m, window_n=500)
    # 先頭バー（当該バー除外＝空窓）は必ず NaN。t=0,1,2 は有限 MAROD が 2 本未満。
    for arr in (lo, hi, slo, shi):
        assert np.isnan(arr[0])
        assert np.isnan(arr[1])
        assert np.isnan(arr[2])
    assert _MIN_STAT_OBS == 2


def test_marod_bands_non_repaint_past_bars_invariant():
    # 非リペイント: df[:k] で計算した過去バーのバンドが df 全体の同区間と一致する。
    df = _ohlc(300, seed=13)
    k = 180
    m_full = marod_series(df, source="close", maxbars=100)
    m_part = marod_series(df.iloc[:k].reset_index(drop=True), source="close", maxbars=100)
    lo_f, hi_f = marod_quantile_bands(m_full, window_n=120)
    lo_p, hi_p = marod_quantile_bands(m_part, window_n=120)
    np.testing.assert_allclose(lo_p, lo_f[:k], rtol=1e-12, atol=1e-12, equal_nan=True)
    np.testing.assert_allclose(hi_p, hi_f[:k], rtol=1e-12, atol=1e-12, equal_nan=True)
    slo_f, shi_f, _, _ = marod_sigma_band(m_full, window_n=120)
    slo_p, shi_p, _, _ = marod_sigma_band(m_part, window_n=120)
    np.testing.assert_allclose(slo_p, slo_f[:k], rtol=1e-12, atol=1e-12, equal_nan=True)
    np.testing.assert_allclose(shi_p, shi_f[:k], rtol=1e-12, atol=1e-12, equal_nan=True)


def test_marod_bands_reuse_btlm_empirical_quantile_scale():
    # MAROD 分位 == btlm_trail の乖離率経験分位 × 100（スケール不変・参照実装整合）。
    df = _ohlc(350, seed=21)
    bt = _ref_btlm_trail()
    prices = np.asarray(bt.resolve_source(df, "close"), dtype=np.float64).ravel()
    mean = np.asarray(bt.rolling_ols_window_end(prices, 100)[0], dtype=np.float64).ravel()
    with np.errstate(invalid="ignore", divide="ignore"):
        deviations = (prices - mean) / mean  # btlm_trail 経験分位が用いる乖離率（分数）。
    m = marod_series(df, source="close", maxbars=100)  # = deviations * 100（close ソース）。
    lo, _ = marod_quantile_bands(m, window_n=250, q_low=0.05, q_high=0.95)
    exp_lo = 100.0 * _expected_causal(deviations, 250, lambda f: np.quantile(f, 0.05))
    np.testing.assert_allclose(lo, exp_lo, rtol=1e-10, atol=1e-10, equal_nan=True)


def test_marod_bands_validation_raises():
    m = marod_series(_ohlc(50), source="close", maxbars=100)
    with pytest.raises(ValueError):
        marod_quantile_bands(m, window_n=1)          # window_n < 2
    with pytest.raises(ValueError):
        marod_quantile_bands(m, q_low=0.9, q_high=0.1)  # q_low >= q_high
    with pytest.raises(ValueError):
        marod_sigma_band(m, window_n=1)              # window_n < 2


def test_rolling_causal_fast_matches_loop_reference():
    # ISSUE-154: ベクトル化（_rolling_causal_fast）は従来ループと全バー完全一致
    #   （NaN 位置含む・部分窓/満杯窓の両区間・quantile/mean/std 全種）。
    import numpy as np
    from src.core import _rolling_causal, _rolling_causal_fast
    rng = np.random.default_rng(7)
    v = rng.normal(0, 1, 1200)
    v[:37] = np.nan                      # warm-up NaN
    v[400:405] = np.nan                  # 途中欠損
    for w in (5, 60, 500, 1500):
        for kind, q, red in (
            ("quantile", 0.05, lambda f: np.quantile(f, 0.05)),
            ("quantile", 0.95, lambda f: np.quantile(f, 0.95)),
            ("mean", None, lambda f: f.mean()),
            ("std", None, lambda f: f.std(ddof=1)),
        ):
            slow = _rolling_causal(v, w, red)
            fast = _rolling_causal_fast(v, w, kind, q)
            np.testing.assert_allclose(fast, slow, rtol=1e-12, atol=1e-12, equal_nan=True)
