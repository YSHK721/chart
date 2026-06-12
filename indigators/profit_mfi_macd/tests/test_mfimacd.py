"""PRO!fitMFIMACD 成果物層（pandas）の検証。

DataFrame から high/low/close/volume を抽出し core 層へ委譲、histogram/macd/signal
の 3 列のみ（mfi/fast/slow は列化しない）を元 index 継承で付与し、σ7水準辞書を返す
薄い変換層の I/O 契約を固定する。
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
    build_mfimacd,
    compute_mfimacd,
    mfimacd_levels,
)


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "High": [10.0, 11.0, 12.0, 11.0, 13.0, 13.0, 14.0, 12.0],
            "Low": [8.0, 9.0, 10.0, 9.0, 11.0, 11.0, 12.0, 10.0],
            "Close": [9.0, 10.0, 11.0, 10.0, 12.0, 12.0, 13.0, 11.0],
            "Volume": [100.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0, 800.0],
        },
        index=[100, 101, 102, 103, 104, 105, 106, 107],
    )


# ---------------------------------------------------------------------------
# TC-M1 列名定数
# ---------------------------------------------------------------------------
def test_column_name_constants():
    assert HIST_COLUMN == "mfimacd_hist"
    assert MACD_COLUMN == "mfimacd_macd"
    assert SIGNAL_COLUMN == "mfimacd_signal"


# ---------------------------------------------------------------------------
# TC-M2 build_mfimacd は histogram/macd/signal の 3 列のみ付与し元 index 継承
# ---------------------------------------------------------------------------
def test_build_mfimacd_adds_only_three_columns_and_keeps_index():
    # Arrange
    df = _sample_df()
    expected = compute_mfimacd(
        df["High"].to_numpy(dtype=np.float64),
        df["Low"].to_numpy(dtype=np.float64),
        df["Close"].to_numpy(dtype=np.float64),
        df["Volume"].to_numpy(dtype=np.float64),
        mfi_period=3,
        fast=4,
        slow=8,
        signal=4,
    )

    # Act
    out = build_mfimacd(df, mfi_period=3, fast=4, slow=8, signal=4)

    # Assert: 3 列が付与され、mfi/fast/slow は列化されない。
    assert HIST_COLUMN in out.columns
    assert MACD_COLUMN in out.columns
    assert SIGNAL_COLUMN in out.columns
    for absent in ("mfi", "mfimacd_mfi", "fast", "slow"):
        assert absent not in out.columns
    # 元 index 継承。
    assert list(out.index) == [100, 101, 102, 103, 104, 105, 106, 107]
    # 値が core と一致。
    np.testing.assert_allclose(
        out[HIST_COLUMN].to_numpy(), expected.histogram, rtol=1e-12
    )
    np.testing.assert_allclose(
        out[MACD_COLUMN].to_numpy(), expected.macd, rtol=1e-12
    )
    np.testing.assert_allclose(
        out[SIGNAL_COLUMN].to_numpy(), expected.signal, rtol=1e-12
    )


# ---------------------------------------------------------------------------
# TC-M3 列名大小不問（小文字列でも抽出できる）
# ---------------------------------------------------------------------------
def test_build_mfimacd_accepts_lowercase_columns():
    df = _sample_df().rename(
        columns={"High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
    )
    out = build_mfimacd(df, mfi_period=3, fast=4, slow=8, signal=4)
    assert HIST_COLUMN in out.columns


# ---------------------------------------------------------------------------
# TC-M4 volume 欠落 -> KeyError
# ---------------------------------------------------------------------------
def test_build_mfimacd_raises_keyerror_when_volume_missing():
    df = _sample_df().drop(columns=["Volume"])
    with pytest.raises(KeyError):
        build_mfimacd(df, mfi_period=3)


# ---------------------------------------------------------------------------
# TC-M5 mfimacd_levels は 7 水準辞書を返す
# ---------------------------------------------------------------------------
def test_mfimacd_levels_returns_seven_levels():
    df = _sample_df()
    levels = mfimacd_levels(df, mfi_period=3, fast=4, slow=8, signal=4)
    assert set(levels.keys()) == {"p1", "p2", "p3", "m1", "m2", "m3", "mid50"}
    assert levels["mid50"] == 50.0


# ---------------------------------------------------------------------------
# TC-M6 mfimacd_levels も volume 欠落 -> KeyError
# ---------------------------------------------------------------------------
def test_mfimacd_levels_raises_keyerror_when_volume_missing():
    df = _sample_df().drop(columns=["Volume"])
    with pytest.raises(KeyError):
        mfimacd_levels(df, mfi_period=3)
