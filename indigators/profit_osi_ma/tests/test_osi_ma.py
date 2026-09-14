"""PRO!fit_OSI_MA 成果物層（pandas）の検証。

build_osi_ma が KAIRI_COLUMN 1 列・元 index 継承・NaN 保持で成果物 DataFrame を
返すこと、osi_ma_levels が 4 水準を返すことを固定する。列名大小不問。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    KAIRI_COLUMN,
    build_osi_ma,
    osi_ma_levels,
)


def _df(close, index=None, col="close"):
    return pd.DataFrame({col: close}, index=index)


# --------------------------------------------------------------------------- 成果物列
def test_build_returns_single_kairi_column_with_index():
    close = [10.0, 20.0, 30.0, 25.0, 40.0, 35.0]
    idx = pd.date_range("2024-01-01", periods=6, freq="D")
    out = build_osi_ma(_df(close, index=idx), ma_mode=0, ma_period=3)
    # KAIRI_COLUMN 1 列のみ。
    assert list(out.columns) == [KAIRI_COLUMN]
    # 元 index 継承。
    assert out.index.equals(idx)
    # core と同値（NaN 保持）。
    assert np.isnan(out[KAIRI_COLUMN].iloc[0])
    assert np.isnan(out[KAIRI_COLUMN].iloc[1])
    assert out[KAIRI_COLUMN].iloc[2] == pytest.approx(0.0)
    assert out[KAIRI_COLUMN].iloc[3] == pytest.approx(20.0)


def test_build_accepts_uppercase_close_column():
    close = [10.0, 20.0, 30.0, 25.0, 40.0, 35.0]
    out = build_osi_ma(_df(close, col="Close"), ma_mode=0, ma_period=3)
    assert out[KAIRI_COLUMN].iloc[3] == pytest.approx(20.0)


def test_build_missing_close_raises_key_error():
    df = pd.DataFrame({"open": [1.0, 2.0, 3.0]})
    with pytest.raises(KeyError):
        build_osi_ma(df, ma_mode=0, ma_period=3)


# --------------------------------------------------------------------------- 水準線
def test_osi_ma_levels_returns_four_levels():
    levels = osi_ma_levels()
    assert levels == {
        "lvl_1": 1.0,
        "lvl_05": 0.5,
        "lvl_-05": -0.5,
        "lvl_-1": -1.0,
    }
