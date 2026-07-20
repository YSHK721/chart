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

from src import DEFAULT_MAXBARS, marod_series  # noqa: E402
from src.core import _load_btlm_trail  # noqa: E402

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
