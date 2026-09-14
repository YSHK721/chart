"""出力アダプタ（lightweight-charts）の検証 — Fake チャートで描画ライブラリ非依存に確認。

import 規約: sys.path.insert(parents[1]) → from src import ...（ガイド §7）。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.lwc_chart import add_price_range_power  # noqa: E402
from src.ratio import build_bull_bear_profile  # noqa: E402


class FakeLine:
    def __init__(self, price, kwargs):
        self.price = price
        self.kwargs = kwargs


class FakeChart:
    """``horizontal_line`` のみを持つスタブ（lightweight_charts 非依存）。

    シグネチャは本物の ``AbstractChart.horizontal_line`` に一致させる
    （price, color, width, style, text, axis_label_visible, func）。これにより
    アダプタが未対応の引数を渡した場合に TypeError で検出できる。
    """

    def __init__(self):
        self.lines = []

    def horizontal_line(self, price, color="rgb(122, 146, 202)", width=2,
                        style="solid", text="", axis_label_visible=True, func=None):
        kwargs = dict(color=color, width=width, style=style, text=text,
                      axis_label_visible=axis_label_visible, func=func)
        line = FakeLine(price, kwargs)
        self.lines.append(line)
        return line


def _sample_df(n=120, seed=7):
    rng = np.random.default_rng(seed)
    base = 1.10 + np.cumsum(rng.normal(0, 0.01, n))
    o = base
    c = base + rng.normal(0, 0.012, n)
    h = np.maximum(o, c) + rng.uniform(0.0, 0.02, n)
    low = np.minimum(o, c) - rng.uniform(0.0, 0.02, n)
    return pd.DataFrame({"open": o, "high": h, "low": low, "close": c})


def test_returns_lines_and_respects_top_n():
    df = _sample_df()
    chart = FakeChart()
    lines = add_price_range_power(chart, df, interval=0.01, top_n=3)
    assert lines == chart.lines
    bull = [ln for ln in lines if "BULL" in ln.kwargs["text"]]
    bear = [ln for ln in lines if "BEAR" in ln.kwargs["text"]]
    assert len(bull) <= 3
    assert len(bear) <= 3
    assert len(lines) == len(bull) + len(bear)


def test_colors_and_prices_are_band_levels():
    df = _sample_df()
    prof = build_bull_bear_profile(df, interval=0.01)
    valid_prices = set(np.round(prof.index.to_numpy(), 4))
    chart = FakeChart()
    lines = add_price_range_power(chart, df, interval=0.01, top_n=5)
    for ln in lines:
        assert round(ln.price, 4) in valid_prices
        if "BULL" in ln.kwargs["text"]:
            assert "46, 158, 91" in ln.kwargs["color"]      # 緑
        else:
            assert "210, 67, 58" in ln.kwargs["color"]      # 赤


def test_zero_power_bands_excluded():
    df = _sample_df()
    prof = build_bull_bear_profile(df, interval=0.01)
    chart = FakeChart()
    lines = add_price_range_power(chart, df, interval=0.01, top_n=50)
    # 描画された価格帯はすべて勢力 > 0。
    for ln in lines:
        i = int(np.argmin(np.abs(prof.index.to_numpy() - ln.price)))
        if "BULL" in ln.kwargs["text"]:
            assert prof["bull_power"].to_numpy()[i] > 0
            assert prof["net_power"].to_numpy()[i] > 0
        else:
            assert prof["bear_power"].to_numpy()[i] > 0
            assert prof["net_power"].to_numpy()[i] < 0


def test_top_n_zero_draws_nothing():
    df = _sample_df()
    chart = FakeChart()
    lines = add_price_range_power(chart, df, interval=0.01, top_n=0)
    assert lines == []


def test_negative_top_n_raises():
    df = _sample_df()
    with pytest.raises(ValueError):
        add_price_range_power(FakeChart(), df, top_n=-1)
