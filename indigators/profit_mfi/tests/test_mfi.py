"""PRO!fitMFI 成果物層（pandas）の検証。

DataFrame から high/low/close/volume を小文字正規化抽出し、MFI 列・MA 列を
付与した DataFrame（元 index 継承）と σ 水準辞書を返す薄い変換層を固定する。
core 層（compute_mfi_full / compute_mfi_levels）の数値は test_core.py で固定済み。
本層は列抽出・列名・大小不問・必須列欠落例外の I/O 契約のみを検証する。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    MA_COLUMN,
    MFI_COLUMN,
    build_mfi,
    mfi_levels,
)
from src.core import compute_mfi_full  # noqa: E402


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "high": [10.0, 11.0, 12.0, 11.0, 13.0, 13.0],
            "low": [8.0, 9.0, 10.0, 9.0, 11.0, 11.0],
            "close": [9.0, 10.0, 11.0, 10.0, 12.0, 12.0],
            "volume": [100.0, 200.0, 300.0, 400.0, 500.0, 600.0],
        },
        index=[10, 20, 30, 40, 50, 60],
    )


# ---------------------------------------------------------------------------
# TC-11 build_mfi が MFI/MA 列を付与し元 index を継承する
# ---------------------------------------------------------------------------
def test_build_mfi_adds_mfi_and_ma_columns_with_original_index():
    # Arrange
    df = _sample_df()
    full = compute_mfi_full(
        df["high"].to_numpy(float),
        df["low"].to_numpy(float),
        df["close"].to_numpy(float),
        df["volume"].to_numpy(float),
        mfi_period=3,
        ma_period=5,
    )

    # Act
    out = build_mfi(df, mfi_period=3, ma_period=5)

    # Assert
    assert MFI_COLUMN == "mfi"
    assert MA_COLUMN == "mfi_ma"
    assert MFI_COLUMN in out.columns
    assert MA_COLUMN in out.columns
    np.testing.assert_allclose(out[MFI_COLUMN].to_numpy(), full.mfi, rtol=1e-12)
    np.testing.assert_allclose(out[MA_COLUMN].to_numpy(), full.ma, rtol=1e-12)
    assert list(out.index) == [10, 20, 30, 40, 50, 60]


# ---------------------------------------------------------------------------
# TC-12 列名の大文字小文字を問わない（小文字正規化）
# ---------------------------------------------------------------------------
def test_build_mfi_accepts_uppercase_column_names():
    # Arrange: 列名を大文字・混在に。
    df = _sample_df().rename(
        columns={
            "high": "High",
            "low": "LOW",
            "close": "Close",
            "volume": "Volume",
        }
    )

    # Act
    out = build_mfi(df, mfi_period=3, ma_period=5)

    # Assert: 大文字でも正しく抽出され MFI/MA 列が付く。
    assert MFI_COLUMN in out.columns
    assert MA_COLUMN in out.columns


# ---------------------------------------------------------------------------
# TC-13 volume を含む必須列欠落 -> KeyError 相当
# ---------------------------------------------------------------------------
def test_build_mfi_raises_keyerror_when_volume_column_missing():
    # Arrange: volume 列を欠落させる。
    df = _sample_df().drop(columns=["volume"])

    # Act / Assert
    with pytest.raises(KeyError):
        build_mfi(df, mfi_period=3, ma_period=5)


# ---------------------------------------------------------------------------
# TC-14 mfi_levels が 7 水準辞書を返す
# ---------------------------------------------------------------------------
def test_mfi_levels_returns_seven_level_dict():
    # Arrange
    df = _sample_df()

    # Act
    levels = mfi_levels(df, mfi_period=3, ma_period=5)

    # Assert
    assert set(levels.keys()) == {"p1", "p2", "p3", "m1", "m2", "m3", "mid50"}
    assert levels["mid50"] == 50.0
