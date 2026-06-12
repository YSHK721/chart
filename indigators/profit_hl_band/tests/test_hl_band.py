"""層名: 成果物層テスト（pandas）。

責務:
    PRO!fit_HLBand の成果物層（build_hl_band / hl_band_levels）を固定する。
    build_hl_band は dist_high/dist_low の 2 列・元 index 継承を、hl_band_levels は
    8 バンド + close_ref を返すことを、必須列欠落 → KeyError とともに固定する。

元 MQL 対応:
    L205-206 MathAbs(iHigh/iLow - iClose)  → build_hl_band（dist_high / dist_low 列）
    L220-227 iClose(1)±iBandsOnArray(...)  → hl_band_levels（8 バンド + close_ref）

依存: 標準 sys/pathlib / 外部 numpy, pandas, pytest / プロジェクト内 src.hl_band
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.hl_band import (  # noqa: E402
    DIST_HIGH_COLUMN,
    DIST_LOW_COLUMN,
    build_hl_band,
    hl_band_levels,
)
from src.core import band_upper  # noqa: E402

# === discriminating dataset（test_core と同一） ===
_HIGH = [10.0, 12.0, 11.0, 13.0, 15.0]
_LOW = [8.0, 9.0, 9.5, 10.0, 11.0]
_CLOSE = [9.0, 10.0, 10.5, 12.0, 14.0]
_EXPECTED_DIST_HIGH = np.array([1.0, 2.0, 0.5, 1.0, 1.0])
_EXPECTED_DIST_LOW = np.array([1.0, 1.0, 1.0, 2.0, 3.0])


def _make_df(index=None) -> pd.DataFrame:
    return pd.DataFrame(
        {"high": _HIGH, "low": _LOW, "close": _CLOSE},
        index=index,
    )


# --- build_hl_band: dist 2 列 ---
def test_build_hl_band_returns_dist_high_and_dist_low_columns():
    # Arrange
    df = _make_df()
    # Act
    out = build_hl_band(df)
    # Assert
    assert list(out.columns) == [DIST_HIGH_COLUMN, DIST_LOW_COLUMN]
    np.testing.assert_allclose(out[DIST_HIGH_COLUMN].to_numpy(), _EXPECTED_DIST_HIGH)
    np.testing.assert_allclose(out[DIST_LOW_COLUMN].to_numpy(), _EXPECTED_DIST_LOW)


def test_build_hl_band_preserves_original_index():
    # Arrange（非自明 index で継承を区別）
    idx = pd.Index([100, 200, 300, 400, 500], name="bar")
    df = _make_df(index=idx)
    # Act
    out = build_hl_band(df)
    # Assert
    pd.testing.assert_index_equal(out.index, idx)


def test_build_hl_band_column_names_are_case_insensitive():
    # Arrange（大文字列名でも抽出可能）
    df = pd.DataFrame({"HIGH": _HIGH, "Low": _LOW, "Close": _CLOSE})
    # Act
    out = build_hl_band(df)
    # Assert
    np.testing.assert_allclose(out[DIST_HIGH_COLUMN].to_numpy(), _EXPECTED_DIST_HIGH)


def test_build_hl_band_raises_keyerror_on_missing_column():
    # Arrange（close 欠落）
    df = pd.DataFrame({"high": _HIGH, "low": _LOW})
    # Act / Assert
    with pytest.raises(KeyError):
        build_hl_band(df)


# --- hl_band_levels: 8 バンド + close_ref ---
def test_hl_band_levels_returns_eight_bands_and_close_ref():
    # Arrange
    df = _make_df()
    # Act
    levels = hl_band_levels(df)
    # Assert
    assert set(levels.keys()) == {
        "up_067", "up_165", "up_196", "up_258",
        "dn_067", "dn_165", "dn_196", "dn_258",
        "close_ref",
    }


def test_hl_band_levels_values_match_core_semantics():
    # Arrange
    df = _make_df()
    # Act
    levels = hl_band_levels(df)
    # Assert（close_ref=close[-2]=12.0、dn_165=12.0-2.92=9.08）
    assert levels["close_ref"] == pytest.approx(12.0, abs=1e-12)
    assert levels["up_165"] == pytest.approx(12.0 + band_upper(_EXPECTED_DIST_HIGH, 1.65), abs=1e-12)
    assert levels["dn_165"] == pytest.approx(9.08, abs=1e-12)


def test_hl_band_levels_raises_keyerror_on_missing_column():
    # Arrange（high 欠落）
    df = pd.DataFrame({"low": _LOW, "close": _CLOSE})
    # Act / Assert
    with pytest.raises(KeyError):
        hl_band_levels(df)
