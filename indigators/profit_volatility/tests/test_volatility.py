"""PRO!fit_Volatility 成果物層の検証（DataFrame 入出力アダプタ）。

OHLC DataFrame → クランプ済みレベルカウント列 / σ12 水準辞書への変換が core 層
（``compute_volatility_full``）と 1:1 一致し、列名大小不問・index 継承・必須列欠落例外を
満たすことを固定する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    DEFAULT_PERIOD,
    LEVEL_COUNT_COLUMN,
    build_volatility,
    compute_volatility_full,
    volatility_levels,
)


# --------------------------------------------------------------------------- fixtures
def _ohlc_df(n: int = 30, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    o = rng.uniform(100, 110, n)
    h = o + rng.uniform(0.5, 2.0, n)
    low = o - rng.uniform(0.5, 2.0, n)
    c = low + rng.uniform(0.0, (h - low))
    idx = pd.date_range("2024-01-01", periods=n, freq="h")
    return pd.DataFrame({"open": o, "high": h, "low": low, "close": c}, index=idx)


# =================================================================== build_volatility
def test_build_volatility_column_matches_core_clamped():
    df = _ohlc_df()
    out = build_volatility(df)
    res = compute_volatility_full(
        df["open"].to_numpy(float), df["high"].to_numpy(float),
        df["low"].to_numpy(float), df["close"].to_numpy(float),
        period=DEFAULT_PERIOD,
    )
    assert LEVEL_COUNT_COLUMN in out.columns
    np.testing.assert_allclose(
        out[LEVEL_COUNT_COLUMN].to_numpy(), res.level_count_clamped, rtol=0, atol=0
    )


def test_build_volatility_inherits_index():
    df = _ohlc_df()
    out = build_volatility(df)
    assert out.index.equals(df.index)


def test_build_volatility_case_insensitive_columns():
    df = _ohlc_df().rename(
        columns={"open": "Open", "high": "HIGH", "low": "Low", "close": "Close"}
    )
    out = build_volatility(df)
    assert LEVEL_COUNT_COLUMN in out.columns
    assert len(out) == len(df)


def test_build_volatility_period_kwarg_propagates():
    df = _ohlc_df()
    out = build_volatility(df, period=3)
    res = compute_volatility_full(
        df["open"].to_numpy(float), df["high"].to_numpy(float),
        df["low"].to_numpy(float), df["close"].to_numpy(float),
        period=3,
    )
    np.testing.assert_allclose(
        out[LEVEL_COUNT_COLUMN].to_numpy(), res.level_count_clamped, rtol=0, atol=0
    )


def test_build_volatility_missing_column_raises():
    df = _ohlc_df().drop(columns=["high"])
    with pytest.raises(KeyError):
        build_volatility(df)


# ================================================================= volatility_levels
def test_volatility_levels_matches_core():
    df = _ohlc_df()
    levels = volatility_levels(df)
    res = compute_volatility_full(
        df["open"].to_numpy(float), df["high"].to_numpy(float),
        df["low"].to_numpy(float), df["close"].to_numpy(float),
        period=DEFAULT_PERIOD,
    )
    assert levels == dict(res.levels)


def test_volatility_levels_has_12_keys():
    df = _ohlc_df()
    levels = volatility_levels(df)
    assert len(levels) == 12
    assert "up_329" in levels and "dn_329" in levels


def test_volatility_levels_missing_column_raises():
    df = _ohlc_df().drop(columns=["close"])
    with pytest.raises(KeyError):
        volatility_levels(df)
