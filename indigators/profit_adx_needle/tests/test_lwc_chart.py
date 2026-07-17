"""lightweight-charts 出力アダプタの検証（Fake チャート）。

描画ライブラリに依存させず、create_histogram / horizontal_line を持つ Fake で
本数・名前・値・水準線・異常系を確認する（ガイド §7）。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import NEEDLE_COLUMN  # noqa: E402
from src.lwc_chart import _LEVEL_KEYS, add_adx_needle  # noqa: E402


class FakeHistogram:
    def __init__(self, name, **kwargs):
        self.name = name
        self.kwargs = kwargs
        self.data = None

    def set(self, data):
        self.data = data


class FakeChart:
    def __init__(self):
        self.histograms = []
        self.hlines = []

    def create_histogram(self, name, **kwargs):
        h = FakeHistogram(name, **kwargs)
        self.histograms.append(h)
        return h

    def horizontal_line(self, price, **kwargs):
        line = {"price": price, **kwargs}
        self.hlines.append(line)
        return line


def _df(n=60):
    h = np.arange(n, dtype=float) + 10.0
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
            "high": h,
            "low": h - 1.0,
            "close": h - 0.5,
        }
    )


def test_creates_one_histogram_and_level_lines():
    chart = FakeChart()
    created = add_adx_needle(chart, _df(), period=6)
    assert len(chart.histograms) == 1
    assert len(chart.hlines) == len(_LEVEL_KEYS)
    assert len(created) == 1 + len(_LEVEL_KEYS)


def test_histogram_name_matches_value_column():
    chart = FakeChart()
    add_adx_needle(chart, _df(), period=6)
    hist = chart.histograms[0]
    assert hist.name == NEEDLE_COLUMN
    assert NEEDLE_COLUMN in hist.data.columns
    assert "time" in hist.data.columns


def test_histogram_color_column_matches_level_colors():
    # per-bar 色（緑→赤・中心からの距離ベース）が level_colors と一致することを固定する。
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from common_view import level_colors

    from src import build_adx_needle

    df = _df()
    chart = FakeChart()
    add_adx_needle(chart, df, period=6)
    data = chart.histograms[0].data
    assert "color" in data.columns
    values = build_adx_needle(df, period=6)[NEEDLE_COLUMN].to_numpy()
    expected = pd.Series(level_colors(values))[pd.Series(values).notna().to_numpy()]
    assert data["color"].tolist() == expected.tolist()


def test_levels_can_be_disabled():
    chart = FakeChart()
    add_adx_needle(chart, _df(), period=6, draw_levels=False)
    assert len(chart.hlines) == 0


def test_histogram_price_flags_off():
    chart = FakeChart()
    add_adx_needle(chart, _df(), period=6)
    assert chart.histograms[0].kwargs["price_line"] is False
    assert chart.histograms[0].kwargs["price_label"] is False


def test_time_resolution_from_datetime_index():
    df = _df(40).set_index("time")
    chart = FakeChart()
    add_adx_needle(chart, df, period=6)
    assert "time" in chart.histograms[0].data.columns


def test_missing_time_raises():
    h = np.arange(40, dtype=float) + 10.0
    df = pd.DataFrame({"high": h, "low": h - 1.0, "close": h - 0.5})  # 時刻なし
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_adx_needle(chart, df, period=6)
