"""PRO!fitRSI lightweight-charts 出力アダプタの検証（Fake チャート）。

描画ライブラリ（lightweight_charts）に依存させず、create_line を持つ Fake で本数
（RSI 1 本＋正常帯 2 本＋外れ値水準 4 本）・名前・値・name 一致・異常系（必須列欠落・
時刻欠落）を確認する（PORTING_GUIDE §6/§7）。

RSI は volume 不要（OHLC のみ）。lwc のライン name は値列名（rsi / rsi_q10 / rsi_evq_ext_hi …）
に一致させる（Apply 依存の短名は plot 凡例側の関心事であり lwc line name ではない）。
水準はすべて時系列（line）であり、水平線（horizontal_line）は使わない — 水準が
当該バー除外の因果ローリング分位に基づき時間で動くためである（σ 7 本からの置換）。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import LEVEL_COLUMNS, RSI_COLUMN, quantile_column  # noqa: E402
from src.lwc_chart import add_rsi  # noqa: E402


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

    def create_line(self, name, **kwargs):
        line = FakeLine(name, **kwargs)
        self.lines.append(line)
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
# TC-31 RSI 線 1 本 ＋ 正常帯 2 本 ＋ 外れ値水準 4 本 を生成する
# ---------------------------------------------------------------------------
def test_creates_rsi_line_bands_and_outlier_levels():
    chart = FakeChart()
    created = add_rsi(chart, _df(), rsi_period=6, apply=5, window_n=10, k_events=5)
    assert len(chart.lines) == 1 + 2 + 4
    assert len(created) == 1 + 2 + 4


# ---------------------------------------------------------------------------
# TC-32 ライン名が値列名（rsi / rsi_q10 / rsi_q90 / 水準 4 本）と一致する
# ---------------------------------------------------------------------------
def test_line_names_match_value_columns():
    chart = FakeChart()
    add_rsi(chart, _df(), rsi_period=6, apply=5, window_n=10, k_events=5)
    names = [ln.name for ln in chart.lines]
    assert names == [
        RSI_COLUMN, quantile_column(0.10), quantile_column(0.90),
        LEVEL_COLUMNS["ext_hi"], LEVEL_COLUMNS["ext_lo"],
        LEVEL_COLUMNS["gpd_hi"], LEVEL_COLUMNS["gpd_lo"],
    ]
    for ln in chart.lines:
        assert ln.name in ln.data.columns
        assert "time" in ln.data.columns


# ---------------------------------------------------------------------------
# TC-33 ライン値が build_rsi の出力と一致する（NaN 行は emit 側で除外される）
# ---------------------------------------------------------------------------
def test_line_values_match_build_output():
    from src import build_rsi

    df = _df()
    chart = FakeChart()
    add_rsi(chart, df, rsi_period=6, apply=5, window_n=10, k_events=5)
    built = build_rsi(df, rsi_period=6, apply=5, window_n=10, k_events=5)
    for line in chart.lines:
        expected = built[line.name].to_numpy(dtype=float)
        expected = expected[np.isfinite(expected)]
        got = line.data[line.name].to_numpy(dtype=float)
        assert np.allclose(got, expected)


# ---------------------------------------------------------------------------
# TC-34 水準線は水平線ではなく時系列（因果ローリング＝時間で動く）
# ---------------------------------------------------------------------------
def test_levels_are_time_series_not_horizontal_lines():
    chart = FakeChart()
    add_rsi(chart, _df(), rsi_period=6, apply=5, window_n=10, k_events=5)
    assert not hasattr(chart, "horizontal_line")   # 水平線 API 自体を使わない
    band = next(ln for ln in chart.lines if ln.name == quantile_column(0.90))
    assert band.data[band.name].nunique() > 1      # 時間で動く


# ---------------------------------------------------------------------------
# TC-35 draw_levels=False で水準線を抑止できる（RSI 線のみ）
# ---------------------------------------------------------------------------
def test_levels_can_be_disabled():
    chart = FakeChart()
    add_rsi(chart, _df(), rsi_period=6, apply=5, draw_levels=False)
    assert len(chart.lines) == 1
    assert chart.lines[0].name == RSI_COLUMN


# ---------------------------------------------------------------------------
# TC-36 多数線のため price_line / price_label は False
# ---------------------------------------------------------------------------
def test_line_price_flags_off():
    chart = FakeChart()
    add_rsi(chart, _df(), rsi_period=6, apply=5, draw_levels=False)
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
