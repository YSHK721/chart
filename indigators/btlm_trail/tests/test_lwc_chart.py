"""btlm_trail 出力アダプタ（add_btlm_trail）の検証（TDD）。

duck typing の FakeChart で系列収集を固定する（描画ライブラリ非依存・ガイド §2/§6）。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_PKG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PKG_DIR))

from src.lwc_chart import add_btlm_trail  # noqa: E402


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


def _df(n=200, seed=0):
    rng = np.random.default_rng(seed)
    prices = np.cumsum(rng.normal(0, 1, n)) + 100.0
    times = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "time": times, "open": prices, "high": prices + 0.5,
        "low": prices - 0.5, "close": prices + 0.1,
    })


def test_emits_mean_and_band_lines():
    chart = FakeChart()
    lines = add_btlm_trail(chart, _df(200), source="close", maxbars=100,
                           quantile_pairs=[(0.05, 0.95)])
    names = {ln.name for ln in chart.lines}
    assert "btlm_trail_mean" in names
    assert "btlm_trail_q5" in names
    assert "btlm_trail_q95" in names
    assert set(lines.keys()) >= {"btlm_trail_mean", "btlm_trail_q5", "btlm_trail_q95"}


def test_multiple_pairs_emit_multiple_bands():
    chart = FakeChart()
    add_btlm_trail(chart, _df(200), source="close", maxbars=100,
                   quantile_pairs=[(0.05, 0.95), (0.25, 0.75)])
    names = {ln.name for ln in chart.lines}
    assert {"btlm_trail_q5", "btlm_trail_q95", "btlm_trail_q25", "btlm_trail_q75"} <= names


def test_series_value_column_matches_line_name_and_drops_nan():
    chart = FakeChart()
    add_btlm_trail(chart, _df(150), source="close", maxbars=100,
                   quantile_pairs=[(0.05, 0.95)])
    mean_line = next(ln for ln in chart.lines if ln.name == "btlm_trail_mean")
    assert "time" in mean_line.data.columns
    assert "btlm_trail_mean" in mean_line.data.columns
    # NaN（先頭ウォームアップ）は除外されている。
    assert mean_line.data["btlm_trail_mean"].notna().all()


def test_empirical_method_emits_bands():
    chart = FakeChart()
    add_btlm_trail(chart, _df(300), source="close", maxbars=100,
                   quantile_pairs=[(0.05, 0.95)], band_method="empirical",
                   empirical_n=150)
    names = {ln.name for ln in chart.lines}
    assert {"btlm_trail_mean", "btlm_trail_q5", "btlm_trail_q95"} <= names


def test_scalar_q_low_q_high_form_single_pair():
    # UI 由来のスカラ q_low/q_high 経路（quantile_pairs 未指定）で単一バンドを構成する。
    chart = FakeChart()
    add_btlm_trail(chart, _df(200), source="close", maxbars=100,
                   q_low=0.10, q_high=0.90)
    names = {ln.name for ln in chart.lines}
    assert {"btlm_trail_mean", "btlm_trail_q10", "btlm_trail_q90"} <= names


def test_invalid_source_raises():
    with pytest.raises(ValueError):
        add_btlm_trail(FakeChart(), _df(100), source="vwap")
