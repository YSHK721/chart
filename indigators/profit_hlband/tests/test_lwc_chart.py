"""lightweight-charts 出力アダプタの検証（Fake チャート / サブチャート）。

描画ライブラリ（lightweight_charts）に依存させず、create_histogram /
horizontal_line / create_line を持つ Fake で二系統出力を確認する（ガイド §7）:
  * separate: ヒストグラム 1 本（name=hl_range）＋水平線 4 本（avg/b165/b196/b258）
    ＋ subwindow MIN=0 / MAX=b196*2。
  * overlay: メイン chart に水平線 8 本（high_*/low_*）。
異常系: 必須列（high/low）欠落・時刻列解決不可で KeyError。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import RANGE_COLUMN  # noqa: E402
from src.lwc_chart import (  # noqa: E402
    _LEVEL_KEYS,
    _OVERLAY_KEYS,
    add_hlband_overlay,
    add_hlband_separate,
)


from indigators.testing.lwc_fakes import FakeChart, FakeHistogram, FakeLine  # noqa: E402


def _df(n=12):
    rng = np.arange(n, dtype=float)
    high = 10.0 + rng + np.sin(rng)
    low = high - (1.0 + 0.5 * np.cos(rng))
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
            "high": high,
            "low": low,
        }
    )


# --- separate（別ウィンドウ: ヒストグラム＋水平4本） -----------------------


def test_separate_creates_one_histogram_and_four_level_lines():
    chart = FakeChart()
    created = add_hlband_separate(chart, _df())
    assert len(chart.histograms) == 1
    assert len(chart.hlines) == 4
    assert len(_LEVEL_KEYS) == 4
    assert len(created) == 1 + 4


def test_separate_histogram_name_matches_range_column():
    chart = FakeChart()
    add_hlband_separate(chart, _df())
    hist = chart.histograms[0]
    assert hist.name == RANGE_COLUMN
    assert RANGE_COLUMN in hist.data.columns
    assert "time" in hist.data.columns


def test_separate_histogram_values_match_build_output():
    from src import build_hlband

    df = _df()
    chart = FakeChart()
    add_hlband_separate(chart, df)
    expected = build_hlband(df)[RANGE_COLUMN].to_numpy()
    got = chart.histograms[0].data[RANGE_COLUMN].to_numpy()
    assert len(got) == len(df)
    assert np.allclose(got, expected)


def test_separate_level_prices_match_hlband_levels():
    from src import hlband_levels

    df = _df()
    chart = FakeChart()
    add_hlband_separate(chart, df)
    levels = hlband_levels(df)
    prices = sorted(h["price"] for h in chart.hlines)
    expected = sorted(levels[k] for k in _LEVEL_KEYS)
    assert np.allclose(prices, expected)


def test_separate_levels_can_be_disabled():
    chart = FakeChart()
    add_hlband_separate(chart, _df(), draw_levels=False)
    assert len(chart.hlines) == 0


def test_separate_subwindow_min_max_available_via_levels():
    from src import hlband_levels

    df = _df()
    # subwindow 範囲（MIN=0 / MAX=b196*2）は hlband_levels で提供し、呼び出し側が
    # subchart のスケール設定に用いる（lightweight_charts 実 API を侵さない設計。
    # create_histogram に独自 kwargs を渡さない）。
    levels = hlband_levels(df)
    assert levels["sub_min"] == 0.0
    assert levels["sub_max"] == pytest.approx(levels["b196"] * 2)


def test_separate_histogram_kwargs_do_not_include_subwindow():
    # 実 lightweight_charts の create_histogram は sub_min/sub_max を受けないため、
    # アダプタが独自 kwargs を渡さないことを固定する（TypeError 回避）。
    chart = FakeChart()
    add_hlband_separate(chart, _df())
    assert "sub_min" not in chart.histograms[0].kwargs
    assert "sub_max" not in chart.histograms[0].kwargs


def test_separate_histogram_price_flags_off():
    chart = FakeChart()
    add_hlband_separate(chart, _df())
    assert chart.histograms[0].kwargs["price_line"] is False
    assert chart.histograms[0].kwargs["price_label"] is False


def test_separate_time_resolution_from_datetime_index():
    df = _df().set_index("time")
    chart = FakeChart()
    add_hlband_separate(chart, df)
    assert "time" in chart.histograms[0].data.columns


def test_separate_missing_hl_raises():
    df = pd.DataFrame({"time": pd.date_range("2024-01-01", periods=5, freq="h")})
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_hlband_separate(chart, df)


def test_separate_missing_time_raises():
    n = 6
    high = np.arange(n, dtype=float) + 10.0
    df = pd.DataFrame({"high": high, "low": high - 1.0})  # 時刻なし
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_hlband_separate(chart, df)


# --- overlay（メインチャート: 水平8本） -----------------------------------


def test_overlay_creates_eight_horizontal_lines():
    chart = FakeChart()
    created = add_hlband_overlay(chart, _df())
    assert len(chart.hlines) == 8
    assert len(_OVERLAY_KEYS) == 8
    assert len(created) == 8


def test_overlay_prices_match_price_bands():
    from src import hlband_price_bands

    df = _df()
    chart = FakeChart()
    add_hlband_overlay(chart, df)
    bands = hlband_price_bands(df)
    prices = sorted(h["price"] for h in chart.hlines)
    expected = sorted(bands[k] for k in _OVERLAY_KEYS)
    assert np.allclose(prices, expected)


def test_overlay_lines_have_axis_label_off():
    # 実 lightweight_charts の horizontal_line は price_line/price_label を受けない
    # （ISSUE-008）。軸ラベル抑制は axis_label_visible=False で行う。
    chart = FakeChart()
    add_hlband_overlay(chart, _df())
    for h in chart.hlines:
        assert h["axis_label_visible"] is False
        assert "price_line" not in h and "price_label" not in h


def test_overlay_missing_hl_raises():
    df = pd.DataFrame({"close": np.arange(5, dtype=float)})
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_hlband_overlay(chart, df)
