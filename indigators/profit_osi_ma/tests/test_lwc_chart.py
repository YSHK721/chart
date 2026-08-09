"""lightweight-charts 出力アダプタの検証（Fake チャート）。

描画ライブラリに依存させず、create_histogram / horizontal_line を持つ Fake で
本数・名前・値（dropna 後）・水準線・列名一致・異常系を確認する（ガイド §7）。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import KAIRI_COLUMN, build_osi_ma  # noqa: E402
from src.lwc_chart import _LEVEL_VALUES, add_osi_ma  # noqa: E402


from indigators.testing.lwc_fakes import FakeChart, FakeHistogram  # noqa: E402


def _df(n=60, col="close"):
    c = np.arange(n, dtype=float) + 10.0
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
            col: c,
        }
    )


# --------------------------------------------------------------------------- 系列本数 + 水準線
def test_creates_one_histogram_and_four_level_lines():
    chart = FakeChart()
    created = add_osi_ma(chart, _df(), ma_mode=0, ma_period=3)
    assert len(chart.histograms) == 1
    assert len(chart.hlines) == len(_LEVEL_VALUES) == 4
    assert len(created) == 1 + len(_LEVEL_VALUES)


# --------------------------------------------------------------------------- 水準線の値（1/0.5/-0.5/-1）
def test_level_values_are_1_05_m05_m1():
    chart = FakeChart()
    add_osi_ma(chart, _df(), ma_mode=0, ma_period=3)
    prices = sorted(line["price"] for line in chart.hlines)
    assert prices == [-1.0, -0.5, 0.5, 1.0]


# --------------------------------------------------------------------------- 列名一致
def test_histogram_name_matches_value_column():
    chart = FakeChart()
    add_osi_ma(chart, _df(), ma_mode=0, ma_period=3)
    hist = chart.histograms[0]
    assert hist.name == KAIRI_COLUMN
    assert KAIRI_COLUMN in hist.data.columns
    assert "time" in hist.data.columns


# --------------------------------------------------------------------------- 値（dropna 後）
def test_histogram_data_is_dropna_of_build():
    chart = FakeChart()
    df = _df(n=8)
    add_osi_ma(chart, df, ma_mode=0, ma_period=3)
    expected = build_osi_ma(df, ma_mode=0, ma_period=3)[KAIRI_COLUMN].dropna()
    data = chart.histograms[0].data
    # NaN（最古バー・MA 未確定）は除外され、行数は dropna 後と一致。
    assert len(data) == len(expected)
    np.testing.assert_allclose(
        data[KAIRI_COLUMN].to_numpy(), expected.to_numpy()
    )


# --------------------------------------------------------------------------- 水準線無効化
def test_levels_can_be_disabled():
    chart = FakeChart()
    add_osi_ma(chart, _df(), ma_mode=0, ma_period=3, draw_levels=False)
    assert len(chart.hlines) == 0


# --------------------------------------------------------------------------- price フラグ off
def test_histogram_price_flags_off():
    chart = FakeChart()
    add_osi_ma(chart, _df(), ma_mode=0, ma_period=3)
    assert chart.histograms[0].kwargs["price_line"] is False
    assert chart.histograms[0].kwargs["price_label"] is False


# --------------------------------------------------------------------------- 時刻解決（DatetimeIndex）
def test_time_resolution_from_datetime_index():
    df = _df(40).set_index("time")
    chart = FakeChart()
    add_osi_ma(chart, df, ma_mode=0, ma_period=3)
    assert "time" in chart.histograms[0].data.columns


# --------------------------------------------------------------------------- 異常系: 時刻列欠落
def test_missing_time_raises():
    c = np.arange(40, dtype=float) + 10.0
    df = pd.DataFrame({"close": c})  # 時刻なし
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_osi_ma(chart, df, ma_mode=0, ma_period=3)


# --------------------------------------------------------------------------- 異常系: close 列欠落
def test_missing_close_raises():
    df = pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=40, freq="h"),
            "open": np.arange(40, dtype=float),
        }
    )
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_osi_ma(chart, df, ma_mode=0, ma_period=3)
