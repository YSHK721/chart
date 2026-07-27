"""PRO!fit_Oscillator lightweight-charts 出力アダプタの検証（Fake チャート）。

描画ライブラリ（lightweight_charts）に依存させず、create_histogram /
horizontal_line を持つ Fake で本数（ヒスト 1 本・水平線 12 本）・name 一致・値・
σ12 水準線（12 本）・異常系（必須列欠落・volume 欠落・時刻欠落）を確認する
（PORTING_GUIDE §6/§7。Arctan と同型の σ12＝上下各 6 本＝計 12 本）。
テストファースト（Red→Green）。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import LEVEL_COUNT_COLUMN  # noqa: E402
from src.lwc_chart import _LEVEL_KEYS, add_oscillator  # noqa: E402


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


def _df(n=80):
    # 合成 OHLCV（volume 列必須＝iMFI が出来高を要する）。
    rng = np.random.default_rng(11)
    base = np.cumsum(rng.normal(0, 0.5, n)) + 100.0
    close = base + rng.normal(0, 0.1, n)
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.2, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.2, n))
    volume = np.abs(rng.normal(1000, 100, n))
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
# σ12: 上方 6 本 + 下方 6 本 = 12 本
# ---------------------------------------------------------------------------
def test_level_keys_count_is_twelve():
    assert len(_LEVEL_KEYS) == 12
    assert _LEVEL_KEYS[:6] == ("up_067", "up_128", "up_165", "up_196", "up_258", "up_329")
    assert _LEVEL_KEYS[6:] == ("dn_067", "dn_128", "dn_165", "dn_196", "dn_258", "dn_329")


# ---------------------------------------------------------------------------
# ヒスト 1 本 ＋ 水準線 12 本（σ12）を生成する
# ---------------------------------------------------------------------------
def test_creates_one_histogram_and_twelve_level_lines():
    chart = FakeChart()
    created = add_oscillator(chart, _df(), period_a=6, period_b=60)
    assert len(chart.histograms) == 1
    assert len(chart.hlines) == 12
    assert len(created) == 1 + 12


# ---------------------------------------------------------------------------
# ヒストグラム名が値列名（oscillator_lc）と一致する
# ---------------------------------------------------------------------------
def test_histogram_name_matches_value_column():
    chart = FakeChart()
    add_oscillator(chart, _df(), period_a=6, period_b=60)
    hist = chart.histograms[0]
    assert hist.name == LEVEL_COUNT_COLUMN
    assert LEVEL_COUNT_COLUMN in hist.data.columns
    assert "time" in hist.data.columns


# ---------------------------------------------------------------------------
# ヒストグラム値が build_oscillator の出力と一致する
# ---------------------------------------------------------------------------
def test_histogram_values_match_build_output():
    from src import build_oscillator

    df = _df()
    chart = FakeChart()
    # 全期間版（window=None）で chart 値と build 出力の一致（配線）を固定。
    add_oscillator(chart, df, period_a=6, period_b=60, window=None)
    built = build_oscillator(df, period_a=6, period_b=60, window=None)
    expected = built[LEVEL_COUNT_COLUMN].to_numpy()
    got = chart.histograms[0].data[LEVEL_COUNT_COLUMN].to_numpy()
    assert len(got) == len(df)
    assert np.allclose(got, expected)


# ---------------------------------------------------------------------------
# 水準線の価格が oscillator_levels の σ12 と一致する
# ---------------------------------------------------------------------------
def test_histogram_color_column_matches_level_colors():
    # per-bar 色（緑→赤・中心からの距離ベース）が level_colors と一致することを固定する。
    from common_view import level_colors

    from src import build_oscillator

    df = _df()
    chart = FakeChart()
    add_oscillator(chart, df, period_a=6, period_b=60)
    data = chart.histograms[0].data
    assert "color" in data.columns
    values = build_oscillator(df, period_a=6, period_b=60)[LEVEL_COUNT_COLUMN].to_numpy()
    expected = pd.Series(level_colors(values))[pd.Series(values).notna().to_numpy()]
    assert data["color"].tolist() == expected.tolist()


def test_horizontal_line_prices_match_levels():
    from src import oscillator_levels

    df = _df()
    levels = oscillator_levels(df, period_a=6, period_b=60)
    chart = FakeChart()
    add_oscillator(chart, df, period_a=6, period_b=60)
    drawn = {line["text"]: line["price"] for line in chart.hlines}
    for key in _LEVEL_KEYS:
        assert drawn[key] == pytest.approx(float(levels[key]))


# ---------------------------------------------------------------------------
# draw_levels=False で水準線を抑止できる
# ---------------------------------------------------------------------------
def test_levels_can_be_disabled():
    chart = FakeChart()
    add_oscillator(chart, _df(), period_a=6, period_b=60, draw_levels=False)
    assert len(chart.hlines) == 0
    assert len(chart.histograms) == 1


# ---------------------------------------------------------------------------
# price_line / price_label は False
# ---------------------------------------------------------------------------
def test_histogram_price_flags_off():
    chart = FakeChart()
    add_oscillator(chart, _df(), period_a=6, period_b=60)
    assert chart.histograms[0].kwargs["price_line"] is False
    assert chart.histograms[0].kwargs["price_label"] is False


# ---------------------------------------------------------------------------
# DatetimeIndex から時刻を解決できる
# ---------------------------------------------------------------------------
def test_time_resolution_from_datetime_index():
    df = _df(50).set_index("time")
    chart = FakeChart()
    add_oscillator(chart, df, period_a=6, period_b=60)
    assert "time" in chart.histograms[0].data.columns


# ---------------------------------------------------------------------------
# 必須列（OHLC）欠落で KeyError
# ---------------------------------------------------------------------------
def test_missing_required_column_raises():
    df = _df(50).drop(columns=["open"])  # open 欠落（OHLCV 整合のため必須）
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_oscillator(chart, df, period_a=6, period_b=60)


# ---------------------------------------------------------------------------
# volume 列欠落で KeyError（iMFI 固有の必須列）
# ---------------------------------------------------------------------------
def test_missing_volume_raises():
    df = _df(50).drop(columns=["volume"])  # volume 欠落
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_oscillator(chart, df, period_a=6, period_b=60)


# ---------------------------------------------------------------------------
# 時刻列が解決できないと KeyError
# ---------------------------------------------------------------------------
def test_missing_time_raises():
    df = _df(50).drop(columns=["time"]).reset_index(drop=True)  # 時刻なし
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_oscillator(chart, df, period_a=6, period_b=60)
