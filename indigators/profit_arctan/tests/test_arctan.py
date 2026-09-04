"""PRO!fit_Arctan 成果物層（DataFrame 入出力アダプタ）の検証。

build_arctan が OHLC DataFrame からクランプ済みレベルカウント列を生成し、元 index を
継承すること、列名大小不問・必須列欠落で KeyError、arctan_levels が σ12 を返すことを固定する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    LEVEL_COUNT_COLUMN,
    arctan_levels,
    build_arctan,
    compute_arctan_full,
)


def _ohlc_df(n: int = 30, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    o = rng.uniform(100, 110, n)
    h = o + rng.uniform(0.5, 2.0, n)
    low = o - rng.uniform(0.5, 2.0, n)
    c = low + rng.uniform(0.0, (h - low))
    return pd.DataFrame({"Open": o, "High": h, "Low": low, "Close": c})


def test_level_count_column_constant():
    assert LEVEL_COUNT_COLUMN == "arctan_lc"


def test_build_arctan_adds_clamped_level_count_column():
    df = _ohlc_df()
    out = build_arctan(df, period=6, ma_method=1, bar_width=0.1)
    assert LEVEL_COUNT_COLUMN in out.columns
    # core の level_count_clamped と一致
    res = compute_arctan_full(
        df["Open"].to_numpy(), df["High"].to_numpy(),
        df["Low"].to_numpy(), df["Close"].to_numpy(),
        period=6, ma_method=1, bar_width=0.1,
    )
    np.testing.assert_allclose(
        out[LEVEL_COUNT_COLUMN].to_numpy(), res.level_count_clamped, rtol=0, atol=0
    )


def test_build_arctan_preserves_original_index():
    df = _ohlc_df()
    df.index = pd.RangeIndex(start=100, stop=100 + len(df))
    out = build_arctan(df)
    assert list(out.index) == list(df.index)


def test_build_arctan_column_names_case_insensitive():
    df = _ohlc_df()
    df_lower = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close"})
    out_lower = build_arctan(df_lower)
    out_upper = build_arctan(df)
    np.testing.assert_allclose(
        out_lower[LEVEL_COUNT_COLUMN].to_numpy(),
        out_upper[LEVEL_COUNT_COLUMN].to_numpy(),
        rtol=0, atol=0,
    )


def test_build_arctan_missing_column_raises_keyerror():
    df = _ohlc_df().drop(columns=["Close"])
    with pytest.raises(KeyError):
        build_arctan(df)


def test_arctan_levels_returns_sigma12():
    df = _ohlc_df()
    levels = arctan_levels(df)
    assert len(levels) == 12
    for key in ("up_067", "up_128", "up_165", "up_196", "up_258", "up_329",
                "dn_067", "dn_128", "dn_165", "dn_196", "dn_258", "dn_329"):
        assert key in levels


def test_arctan_levels_missing_column_raises_keyerror():
    df = _ohlc_df().drop(columns=["High"])
    with pytest.raises(KeyError):
        arctan_levels(df)
