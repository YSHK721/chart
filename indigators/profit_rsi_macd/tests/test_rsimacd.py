"""PRO!fitRSIMACD 成果物層（pandas）の検証。

DataFrame から high/low/close を小文字正規化抽出し、core 層（compute_rsimacd）を
呼んで histogram/macd/signal の 3 列のみを付与した DataFrame（元 index 継承）と
σ7 水準辞書を返す薄い変換層の I/O 契約を固定する。中間 rsi/fast/slow は列化しない。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    HIST_COLUMN,
    MACD_COLUMN,
    SIGNAL_COLUMN,
    build_rsimacd,
    compute_rsimacd,
    rsimacd_levels,
)


def _make_df(n=20, index=None, upper=False):
    high = np.arange(10.0, 10.0 + n, dtype=float)
    low = high - 2.0
    close = high - 1.0
    cols = {"high": high, "low": low, "close": close}
    if upper:
        cols = {k.upper(): v for k, v in cols.items()}
    return pd.DataFrame(cols, index=index)


# ===========================================================================
# 列名定数
# ===========================================================================
def test_output_column_name_constants():
    assert HIST_COLUMN == "rsimacd_hist"
    assert MACD_COLUMN == "rsimacd_macd"
    assert SIGNAL_COLUMN == "rsimacd_signal"


# ===========================================================================
# build_rsimacd: 3列のみ付与・index 継承・core 一致
# ===========================================================================
def test_build_rsimacd_adds_only_three_columns():
    # Arrange
    df = _make_df(20)
    original_cols = set(df.columns)
    # Act
    out = build_rsimacd(df)
    added = set(out.columns) - original_cols
    # Assert: histogram/macd/signal の 3 列のみ（中間 rsi/fast/slow は列化しない）
    assert added == {HIST_COLUMN, MACD_COLUMN, SIGNAL_COLUMN}
    assert "rsimacd_rsi" not in out.columns
    assert "rsimacd_fast" not in out.columns
    assert "rsimacd_slow" not in out.columns


def test_build_rsimacd_preserves_original_index():
    # Arrange: 非連番 index
    idx = pd.date_range("2024-01-01", periods=20, freq="D")
    df = _make_df(20, index=idx)
    # Act
    out = build_rsimacd(df)
    # Assert
    pd.testing.assert_index_equal(out.index, df.index)


def test_build_rsimacd_columns_match_core_compute():
    # Arrange
    df = _make_df(20)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    # open は core 内で無視されるため high を仮置き（typical は h/l/c のみ）
    result = compute_rsimacd(high, high, low, close)
    # Act
    out = build_rsimacd(df)
    # Assert
    np.testing.assert_allclose(out[HIST_COLUMN].to_numpy(), result.histogram, atol=1e-12)
    np.testing.assert_allclose(out[MACD_COLUMN].to_numpy(), result.macd, atol=1e-12)
    np.testing.assert_allclose(out[SIGNAL_COLUMN].to_numpy(), result.signal, atol=1e-12)


def test_build_rsimacd_accepts_uppercase_column_names():
    # Arrange: 大文字列名
    df = _make_df(20, upper=True)
    # Act / Assert: 列名大小不問
    out = build_rsimacd(df)
    assert HIST_COLUMN in out.columns


def test_build_rsimacd_raises_key_error_when_required_column_missing():
    # Arrange: close 欠落
    df = _make_df(20).drop(columns=["close"])
    # Act / Assert
    with pytest.raises(KeyError):
        build_rsimacd(df)


# ===========================================================================
# rsimacd_levels: 7水準・core 一致
# ===========================================================================
def test_rsimacd_levels_returns_seven_keys():
    df = _make_df(20)
    levels = rsimacd_levels(df)
    assert set(levels.keys()) == {"p1", "p2", "p3", "m1", "m2", "m3", "mid50"}
    assert levels["mid50"] == 50.0


def test_rsimacd_levels_match_core_compute():
    # Arrange
    df = _make_df(20)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    result = compute_rsimacd(high, high, low, close)
    # Act
    levels = rsimacd_levels(df)
    # Assert
    assert levels == result.levels


def test_rsimacd_levels_raises_key_error_when_required_column_missing():
    df = _make_df(20).drop(columns=["high"])
    with pytest.raises(KeyError):
        rsimacd_levels(df)
