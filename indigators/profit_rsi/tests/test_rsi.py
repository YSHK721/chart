"""PRO!fitRSI 成果物層（pandas）の検証。

``build_rsi`` が OHLC DataFrame から apply で適用価格を選び core を呼び、RSI 列・MA 列を
付与した DataFrame（元 index 継承）を返すこと、``rsi_levels`` が生 RSI 由来の σ 水準辞書
（7 要素）を返すこと、必須列欠落で KeyError を送出することを固定する。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    MA_COLUMN,
    RSI_COLUMN,
    build_rsi,
    compute_rsi_full,
    compute_rsi_levels,
    rsi_levels,
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
# TC-16 build_rsi は RSI/MA 列を付与し元 index を継承する（apply=0 -> close）
# ---------------------------------------------------------------------------
def test_build_rsi_appends_rsi_and_ma_columns_preserving_index():
    # Arrange
    df = _ohlc()

    # Act
    out = build_rsi(df, rsi_period=3, apply=0, ma_period=2)

    # Assert: 列付与・index 継承・close ベースの core 出力と一致。
    assert RSI_COLUMN in out.columns
    assert MA_COLUMN in out.columns
    assert list(out.index) == list(df.index)
    full = compute_rsi_full(
        df["open"].to_numpy(float), df["high"].to_numpy(float),
        df["low"].to_numpy(float), df["close"].to_numpy(float),
        rsi_period=3, apply=0, ma_period=2,
    )
    np.testing.assert_allclose(out[RSI_COLUMN].to_numpy(), full.rsi, rtol=1e-12)
    np.testing.assert_allclose(out[MA_COLUMN].to_numpy(), full.ma, rtol=1e-12)


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
    expected = compute_rsi_full(open_, high, low, close, rsi_period=3, ma_period=2)

    # Act（apply 省略 = 既定 5 -> TYPICAL）。
    out = build_rsi(df, rsi_period=3, ma_period=2)

    # Assert
    np.testing.assert_allclose(out[RSI_COLUMN].to_numpy(), expected.rsi, rtol=1e-12)


# ---------------------------------------------------------------------------
# TC-18 列名の大小は不問（OPEN/HIGH/LOW/CLOSE でも動く）
# ---------------------------------------------------------------------------
def test_build_rsi_is_case_insensitive_for_columns():
    df = _ohlc().rename(
        columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"}
    )
    out = build_rsi(df, rsi_period=3, apply=0, ma_period=2)
    assert RSI_COLUMN in out.columns


# ---------------------------------------------------------------------------
# TC-19 必須列欠落で KeyError
# ---------------------------------------------------------------------------
def test_build_rsi_raises_keyerror_on_missing_column():
    df = _ohlc().drop(columns=["high"])
    with pytest.raises(KeyError):
        build_rsi(df, rsi_period=3, ma_period=2)


# ---------------------------------------------------------------------------
# TC-20 rsi_levels は生 RSI 由来の σ 水準辞書（7 要素）を返す
# ---------------------------------------------------------------------------
def test_rsi_levels_returns_seven_level_dict_from_raw_rsi():
    df = _ohlc()
    levels = rsi_levels(df, rsi_period=3, apply=0, ma_period=2)
    assert set(levels.keys()) == {"p1", "p2", "p3", "m1", "m2", "m3", "mid50"}
    full = compute_rsi_full(
        df["open"].to_numpy(float), df["high"].to_numpy(float),
        df["low"].to_numpy(float), df["close"].to_numpy(float),
        rsi_period=3, apply=0, ma_period=2,
    )
    # 生 RSI 由来であることを直接固定。
    assert levels == pytest.approx(compute_rsi_levels(full.rsi))
    assert levels == pytest.approx(full.levels)
