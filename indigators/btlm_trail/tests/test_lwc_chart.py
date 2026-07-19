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
                           q_low=0.05, q_high=0.95)
    names = {ln.name for ln in chart.lines}
    assert "btlm_trail_mean" in names
    assert "btlm_trail_q5" in names
    assert "btlm_trail_q95" in names
    assert set(lines.keys()) >= {"btlm_trail_mean", "btlm_trail_q5", "btlm_trail_q95"}


def test_series_value_column_matches_line_name_and_drops_nan():
    chart = FakeChart()
    add_btlm_trail(chart, _df(150), source="close", maxbars=100,
                   q_low=0.05, q_high=0.95)
    mean_line = next(ln for ln in chart.lines if ln.name == "btlm_trail_mean")
    assert "time" in mean_line.data.columns
    assert "btlm_trail_mean" in mean_line.data.columns
    # NaN（先頭ウォームアップ）は除外されている。
    assert mean_line.data["btlm_trail_mean"].notna().all()


def test_empirical_method_emits_bands():
    chart = FakeChart()
    add_btlm_trail(chart, _df(300), source="close", maxbars=100,
                   q_low=0.05, q_high=0.95, band_method="empirical",
                   empirical_n=150)
    names = {ln.name for ln in chart.lines}
    assert {"btlm_trail_mean", "btlm_trail_q5", "btlm_trail_q95"} <= names


def test_scalar_q_low_q_high_form_single_pair():
    # UI 由来のスカラ q_low/q_high で単一バンドを構成する（任意分位）。
    chart = FakeChart()
    add_btlm_trail(chart, _df(200), source="close", maxbars=100,
                   q_low=0.10, q_high=0.90)
    names = {ln.name for ln in chart.lines}
    assert {"btlm_trail_mean", "btlm_trail_q10", "btlm_trail_q90"} <= names


def test_invalid_pair_raises():
    # 単一ペア検証（0<q_low<q_high<1）は温存。
    with pytest.raises(ValueError):
        add_btlm_trail(FakeChart(), _df(120), q_low=0.9, q_high=0.1)


def test_invalid_source_raises():
    with pytest.raises(ValueError):
        add_btlm_trail(FakeChart(), _df(100), source="vwap")


# --- 表示層: 既定はドット emit（ドット/ライン切替はスタイルタブ・案A） --------
def test_default_emit_is_dots_with_radius():
    # display_mode param は撤去。adapter は常にドット（サークル）ヒント＋明示半径を既定 emit。
    #   ドット/ライン切替は front のスタイルタブ（applySeriesStyle の display）が描画後に上書きする。
    chart = FakeChart()
    add_btlm_trail(chart, _df(200), source="close", maxbars=100)
    for name in ("btlm_trail_mean", "btlm_trail_q5", "btlm_trail_q95"):
        ln = next(l for l in chart.lines if l.name == name)
        assert ln.kwargs.get("point_markers") is True
        assert ln.kwargs.get("line_visible") is False
        assert ln.kwargs.get("point_markers_radius", 0) >= 3


# --- 外れ値分位ライン（q_out・既定オフ・q_high<q_out<1 のみ有効） ----------
def test_outlier_lines_emitted_only_when_qout_valid():
    # 未入力（None）・無効（q_out<=q_high）は補助線なし。
    for bad in (None, 0.95, 0.90):
        chart = FakeChart()
        add_btlm_trail(chart, _df(200), source="close", maxbars=100,
                       q_low=0.05, q_high=0.95, q_out=bad)
        assert not any(ln.name.startswith("btlm_trail_off_") for ln in chart.lines), f"q_out={bad}"

    chart_on = FakeChart()
    add_btlm_trail(chart_on, _df(200), source="close", maxbars=100,
                   q_low=0.05, q_high=0.95, q_out=0.99)
    names = {ln.name for ln in chart_on.lines}
    assert {"btlm_trail_off_hi", "btlm_trail_off_lo"} <= names


def test_outlier_lines_are_outside_band_edges():
    chart = FakeChart()
    add_btlm_trail(chart, _df(300, seed=3), source="close", maxbars=100,
                   q_low=0.05, q_high=0.95, band_method="ols", q_out=0.99)
    lines = {ln.name: ln for ln in chart.lines}
    off_hi = lines["btlm_trail_off_hi"].data.set_index("time")["btlm_trail_off_hi"]
    hi = lines["btlm_trail_q95"].data.set_index("time")["btlm_trail_q95"]
    common = off_hi.index.intersection(hi.index)
    # 外れ値分位（0.99）はバンド上端（0.95）より外側（大きい）。
    assert (off_hi.loc[common] > hi.loc[common]).all()


# --- 数値表示（β・実現被覆率・残差 σ）を読取欄オーバーレイ系列で供給 -------
def test_metric_series_emitted_invisible_for_readout():
    chart = FakeChart()
    add_btlm_trail(chart, _df(400, seed=5), source="close", maxbars=100,
                   q_low=0.05, q_high=0.95, show_metrics=True, n_cov=250)
    names = {ln.name for ln in chart.lines}
    assert {"btlm_trail_beta", "btlm_trail_sigma", "btlm_trail_band_hit_rate"} <= names
    beta = next(ln for ln in chart.lines if ln.name == "btlm_trail_beta")
    # 読取欄専用: チャート上は不可視（line_visible=False かつ point_markers=False）。
    assert beta.kwargs.get("line_visible") is False
    assert beta.kwargs.get("point_markers") is False
    # 価格軸オートスケールから除外（小値系列がローソクを圧縮しないように）。
    assert beta.kwargs.get("readout_only") is True


def test_metrics_suppressed_when_disabled():
    chart = FakeChart()
    add_btlm_trail(chart, _df(300), source="close", maxbars=100, show_metrics=False)
    assert not any(ln.name.startswith("btlm_trail_beta") for ln in chart.lines)
