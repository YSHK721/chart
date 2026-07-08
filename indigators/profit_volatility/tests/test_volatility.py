"""PRO!fit_Volatility 成果物層の検証（DataFrame 入出力アダプタ）。

OHLC DataFrame → クランプ済み標準化系列の列 / σ12 水準辞書への変換が本質コア層
（``compute_core_volatility``）と 1:1 一致し、列名大小不問・index 継承・必須列欠落例外を
満たすことを固定する。warm-up（先頭 period 本）は NaN（非描画）である点も含めて比較する。
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
    compute_core_volatility,
    volatility_levels,
)


# --------------------------------------------------------------------------- fixtures
# 因果窓（既定 120）で有効点が出るよう、比較系テストは十分な長さを使う。
_WIN = 60


def _ohlc_df(n: int = 200, seed: int = 3) -> pd.DataFrame:
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
    out = build_volatility(df, window=_WIN)
    res = compute_core_volatility(
        df["open"].to_numpy(float), df["high"].to_numpy(float),
        df["low"].to_numpy(float), df["close"].to_numpy(float),
        period=DEFAULT_PERIOD, window=_WIN,
    )
    assert LEVEL_COUNT_COLUMN in out.columns
    np.testing.assert_allclose(
        out[LEVEL_COUNT_COLUMN].to_numpy(), res.level_count_clamped,
        rtol=0, atol=0, equal_nan=True,
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


def test_build_volatility_period_and_window_kwargs_propagate():
    df = _ohlc_df()
    out = build_volatility(df, period=3, window=_WIN)
    res = compute_core_volatility(
        df["open"].to_numpy(float), df["high"].to_numpy(float),
        df["low"].to_numpy(float), df["close"].to_numpy(float),
        period=3, window=_WIN,
    )
    np.testing.assert_allclose(
        out[LEVEL_COUNT_COLUMN].to_numpy(), res.level_count_clamped,
        rtol=0, atol=0, equal_nan=True,
    )


def test_build_volatility_missing_column_raises():
    df = _ohlc_df().drop(columns=["high"])
    with pytest.raises(KeyError):
        build_volatility(df)


# ================================================================= volatility_levels
def test_volatility_levels_matches_core():
    df = _ohlc_df()
    levels = volatility_levels(df, window=_WIN)
    res = compute_core_volatility(
        df["open"].to_numpy(float), df["high"].to_numpy(float),
        df["low"].to_numpy(float), df["close"].to_numpy(float),
        period=DEFAULT_PERIOD, window=_WIN,
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
