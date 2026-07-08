"""PRO!fitRSIMACD lightweight-charts 出力アダプタの検証（Fake チャート）。

描画ライブラリ（lightweight_charts）に依存させず、create_histogram /
create_line / horizontal_line を持つ Fake で、本数（ヒスト 1 本・線 2 本・
水平線 7 本）・名前・値・name↔値列一致・異常系（必須列欠落・時刻欠落）を
確認する（PORTING_GUIDE §6/§7）。RSIMACD は iRSI 起点（価格 Typical）のため
出来高（volume）は不要＝OHLC のみで成立する。テストファースト（Red→Green）。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import HIST_COLUMN, MACD_COLUMN, SIGNAL_COLUMN, build_rsimacd  # noqa: E402
from src.lwc_chart import (  # noqa: E402
    MACD_LINE_NAME,
    SIGNAL_LINE_NAME,
    _LEVEL_KEYS,
    add_rsimacd,
)

_KW = dict(rsi_period=3, fast=4, slow=8, signal=4)


class FakeSeries:
    def __init__(self, kind, name, **kwargs):
        self.kind = kind
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
        s = FakeSeries("histogram", name, **kwargs)
        self.histograms.append(s)
        return s

    def create_line(self, name, **kwargs):
        s = FakeSeries("line", name, **kwargs)
        self.lines.append(s)
        return s

    def horizontal_line(self, price, **kwargs):
        line = {"price": price, **kwargs}
        self.hlines.append(line)
        return line


def _df(n=40):
    # 単調増加の合成 OHLC（volume 列は不要＝RSIMACD は価格 Typical 起点）。
    h = np.arange(n, dtype=float) + 10.0
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
            "high": h,
            "low": h - 1.0,
            "close": h - 0.5,
        }
    )


# ---------------------------------------------------------------------------
# TC-L1 ヒスト 1 本・線 2 本・水準線 7 本を生成する
# ---------------------------------------------------------------------------
def test_creates_one_histogram_two_lines_seven_level_lines():
    chart = FakeChart()
    created = add_rsimacd(chart, _df(), **_KW)
    assert len(chart.histograms) == 1
    assert len(chart.lines) == 2
    assert len(chart.hlines) == 7
    assert len(_LEVEL_KEYS) == 7
    assert len(created) == 1 + 2 + 7


# ---------------------------------------------------------------------------
# TC-L2 ヒストグラム名が値列名（rsimacd_hist）と一致する
# ---------------------------------------------------------------------------
def test_histogram_name_matches_value_column():
    chart = FakeChart()
    add_rsimacd(chart, _df(), **_KW)
    hist = chart.histograms[0]
    assert hist.name == HIST_COLUMN
    assert HIST_COLUMN in hist.data.columns
    assert "time" in hist.data.columns


# ---------------------------------------------------------------------------
# TC-L3 ライン名（RSIMACD / Signal）が値列名と一致する
# ---------------------------------------------------------------------------
def test_line_names_match_value_columns():
    chart = FakeChart()
    add_rsimacd(chart, _df(), **_KW)
    names = [ln.name for ln in chart.lines]
    assert names == [MACD_LINE_NAME, SIGNAL_LINE_NAME]
    for ln in chart.lines:
        # 値列名はライン name と完全一致（§5）。
        assert ln.name in ln.data.columns
        assert "time" in ln.data.columns


# ---------------------------------------------------------------------------
# TC-L4 系列値が build_rsimacd の出力と一致する
# ---------------------------------------------------------------------------
def test_series_values_match_build_output():
    df = _df()
    chart = FakeChart()
    add_rsimacd(chart, df, **_KW)
    built = build_rsimacd(df, **_KW)

    # ヒストグラム値（rsimacd_hist）。
    hist = chart.histograms[0]
    assert len(hist.data) == len(df)
    np.testing.assert_allclose(
        hist.data[HIST_COLUMN].to_numpy(), built[HIST_COLUMN].to_numpy()
    )
    # ライン値（macd→RSIMACD, signal→Signal にマップ）。
    expected_cols = (MACD_COLUMN, SIGNAL_COLUMN)
    for value_col, line in zip(expected_cols, chart.lines):
        got = line.data[line.name].to_numpy()
        assert len(got) == len(df)
        np.testing.assert_allclose(got, built[value_col].to_numpy())


# ---------------------------------------------------------------------------
# TC-L5 水準線の価格が rsimacd_levels の 7 水準と一致する
# ---------------------------------------------------------------------------
def test_level_prices_match_levels():
    from src import rsimacd_levels

    df = _df()
    chart = FakeChart()
    add_rsimacd(chart, df, **_KW)
    levels = rsimacd_levels(df, **_KW)
    prices = sorted(h["price"] for h in chart.hlines)
    expected = sorted(levels[k] for k in _LEVEL_KEYS)
    np.testing.assert_allclose(prices, expected)
    assert any(np.isclose(p, 50.0) for p in prices)  # mid50 が含まれる


# ---------------------------------------------------------------------------
# TC-L6 draw_levels=False で水準線を抑止できる
# ---------------------------------------------------------------------------
def test_levels_can_be_disabled():
    chart = FakeChart()
    add_rsimacd(chart, _df(), **_KW, draw_levels=False)
    assert len(chart.hlines) == 0
    assert len(chart.histograms) == 1
    assert len(chart.lines) == 2


# ---------------------------------------------------------------------------
# TC-L7 多数系列のため price_line / price_label は False（§6）
# ---------------------------------------------------------------------------
def test_series_price_flags_off():
    chart = FakeChart()
    add_rsimacd(chart, _df(), **_KW)
    for s in (*chart.histograms, *chart.lines):
        assert s.kwargs["price_line"] is False
        assert s.kwargs["price_label"] is False


# ---------------------------------------------------------------------------
# TC-L8 DatetimeIndex から時刻を解決できる
# ---------------------------------------------------------------------------
def test_time_resolution_from_datetime_index():
    df = _df(30).set_index("time")
    chart = FakeChart()
    add_rsimacd(chart, df, **_KW)
    assert "time" in chart.histograms[0].data.columns
    assert "time" in chart.lines[0].data.columns


# ---------------------------------------------------------------------------
# TC-L9 時刻列が解決できないと KeyError
# ---------------------------------------------------------------------------
def test_missing_time_raises():
    n = 30
    h = np.arange(n, dtype=float) + 10.0
    df = pd.DataFrame(
        {
            "high": h,
            "low": h - 1.0,
            "close": h - 0.5,
        }
    )  # 時刻なし
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_rsimacd(chart, df, **_KW)


# ---------------------------------------------------------------------------
# TC-L10 HLC 列欠落で KeyError
# ---------------------------------------------------------------------------
def test_missing_hlc_raises():
    n = 30
    df = pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
        }
    )  # HLC なし
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_rsimacd(chart, df, **_KW)
