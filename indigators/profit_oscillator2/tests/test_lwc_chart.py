"""PRO!fitOscillator lightweight-charts 出力アダプタの検証（Fake チャート）。

描画ライブラリ（lightweight_charts）に依存させず、create_histogram /
create_line / horizontal_line を持つ Fake で本数（ヒスト 1 本・線 1 本・
水平線 6 本）・名前・値・name 一致・price フラグ・異常系（必須列欠落・
volume 欠落・時刻欠落）を確認する（PORTING_GUIDE §6/§7）。

テストファースト（Red→Green）: 本ファイルは src/lwc_chart.py 実装前に作成し、
add_oscillator2 未実装による失敗（ImportError）を Red として確認した。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import LEVEL_COUNT_COLUMN, RCI_COLUMN  # noqa: E402
from src.lwc_chart import _LEVEL_KEYS, add_oscillator2  # noqa: E402


class FakeHistogram:
    def __init__(self, name, **kwargs):
        self.name = name
        self.kwargs = kwargs
        self.data = None

    def set(self, data):
        self.data = data


class FakeLine:
    def __init__(self, name, **kwargs):
        self.name = name
        self.kwargs = kwargs
        self.data = None

    def set(self, data):
        self.data = data


class FakeChart:
    def __init__(self):
        self.histograms = []
        self.lines = []
        self.hlines = []

    def create_histogram(self, name, **kwargs):
        hist = FakeHistogram(name, **kwargs)
        self.histograms.append(hist)
        return hist

    def create_line(self, name, **kwargs):
        line = FakeLine(name, **kwargs)
        self.lines.append(line)
        return line

    def horizontal_line(self, price, **kwargs):
        line = {"price": price, **kwargs}
        self.hlines.append(line)
        return line


def _df(n=80, seed=11):
    rng = np.random.default_rng(seed)
    close = np.cumsum(rng.normal(0.0, 1.0, n)) + 100.0
    high = close + rng.uniform(0.2, 1.5, n)
    low = close - rng.uniform(0.2, 1.5, n)
    open_ = close + rng.normal(0.0, 0.3, n)
    volume = rng.uniform(100.0, 1000.0, n)
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


# ---------------------------------------------------------------------------
# TC-31 ヒスト 1 本（LC）＋ 線 1 本（RCI）＋ 水準線 6 本（σ6）を生成する
# ---------------------------------------------------------------------------
def test_creates_one_histogram_one_line_and_six_level_lines():
    chart = FakeChart()
    created = add_oscillator2(chart, _df())
    assert len(chart.histograms) == 1
    assert len(chart.lines) == 1
    assert len(chart.hlines) == 6
    assert len(_LEVEL_KEYS) == 6
    assert len(created) == 1 + 1 + 6


# ---------------------------------------------------------------------------
# TC-32 ヒスト名 / 線名が値列名（oscillator2_lc / oscillator2_rci）と一致する
# ---------------------------------------------------------------------------
def test_series_names_match_value_columns():
    chart = FakeChart()
    add_oscillator2(chart, _df())
    hist = chart.histograms[0]
    line = chart.lines[0]
    assert hist.name == LEVEL_COUNT_COLUMN
    assert line.name == RCI_COLUMN
    assert LEVEL_COUNT_COLUMN in hist.data.columns
    assert "time" in hist.data.columns
    assert RCI_COLUMN in line.data.columns
    assert "time" in line.data.columns


# ---------------------------------------------------------------------------
# TC-33 ヒスト値 / 線値が build_oscillator2 の出力と一致する
# ---------------------------------------------------------------------------
def test_series_values_match_build_output():
    from src import build_oscillator2

    df = _df()
    chart = FakeChart()
    add_oscillator2(chart, df)
    built = build_oscillator2(df)
    hist = chart.histograms[0]
    line = chart.lines[0]
    assert np.allclose(hist.data[LEVEL_COUNT_COLUMN].to_numpy(),
                       built[LEVEL_COUNT_COLUMN].to_numpy())
    assert np.allclose(line.data[RCI_COLUMN].to_numpy(),
                       built[RCI_COLUMN].to_numpy())
    assert len(hist.data) == len(df)
    assert len(line.data) == len(df)


# ---------------------------------------------------------------------------
# TC-33b ヒストの color 列が level_colors（緑→赤・中心からの距離ベース）と一致する
# ---------------------------------------------------------------------------
def test_histogram_color_column_matches_level_colors():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from common import level_colors

    from src import build_oscillator2

    df = _df()
    chart = FakeChart()
    add_oscillator2(chart, df)
    data = chart.histograms[0].data
    assert "color" in data.columns
    values = build_oscillator2(df)[LEVEL_COUNT_COLUMN].to_numpy()
    assert data["color"].tolist() == level_colors(values)


# ---------------------------------------------------------------------------
# TC-34 水準線の価格が oscillator2_levels の σ6（up/dn 各 3 本）と一致する
# ---------------------------------------------------------------------------
def test_level_prices_match_sigma6_levels():
    from src import oscillator2_levels

    df = _df()
    chart = FakeChart()
    add_oscillator2(chart, df)
    levels = oscillator2_levels(df)
    prices = sorted(h["price"] for h in chart.hlines)
    expected = sorted(levels[k] for k in _LEVEL_KEYS)
    assert np.allclose(prices, expected)


# ---------------------------------------------------------------------------
# TC-35 draw_levels=False で水準線を抑止できる
# ---------------------------------------------------------------------------
def test_levels_can_be_disabled():
    chart = FakeChart()
    add_oscillator2(chart, _df(), draw_levels=False)
    assert len(chart.hlines) == 0
    assert len(chart.histograms) == 1
    assert len(chart.lines) == 1


# ---------------------------------------------------------------------------
# TC-36 多数系列のため price_line / price_label は False
# ---------------------------------------------------------------------------
def test_series_price_flags_off():
    chart = FakeChart()
    add_oscillator2(chart, _df())
    for series in (chart.histograms[0], chart.lines[0]):
        assert series.kwargs["price_line"] is False
        assert series.kwargs["price_label"] is False


# ---------------------------------------------------------------------------
# TC-37 DatetimeIndex から時刻を解決できる
# ---------------------------------------------------------------------------
def test_time_resolution_from_datetime_index():
    df = _df(60).set_index("time")
    chart = FakeChart()
    add_oscillator2(chart, df)
    assert "time" in chart.histograms[0].data.columns
    assert "time" in chart.lines[0].data.columns


# ---------------------------------------------------------------------------
# TC-38 時刻列が解決できないと KeyError
# ---------------------------------------------------------------------------
def test_missing_time_raises():
    df = _df(40).drop(columns=["time"]).reset_index(drop=True)
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_oscillator2(chart, df)


# ---------------------------------------------------------------------------
# TC-39 HLC 列欠落で KeyError
# ---------------------------------------------------------------------------
def test_missing_hlc_raises():
    df = _df(40).drop(columns=["high"])
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_oscillator2(chart, df)


# ---------------------------------------------------------------------------
# TC-40 volume 列欠落で KeyError（Oscillator2 固有の必須列）
# ---------------------------------------------------------------------------
def test_missing_volume_raises():
    df = _df(40).drop(columns=["volume"])
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_oscillator2(chart, df)
