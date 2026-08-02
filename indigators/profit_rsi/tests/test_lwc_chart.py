"""PRO!fitRSI lightweight-charts 出力アダプタの検証（Fake チャート）。

描画ライブラリ（lightweight_charts）に依存させず、create_line /
horizontal_line を持つ Fake で本数（線 1 本・水平線 7 本）・名前・値・
name 一致・異常系（必須列欠落・時刻欠落）を確認する
（PORTING_GUIDE §6/§7）。テストファースト（Red→Green）。

RSI は volume 不要（OHLC のみ）。lwc のライン name は値列名（rsi）に
一致させる（Apply 依存の短名は plot 凡例側の関心事であり lwc line name ではない）。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import RSI_COLUMN  # noqa: E402
from src.lwc_chart import _LEVEL_KEYS, add_rsi  # noqa: E402


class FakeLine:
    def __init__(self, name, **kwargs):
        self.name = name
        self.kwargs = kwargs
        self.data = None

    def set(self, data):
        self.data = data


class FakeChart:
    def __init__(self):
        self.lines = []
        self.hlines = []

    def create_line(self, name, **kwargs):
        line = FakeLine(name, **kwargs)
        self.lines.append(line)
        return line

    def horizontal_line(self, price, **kwargs):
        line = {"price": price, **kwargs}
        self.hlines.append(line)
        return line


def _df(n=40):
    # 合成 OHLC（volume なし）。RSI に起伏を出すため緩やかな振動を与える。
    base = np.arange(n, dtype=float)
    close = 100.0 + np.sin(base * 0.5) * 5.0 + base * 0.1
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
            "open": close - 0.3,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
        }
    )


# ---------------------------------------------------------------------------
# TC-31 線 1 本（RSI）＋ 水準線 7 本（±1/2/3σ, mid50）を生成する
# ---------------------------------------------------------------------------
def test_creates_one_line_and_seven_level_lines():
    chart = FakeChart()
    created = add_rsi(chart, _df(), rsi_period=6, apply=5)
    assert len(chart.lines) == 1
    assert len(chart.hlines) == 7
    assert len(_LEVEL_KEYS) == 7
    assert len(created) == 1 + 7


# ---------------------------------------------------------------------------
# TC-32 ライン名が値列名（rsi）と一致する
# ---------------------------------------------------------------------------
def test_line_names_match_value_columns():
    chart = FakeChart()
    add_rsi(chart, _df(), rsi_period=6, apply=5)
    names = [ln.name for ln in chart.lines]
    assert names == [RSI_COLUMN]  # EMA 平滑線は持たない（ma_period 削除）
    for ln in chart.lines:
        assert ln.name in ln.data.columns
        assert "time" in ln.data.columns


# ---------------------------------------------------------------------------
# TC-33 ライン値が build_rsi の出力と一致する
# ---------------------------------------------------------------------------
def test_line_values_match_build_output():
    from src import build_rsi

    df = _df()
    chart = FakeChart()
    add_rsi(chart, df, rsi_period=6, apply=5)
    built = build_rsi(df, rsi_period=6, apply=5)
    for col, line in zip((RSI_COLUMN,), chart.lines):
        expected = built[col].to_numpy()
        got = line.data[col].to_numpy()
        assert len(got) == len(df)
        assert np.allclose(got, expected)


# ---------------------------------------------------------------------------
# TC-34 水準線の価格が rsi_levels の 7 水準と一致する
# ---------------------------------------------------------------------------
def test_level_prices_match_rsi_levels():
    from src import rsi_levels

    df = _df()
    chart = FakeChart()
    add_rsi(chart, df, rsi_period=6, apply=5)
    levels = rsi_levels(df, rsi_period=6, apply=5)
    prices = sorted(h["price"] for h in chart.hlines)
    expected = sorted(levels[k] for k in _LEVEL_KEYS)
    assert np.allclose(prices, expected)
    assert any(np.isclose(p, 50.0) for p in prices)  # mid50 が含まれる


# ---------------------------------------------------------------------------
# TC-35 draw_levels=False で水準線を抑止できる
# ---------------------------------------------------------------------------
def test_levels_can_be_disabled():
    chart = FakeChart()
    add_rsi(chart, _df(), rsi_period=6, apply=5, draw_levels=False)
    assert len(chart.hlines) == 0
    assert len(chart.lines) == 1


# ---------------------------------------------------------------------------
# TC-36 多数線のため price_line / price_label は False
# ---------------------------------------------------------------------------
def test_line_price_flags_off():
    chart = FakeChart()
    add_rsi(chart, _df(), rsi_period=6, apply=5)
    for ln in chart.lines:
        assert ln.kwargs["price_line"] is False
        assert ln.kwargs["price_label"] is False


# ---------------------------------------------------------------------------
# TC-37 DatetimeIndex から時刻を解決できる
# ---------------------------------------------------------------------------
def test_time_resolution_from_datetime_index():
    df = _df(30).set_index("time")
    chart = FakeChart()
    add_rsi(chart, df, rsi_period=6, apply=5)
    assert "time" in chart.lines[0].data.columns


# ---------------------------------------------------------------------------
# TC-38 時刻列が解決できないと KeyError
# ---------------------------------------------------------------------------
def test_missing_time_raises():
    n = 30
    base = np.arange(n, dtype=float)
    close = 100.0 + np.sin(base * 0.5) * 5.0
    df = pd.DataFrame(
        {
            "open": close - 0.3,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
        }
    )  # 時刻なし
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_rsi(chart, df, rsi_period=6, apply=5)


# ---------------------------------------------------------------------------
# TC-39 OHLC 列欠落で KeyError
# ---------------------------------------------------------------------------
def test_missing_ohlc_raises():
    n = 30
    df = pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
            "close": np.full(n, 100.0),
        }
    )  # open/high/low なし
    chart = FakeChart()
    with pytest.raises(KeyError):
        add_rsi(chart, df, rsi_period=6, apply=5)
