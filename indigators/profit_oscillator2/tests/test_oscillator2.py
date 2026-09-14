"""profit_oscillator2 成果物層テスト（Red→Green・DataFrame 入出力 1:1 固定）。

discriminating 観点:
    TC-COLS   : build_oscillator2 が LEVEL_COUNT_COLUMN / RCI_COLUMN の 2 列を返す。
    TC-VALUES : 2 列の値が core.compute_oscillator2_full と一致する。
    TC-CASE   : 列名大小不問（Open/HIGH/low/Close/Volume 混在でも解決）。
    TC-LEVELS : oscillator2_levels が σ6 dict ＋ sub_min/sub_max を返す。
    TC-KEYERR : volume を含む必須列欠落で KeyError。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import core, oscillator2  # noqa: E402


def _make_df(n: int = 80, seed: int = 11, columns=None) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = np.cumsum(rng.normal(0.0, 1.0, n)) + 100.0
    high = close + rng.uniform(0.2, 1.5, n)
    low = close - rng.uniform(0.2, 1.5, n)
    open_ = close + rng.normal(0.0, 0.3, n)
    volume = rng.uniform(100.0, 1000.0, n)
    cols = columns or ["open", "high", "low", "close", "volume"]
    return pd.DataFrame(
        {cols[0]: open_, cols[1]: high, cols[2]: low, cols[3]: close, cols[4]: volume}
    )


# =========================================================================== #
# TC-COLS / TC-VALUES
# =========================================================================== #
def test_build_oscillator2_returns_two_named_columns():
    """build_oscillator2 が LEVEL_COUNT_COLUMN / RCI_COLUMN の 2 列だけを返すことを固定。"""
    df = _make_df()
    out = oscillator2.build_oscillator2(df)
    assert list(out.columns) == [
        oscillator2.LEVEL_COUNT_COLUMN,
        oscillator2.RCI_COLUMN,
    ]
    assert oscillator2.LEVEL_COUNT_COLUMN == "oscillator2_lc"
    assert oscillator2.RCI_COLUMN == "oscillator2_rci"
    assert len(out) == len(df)


def test_build_oscillator2_values_match_core():
    """build_oscillator2 の 2 列値が core.compute_oscillator2_full と一致することを固定。"""
    df = _make_df()
    out = oscillator2.build_oscillator2(df)
    res = core.compute_oscillator2_full(
        df["open"].to_numpy(),
        df["high"].to_numpy(),
        df["low"].to_numpy(),
        df["close"].to_numpy(),
        df["volume"].to_numpy(),
    )
    np.testing.assert_array_equal(
        out[oscillator2.LEVEL_COUNT_COLUMN].to_numpy(), res.level_count
    )
    np.testing.assert_array_equal(out[oscillator2.RCI_COLUMN].to_numpy(), res.rci)


# =========================================================================== #
# TC-CASE: 列名大小不問
# =========================================================================== #
def test_build_oscillator2_is_case_insensitive_for_columns():
    """列名大小混在（Open/HIGH/low/Close/Volume）でも同一結果を返すことを固定。"""
    base = _make_df()
    mixed = base.rename(
        columns={
            "open": "Open",
            "high": "HIGH",
            "low": "low",
            "close": "Close",
            "volume": "Volume",
        }
    )
    out_base = oscillator2.build_oscillator2(base)
    out_mixed = oscillator2.build_oscillator2(mixed)
    np.testing.assert_array_equal(
        out_base[oscillator2.LEVEL_COUNT_COLUMN].to_numpy(),
        out_mixed[oscillator2.LEVEL_COUNT_COLUMN].to_numpy(),
    )
    np.testing.assert_array_equal(
        out_base[oscillator2.RCI_COLUMN].to_numpy(),
        out_mixed[oscillator2.RCI_COLUMN].to_numpy(),
    )


# =========================================================================== #
# TC-LEVELS: oscillator2_levels
# =========================================================================== #
def test_oscillator2_levels_returns_sigma6_dict_with_sub_bounds():
    """oscillator2_levels が σ6 dict ＋ sub_min/sub_max を返し core と一致することを固定。"""
    df = _make_df()
    levels = oscillator2.oscillator2_levels(df)
    res = core.compute_oscillator2_full(
        df["open"].to_numpy(),
        df["high"].to_numpy(),
        df["low"].to_numpy(),
        df["close"].to_numpy(),
        df["volume"].to_numpy(),
    )
    levels2 = core.compute_levels2(res.level_count)
    for key in ("up_165", "up_196", "up_258", "dn_165", "dn_196", "dn_258",
                "sub_min", "sub_max"):
        assert key in levels
        assert levels[key] == pytest.approx(levels2[key])


# =========================================================================== #
# TC-KEYERR: 必須列欠落
# =========================================================================== #
def test_build_oscillator2_missing_volume_raises_keyerror():
    """volume 列欠落で KeyError を投げることを固定。"""
    df = _make_df().drop(columns=["volume"])
    with pytest.raises(KeyError):
        oscillator2.build_oscillator2(df)


def test_oscillator2_levels_missing_high_raises_keyerror():
    """high 列欠落で KeyError を投げることを固定。"""
    df = _make_df().drop(columns=["high"])
    with pytest.raises(KeyError):
        oscillator2.oscillator2_levels(df)
