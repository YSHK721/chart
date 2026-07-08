"""lightweight-charts 出力アダプタの検証（Fake チャート）。

描画ライブラリ（lightweight_charts）に依存させず、``create_histogram`` /
``horizontal_line`` を持つ Fake チャートで、ヒストグラム 1 本・名前と値列名の一致・
σ12 水準線（12 本）・price フラグ・時刻解決・異常系（必須列欠落 / 時刻欠落）を確認する
（profit_arctan/tests/test_lwc_chart.py を踏襲。Volatility は σ12＝上下各 6 本＝12 本）。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import LEVEL_COUNT_COLUMN  # noqa: E402
from src.lwc_chart import _LEVEL_KEYS, add_volatility  # noqa: E402


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


def _df(n=250, seed=3):  # 因果窓（既定 120）で有効点が出る長さ。
    rng = np.random.default_rng(seed)
    o = rng.uniform(100, 110, n)
    h = o + rng.uniform(0.5, 2.0, n)
    low = o - rng.uniform(0.5, 2.0, n)
    c = low + rng.uniform(0.0, (h - low))
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
            "open": o,
            "high": h,
            "low": low,
            "close": c,
        }
    )


def test_level_keys_count_is_twelve():
    # σ12: 上方 6 本 + 下方 6 本 = 12 本。
    assert len(_LEVEL_KEYS) == 12
    assert _LEVEL_KEYS[:6] == ("up_067", "up_128", "up_165", "up_196", "up_258", "up_329")
    assert _LEVEL_KEYS[6:] == ("dn_067", "dn_128", "dn_165", "dn_196", "dn_258", "dn_329")


def test_creates_one_histogram_and_twelve_level_lines():
    chart = FakeChart()
    created = add_volatility(chart, _df(), period=6)
    assert len(chart.histograms) == 1
    assert len(chart.hlines) == 12
    assert len(created) == 1 + 12


def test_histogram_name_matches_value_column():
    chart = FakeChart()
    add_volatility(chart, _df(), period=6)
    hist = chart.histograms[0]
    assert hist.name == LEVEL_COUNT_COLUMN
    assert LEVEL_COUNT_COLUMN in hist.data.columns
    assert "time" in hist.data.columns


def test_histogram_values_match_build_volatility():
    from src import build_volatility

    df = _df()
    bands = build_volatility(df, period=6)
    chart = FakeChart()
    add_volatility(chart, df, period=6)
    values = chart.histograms[0].data[LEVEL_COUNT_COLUMN].to_numpy()
    # warm-up（先頭 period 本）は NaN で描画側 dropna により除外されるため、参照も NaN を除く。
    expected = bands[LEVEL_COUNT_COLUMN].dropna().to_numpy()
    np.testing.assert_allclose(values, expected, rtol=0, atol=0)


def test_histogram_color_column_matches_level_colors():
    # per-bar 色（緑→赤・中心からの距離ベース）が level_colors と一致することを固定する。
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from common import level_colors

    from src import build_volatility

    df = _df()
    chart = FakeChart()
    add_volatility(chart, df, period=6)
    data = chart.histograms[0].data
    assert "color" in data.columns
    values = build_volatility(df, period=6)[LEVEL_COUNT_COLUMN].to_numpy()
    expected = pd.Series(level_colors(values))[pd.Series(values).notna().to_numpy()]
    assert data["color"].tolist() == expected.tolist()


def test_horizontal_line_prices_match_levels():
    from src import volatility_levels

    df = _df()
    levels = volatility_levels(df, period=6)
    chart = FakeChart()
    add_volatility(chart, df, period=6)
    drawn = {line["text"]: line["price"] for line in chart.hlines}
    for key in _LEVEL_KEYS:
        assert drawn[key] == pytest.approx(float(levels[key]))


def test_levels_can_be_disabled():
    chart = FakeChart()
    add_volatility(chart, _df(), period=6, draw_levels=False)
    assert len(chart.hlines) == 0


def test_histogram_price_flags_off():
    chart = FakeChart()
    add_volatility(chart, _df(), period=6)
    assert chart.histograms[0].kwargs["price_line"] is False
    assert chart.histograms[0].kwargs["price_label"] is False


def test_time_resolution_from_datetime_index():
    df = _df(250).set_index("time")
    chart = FakeChart()
    add_volatility(chart, df, period=6)
    assert "time" in chart.histograms[0].data.columns


def test_missing_required_column_raises():
    # open 欠落（iVOLATILITY は open も使用するため必須）。
    df = _df(50).drop(columns=["open"])
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_volatility(chart, df, period=6)


def test_missing_time_raises():
    n = 50
    rng = np.random.default_rng(1)
    o = rng.uniform(100, 110, n)
    h = o + 1.0
    low = o - 1.0
    c = o + 0.2
    df = pd.DataFrame({"open": o, "high": h, "low": low, "close": c})  # 時刻なし
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_volatility(chart, df, period=6)
