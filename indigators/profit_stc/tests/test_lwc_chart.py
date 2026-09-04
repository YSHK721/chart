"""lightweight-charts 出力アダプタの検証（Fake チャート）。

描画ライブラリに依存させず、create_line / horizontal_line を持つ Fake で
本数（線1本・水平線4本）・名前・値・name 一致・異常系を確認する（ガイド §7）。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import OSC_COLUMN  # noqa: E402
from src.lwc_chart import _LEVEL_KEYS, add_stc  # noqa: E402


from indigators.testing.lwc_fakes import FakeChart, FakeLine  # noqa: E402


def _df(n=90):
    h = np.arange(n, dtype=float) + 10.0
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
            "high": h,
            "low": h - 1.0,
            "close": h - 0.5,
        }
    )


def test_creates_one_line_and_four_level_lines():
    chart = FakeChart()
    created = add_stc(chart, _df(), period=70)
    assert len(chart.lines) == 1
    assert len(chart.hlines) == 4
    assert len(_LEVEL_KEYS) == 4
    assert len(created) == 1 + 4


def test_line_name_matches_value_column():
    chart = FakeChart()
    add_stc(chart, _df(), period=70)
    line = chart.lines[0]
    assert line.name == OSC_COLUMN
    assert OSC_COLUMN in line.data.columns
    assert "time" in line.data.columns


def test_line_values_match_build_output():
    from src import build_stc

    df = _df()
    chart = FakeChart()
    add_stc(chart, df, period=70)
    expected = build_stc(df, period=70)[OSC_COLUMN].to_numpy()
    got = chart.lines[0].data[OSC_COLUMN].to_numpy()
    # warm-up は 0 で描画される（NaN 無し＝全バー残る）。
    assert len(got) == len(df)
    assert np.allclose(got, expected)


def test_level_prices_match_stc_levels():
    from src import stc_levels

    df = _df()
    chart = FakeChart()
    add_stc(chart, df, period=70)
    levels = stc_levels(df, period=70)
    prices = sorted(h["price"] for h in chart.hlines)
    expected = sorted(levels[k] for k in _LEVEL_KEYS)
    assert np.allclose(prices, expected)


def test_levels_can_be_disabled():
    chart = FakeChart()
    add_stc(chart, _df(), period=70, draw_levels=False)
    assert len(chart.hlines) == 0


def test_line_price_flags_off():
    chart = FakeChart()
    add_stc(chart, _df(), period=70)
    assert chart.lines[0].kwargs["price_line"] is False
    assert chart.lines[0].kwargs["price_label"] is False


def test_time_resolution_from_datetime_index():
    df = _df(80).set_index("time")
    chart = FakeChart()
    add_stc(chart, df, period=70)
    assert "time" in chart.lines[0].data.columns


def test_missing_time_raises():
    h = np.arange(80, dtype=float) + 10.0
    df = pd.DataFrame({"high": h, "low": h - 1.0, "close": h - 0.5})  # 時刻なし
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_stc(chart, df, period=70)


def test_missing_hlc_raises():
    n = 80
    df = pd.DataFrame(
        {"time": pd.date_range("2024-01-01", periods=n, freq="h")}  # HLC なし
    )
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_stc(chart, df, period=70)
