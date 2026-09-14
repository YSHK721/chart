"""lightweight-charts 出力アダプタの検証（Fake チャート）。

描画ライブラリに依存させず、create_histogram / horizontal_line を持つ Fake で
ヒストグラム本数・名前・値・σ12 水準線（12 本）・異常系を確認する
（ガイド §7。Arctan は ADX_NEEDLE と異なり上下 σ 各 6 本＝計 12 本）。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import LEVEL_COUNT_COLUMN  # noqa: E402
from src.lwc_chart import _LEVEL_KEYS, add_arctan  # noqa: E402


from indigators.testing.lwc_fakes import FakeChart, FakeHistogram  # noqa: E402


def _df(n=80):
    base = np.linspace(1.1000, 1.1100, n)
    wiggle = 0.0008 * np.sin(np.linspace(0, 7, n))
    close = base + wiggle
    high = close + 0.0006
    low = close - 0.0006
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
        }
    )


def test_level_keys_count_is_twelve():
    # σ12: 上方 6 本 + 下方 6 本 = 12 本。
    assert len(_LEVEL_KEYS) == 12
    assert _LEVEL_KEYS[:6] == ("up_067", "up_128", "up_165", "up_196", "up_258", "up_329")
    assert _LEVEL_KEYS[6:] == ("dn_067", "dn_128", "dn_165", "dn_196", "dn_258", "dn_329")


def test_creates_one_histogram_and_twelve_level_lines():
    chart = FakeChart()
    created = add_arctan(chart, _df(), period=6, ma_method=1, bar_width=0.1)
    assert len(chart.histograms) == 1
    assert len(chart.hlines) == 12
    assert len(created) == 1 + 12


def test_histogram_name_matches_value_column():
    chart = FakeChart()
    add_arctan(chart, _df(), period=6)
    hist = chart.histograms[0]
    assert hist.name == LEVEL_COUNT_COLUMN
    assert LEVEL_COUNT_COLUMN in hist.data.columns
    assert "time" in hist.data.columns


def test_histogram_color_column_matches_level_colors():
    # per-bar 色（緑→赤・中心からの距離ベース）が level_colors と一致することを固定する。
    from common_view import level_colors

    from src import build_arctan

    df = _df()
    chart = FakeChart()
    add_arctan(chart, df, period=6, ma_method=1, bar_width=0.1)
    data = chart.histograms[0].data
    assert "color" in data.columns
    values = build_arctan(
        df, period=6, ma_method=1, bar_width=0.1
    )[LEVEL_COUNT_COLUMN].to_numpy()
    expected = pd.Series(level_colors(values))[pd.Series(values).notna().to_numpy()]
    assert data["color"].tolist() == expected.tolist()


def test_horizontal_line_prices_match_levels():
    from src import arctan_levels

    df = _df()
    levels = arctan_levels(df, period=6, ma_method=1, bar_width=0.1)
    chart = FakeChart()
    add_arctan(chart, df, period=6, ma_method=1, bar_width=0.1)
    drawn = {line["text"]: line["price"] for line in chart.hlines}
    for key in _LEVEL_KEYS:
        assert drawn[key] == pytest.approx(float(levels[key]))


def test_levels_can_be_disabled():
    chart = FakeChart()
    add_arctan(chart, _df(), period=6, draw_levels=False)
    assert len(chart.hlines) == 0


def test_histogram_price_flags_off():
    chart = FakeChart()
    add_arctan(chart, _df(), period=6)
    assert chart.histograms[0].kwargs["price_line"] is False
    assert chart.histograms[0].kwargs["price_label"] is False


def test_time_resolution_from_datetime_index():
    df = _df(50).set_index("time")
    chart = FakeChart()
    add_arctan(chart, df, period=6)
    assert "time" in chart.histograms[0].data.columns


def test_missing_required_column_raises():
    # open 欠落（iARCTAN は open も使用するため必須）。
    df = _df(50).drop(columns=["open"])
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_arctan(chart, df, period=6)


def test_missing_time_raises():
    n = 50
    close = np.linspace(1.10, 1.11, n)
    df = pd.DataFrame(
        {
            "open": np.roll(close, 1),
            "high": close + 0.0006,
            "low": close - 0.0006,
            "close": close,
        }
    )  # 時刻なし
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_arctan(chart, df, period=6)
