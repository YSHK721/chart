"""PRO!fitSTC 成果物層（pandas）の検証。

build_stc: high/low/close DataFrame → OSC_COLUMN 1 列・元 index 継承。
    元 ``SetIndexBuffer(0, ExtBufferOscillator)`` への書き込みに対応。
stc_levels: 成果物の {P1,P2,M1,M2,sub_min,sub_max} を辞書で返す。
    元 ``StcLCStdDevArray[1..4]`` および
    ``IndicatorSetDouble(INDICATOR_MINIMUM=M2, INDICATOR_MAXIMUM=P2)`` に対応。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    OSC_COLUMN,
    build_stc,
    compute_osc_levels,
    compute_stc,
    stc_levels,
)


def _make_df(n: int) -> pd.DataFrame:
    """単調レンジの OHLC DataFrame（任意 index）を作る。"""
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    base = np.arange(n, dtype=float)
    return pd.DataFrame(
        {"high": base + 10.0, "low": base, "close": base + 5.0},
        index=idx,
    )


def test_build_stc_returns_single_column_with_index():
    """OSC_COLUMN 1 列・元 index 継承の DataFrame を返す。"""
    # Arrange
    df = _make_df(5)

    # Act
    out = build_stc(df, period=2)

    # Assert
    assert list(out.columns) == [OSC_COLUMN]
    assert out.index.equals(df.index)
    assert len(out) == 5


def test_build_stc_matches_core_oscillator():
    """成果物の値が core.compute_stc(...).oscillator と一致する。"""
    # Arrange
    df = _make_df(6)

    # Act
    out = build_stc(df, period=3)
    expected = compute_stc(
        df["high"].to_numpy(),
        df["low"].to_numpy(),
        df["close"].to_numpy(),
        period=3,
    ).oscillator

    # Assert
    np.testing.assert_array_equal(out[OSC_COLUMN].to_numpy(), expected)


def test_build_stc_case_insensitive_columns():
    """列名の大小は不問（High/LOW/Close でも動作）。"""
    # Arrange
    df = _make_df(4).rename(columns={"high": "High", "low": "LOW", "close": "Close"})

    # Act
    out = build_stc(df, period=2)

    # Assert
    assert list(out.columns) == [OSC_COLUMN]
    assert len(out) == 4


def test_build_stc_missing_column_raises():
    """high/low/close いずれか欠落で KeyError。"""
    # Arrange
    df = _make_df(4).drop(columns=["low"])

    # Act / Assert
    with pytest.raises(KeyError):
        build_stc(df, period=2)


def test_stc_levels_keys_include_sub_range():
    """stc_levels は {P1,P2,M1,M2,sub_min,sub_max} を返す。"""
    # Arrange
    df = _make_df(30)

    # Act
    levels = stc_levels(df, period=5)

    # Assert
    assert set(levels.keys()) == {"P1", "P2", "M1", "M2", "sub_min", "sub_max"}
    assert levels["sub_min"] == pytest.approx(levels["M2"])
    assert levels["sub_max"] == pytest.approx(levels["P2"])
    # 単調: P2 >= P1 >= M1 >= M2
    assert levels["P2"] >= levels["P1"] >= levels["M1"] >= levels["M2"]


def test_stc_levels_matches_compute_osc_levels_on_full_series():
    """stc_levels の P/M は build_stc 全系列（warm-up 0 込み）の compute_osc_levels と一致。"""
    # Arrange
    df = _make_df(20)
    osc = build_stc(df, period=4)[OSC_COLUMN].to_numpy()
    expected = compute_osc_levels(osc)  # 全系列・0 込み

    # Act
    levels = stc_levels(df, period=4)

    # Assert
    for key in ("P1", "P2", "M1", "M2"):
        assert levels[key] == pytest.approx(expected[key])
    assert levels["sub_min"] == pytest.approx(expected["M2"])
    assert levels["sub_max"] == pytest.approx(expected["P2"])
