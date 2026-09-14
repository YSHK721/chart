"""PRO!fitRSI 成果物層（pandas）の検証。

``build_rsi`` が OHLC DataFrame から apply で適用価格を選び core を呼び、RSI 列と水準列
（正常帯 2・外れ値 4）を付与した DataFrame（元 index 継承）を返すこと、列名が分位から導かれる
こと、必須列欠落で KeyError を送出することを固定する。水準そのものの定義は
``tests/test_levels.py``。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    LEVEL_COLUMNS,
    RSI_COLUMN,
    build_rsi,
    compute_rsi_full,
    quantile_column,
)


def _ohlc():
    return pd.DataFrame(
        {
            "open": [10.0, 11.0, 10.5, 12.0, 11.0, 11.0, 12.5, 13.0],
            "high": [10.8, 11.7, 11.2, 12.6, 11.4, 11.9, 13.2, 13.6],
            "low": [9.3, 10.2, 10.1, 11.4, 10.6, 10.4, 12.1, 12.7],
            "close": [10.2, 11.3, 10.4, 12.2, 11.1, 11.6, 12.8, 13.1],
        },
        index=[100, 101, 102, 103, 104, 105, 106, 107],
    )


# ---------------------------------------------------------------------------
# TC-16 build_rsi は RSI 列を付与し元 index を継承する（apply=0 -> close）
# ---------------------------------------------------------------------------
def test_build_rsi_appends_rsi_column_preserving_index():
    # Arrange
    df = _ohlc()

    # Act
    out = build_rsi(df, rsi_period=3, apply=0)

    # Assert: 列付与・index 継承・close ベースの core 出力と一致。
    assert RSI_COLUMN in out.columns
    assert "rsi_ma" not in out.columns  # EMA 平滑列は持たない（ma_period 削除）
    assert list(out.index) == list(df.index)
    full = compute_rsi_full(
        df["open"].to_numpy(float), df["high"].to_numpy(float),
        df["low"].to_numpy(float), df["close"].to_numpy(float),
        rsi_period=3, apply=0,
    )
    np.testing.assert_allclose(out[RSI_COLUMN].to_numpy(), full.rsi, rtol=1e-12)


# ---------------------------------------------------------------------------
# TC-17 apply 既定（=5 -> TYPICAL）は (H+L+C)/3 ベースで計算する
# ---------------------------------------------------------------------------
def test_build_rsi_default_apply_uses_typical_price():
    # Arrange
    df = _ohlc()
    open_ = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    expected = compute_rsi_full(open_, high, low, close, rsi_period=3)

    # Act（apply 省略 = 既定 5 -> TYPICAL）。
    out = build_rsi(df, rsi_period=3)

    # Assert
    np.testing.assert_allclose(out[RSI_COLUMN].to_numpy(), expected.rsi, rtol=1e-12)


# ---------------------------------------------------------------------------
# TC-18 列名の大小は不問（OPEN/HIGH/LOW/CLOSE でも動く）
# ---------------------------------------------------------------------------
def test_build_rsi_is_case_insensitive_for_columns():
    df = _ohlc().rename(
        columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"}
    )
    out = build_rsi(df, rsi_period=3, apply=0)
    assert RSI_COLUMN in out.columns


# ---------------------------------------------------------------------------
# TC-19 必須列欠落で KeyError
# ---------------------------------------------------------------------------
def test_build_rsi_raises_keyerror_on_missing_column():
    df = _ohlc().drop(columns=["high"])
    with pytest.raises(KeyError):
        build_rsi(df, rsi_period=3)


# ---------------------------------------------------------------------------
# TC-20 build_rsi は正常帯 2 列・外れ値 4 列を付け、列名は分位から導かれる
# ---------------------------------------------------------------------------
def test_build_rsi_appends_band_and_outlier_level_columns():
    df = _ohlc()
    out = build_rsi(df, rsi_period=3, apply=0, window_n=3, q_low=0.2, q_high=0.8, k_events=5)

    # 正常帯の列名は分位から導く（tickvol と同規約）。
    assert quantile_column(0.2) == "rsi_q20"
    assert "rsi_q20" in out.columns and "rsi_q80" in out.columns
    # 外れ値水準は経験的（evq）と GPD 外挿の上下 4 本。
    assert set(LEVEL_COLUMNS.values()) == {
        "rsi_evq_ext_hi", "rsi_evq_ext_lo", "rsi_gpd_hi", "rsi_gpd_lo"
    }
    for column in LEVEL_COLUMNS.values():
        assert column in out.columns
    assert list(out.index) == list(df.index)


# ---------------------------------------------------------------------------
# TC-21 水準は [0,100] を出ない（RSI は有界・余地割合スケールの構成上の不変条件）
# ---------------------------------------------------------------------------
def test_level_columns_stay_inside_rsi_bounds():
    df = _ohlc()
    out = build_rsi(df, rsi_period=3, apply=0, window_n=3, q_low=0.2, q_high=0.8, k_events=5)
    level_columns = ["rsi_q20", "rsi_q80", *LEVEL_COLUMNS.values()]
    values = out[level_columns].to_numpy(dtype=float)
    finite = values[np.isfinite(values)]
    assert finite.min() >= 0.0
    assert finite.max() <= 100.0
