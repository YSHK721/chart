"""btlm_trail_marod 出力アダプタ（add_btlm_trail_marod）の単体テスト（TDD）。

別 pane オシレータ: line 系列 'btlm_trail_marod' ＋ 0% 水平基準線を emit する
（lightweight_charts は import せず duck typing）。NaN（warm-up）は描画から除外する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_PKG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PKG_DIR))

from src import marod_series  # noqa: E402
from src.lwc_chart import add_btlm_trail_marod  # noqa: E402


class _FakeLine:
    def __init__(self, name, **kwargs):
        self.name = name
        self.kwargs = kwargs
        self.data = None

    def set(self, df):
        self.data = df


class _FakeChart:
    def __init__(self):
        self.lines = []
        self.hlines = []

    def create_line(self, name, **kwargs):
        line = _FakeLine(name, **kwargs)
        self.lines.append(line)
        return line

    def horizontal_line(self, price, **kwargs):
        hl = {"price": price, **kwargs}
        self.hlines.append(hl)
        return hl


def _ohlc(n: int = 200, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    prices = np.cumsum(rng.normal(0, 1, n)) + 100.0
    return pd.DataFrame({
        "time": pd.date_range("2020-01-01", periods=n, freq="D"),
        "open": prices, "high": prices + 1.0, "low": prices - 1.0, "close": prices + 0.25,
    })


def test_add_emits_line_series_named_marod():
    chart = _FakeChart()
    add_btlm_trail_marod(chart, _ohlc(200), source="close", maxbars=100)
    names = {ln.name for ln in chart.lines}
    assert "btlm_trail_marod" in names


def test_add_emits_zero_baseline_horizontal_line():
    chart = _FakeChart()
    add_btlm_trail_marod(chart, _ohlc(200))
    prices = [hl["price"] for hl in chart.hlines]
    assert 0.0 in prices
    assert len(chart.hlines) == 1  # 0% 基準線のみ（±閾値線は対象外）


def test_line_values_match_marod_series_with_nan_dropped():
    df = _ohlc(180, seed=5)
    chart = _FakeChart()
    add_btlm_trail_marod(chart, df, source="close", maxbars=80)
    line = next(ln for ln in chart.lines if ln.name == "btlm_trail_marod")
    values = marod_series(df, source="close", maxbars=80)
    finite = values[np.isfinite(values)]
    # NaN（warm-up）は emit されない。
    assert len(line.data) == finite.size
    np.testing.assert_allclose(
        line.data["btlm_trail_marod"].to_numpy(), finite, rtol=1e-12, atol=1e-12,
    )
    assert not line.data["btlm_trail_marod"].isna().any()


def test_line_color_is_configurable():
    chart = _FakeChart()
    add_btlm_trail_marod(chart, _ohlc(120), color="rgba(1, 2, 3, 1)")
    line = next(ln for ln in chart.lines if ln.name == "btlm_trail_marod")
    assert line.kwargs.get("color") == "rgba(1, 2, 3, 1)"
