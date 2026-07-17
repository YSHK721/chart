"""lightweight-charts 出力アダプタ（profit_rmm）の検証（Fake チャート）。

描画ライブラリ（lightweight_charts）に依存させず、``create_histogram`` /
``horizontal_line`` を持つ Fake チャートで以下を確認する（先例 profit_adx_needle 準拠）::

    1. ヒストグラム 1 本（rmm_lc）と σ6 水準線 6 本が生成される
    2. ヒストグラム名 = 値列名 = LEVEL_COUNT_COLUMN（"rmm_lc"）一致
    3. ヒストグラムの値が build_rmm の level_count と一致
    4. price_line / price_label が False
    5. draw_levels=False で水準線が 0 本
    6. 異常系: 必須列欠落（KeyError）/ volume 欠落（KeyError）/ 時刻解決不可（KeyError）
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # = profit_rmm/

from src import core, rmm  # noqa: E402
from src.lwc_chart import _LEVEL_KEYS, add_rmm  # noqa: E402


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
    rng = np.random.default_rng(11)
    close = 1.1000 + np.cumsum(rng.normal(0, 0.0005, n))
    high = close + np.abs(rng.normal(0, 0.0007, n))
    low = close - np.abs(rng.normal(0, 0.0007, n))
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
            "open": np.roll(close, 1),
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(100, 1000, n).astype(float),
        }
    )


def test_creates_one_histogram_and_six_level_lines():
    chart = FakeChart()
    created = add_rmm(chart, _df(), osc_period=6, ma_period=6)
    assert len(chart.histograms) == 1
    assert len(_LEVEL_KEYS) == 6
    assert len(chart.hlines) == 6
    assert len(created) == 1 + 6


def test_histogram_name_matches_value_column():
    chart = FakeChart()
    add_rmm(chart, _df(), osc_period=6, ma_period=6)
    hist = chart.histograms[0]
    assert hist.name == rmm.LEVEL_COUNT_COLUMN
    assert rmm.LEVEL_COUNT_COLUMN in hist.data.columns
    assert "time" in hist.data.columns


def test_histogram_values_match_build_rmm():
    df = _df()
    chart = FakeChart()
    # 全期間版（window=None）で chart 値と build 出力の一致（配線）を固定。
    add_rmm(chart, df, osc_period=6, ma_period=6, window=None)
    expected = rmm.build_rmm(df, osc_period=6, ma_period=6, window=None)[
        rmm.LEVEL_COUNT_COLUMN
    ].to_numpy()
    got = chart.histograms[0].data[rmm.LEVEL_COUNT_COLUMN].to_numpy()
    np.testing.assert_allclose(got, expected)


def test_histogram_color_column_matches_level_colors():
    # per-bar 色（緑→赤・中心からの距離ベース）が level_colors と一致することを固定する。
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from common_view import level_colors

    df = _df()
    chart = FakeChart()
    add_rmm(chart, df, osc_period=6, ma_period=6)
    data = chart.histograms[0].data
    assert "color" in data.columns
    values = rmm.build_rmm(df, osc_period=6, ma_period=6)[
        rmm.LEVEL_COUNT_COLUMN
    ].to_numpy()
    expected = pd.Series(level_colors(values))[pd.Series(values).notna().to_numpy()]
    assert data["color"].tolist() == expected.tolist()


def test_level_line_values_match_rmm_levels():
    df = _df()
    chart = FakeChart()
    add_rmm(chart, df, osc_period=6, ma_period=6)
    levels = rmm.rmm_levels(df, osc_period=6, ma_period=6)
    prices = {ln["price"] for ln in chart.hlines}
    for key in _LEVEL_KEYS:
        assert float(levels[key]) in prices


def test_price_flags_off():
    chart = FakeChart()
    add_rmm(chart, _df(), osc_period=6, ma_period=6)
    assert chart.histograms[0].kwargs["price_line"] is False
    assert chart.histograms[0].kwargs["price_label"] is False


def test_levels_can_be_disabled():
    chart = FakeChart()
    add_rmm(chart, _df(), osc_period=6, ma_period=6, draw_levels=False)
    assert len(chart.hlines) == 0


def test_time_resolution_from_datetime_index():
    df = _df(40).set_index("time")
    chart = FakeChart()
    add_rmm(chart, df, osc_period=6, ma_period=6)
    assert "time" in chart.histograms[0].data.columns


def test_missing_required_column_raises():
    df = _df()
    df = df.drop(columns=["high"])  # 必須列欠落
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_rmm(chart, df, osc_period=6, ma_period=6)


def test_missing_volume_raises():
    df = _df()
    df = df.drop(columns=["volume"])  # volume 欠落
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_rmm(chart, df, osc_period=6, ma_period=6)


def test_missing_time_raises():
    df = _df().drop(columns=["time"])  # 時刻なし・DatetimeIndex でもない
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_rmm(chart, df, osc_period=6, ma_period=6)
