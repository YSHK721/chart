"""ma_marod 出力アダプタ（add_ma_marod）の単体テスト（TDD）。

別 pane オシレータ: line 系列 'ma_marod' ＋ 0% 水平基準線を emit する
（lightweight_charts は import せず duck typing）。NaN（warm-up）は描画から除外する。
btlm_trail_marod の test_lwc_chart と対称。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_PKG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PKG_DIR))

from src import (  # noqa: E402
    ma_marod_outlier_event_quantiles,
    ma_marod_quantile_bands,
    ma_marod_series,
)
from src.lwc_chart import add_ma_marod  # noqa: E402


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


def test_add_emits_line_series_named_ma_marod():
    chart = _FakeChart()
    add_ma_marod(chart, _ohlc(200), source="close", ma_type="ema", length=50)
    names = {ln.name for ln in chart.lines}
    assert "ma_marod" in names


def test_add_emits_zero_baseline_horizontal_line():
    chart = _FakeChart()
    add_ma_marod(chart, _ohlc(200))
    prices = [hl["price"] for hl in chart.hlines]
    assert 0.0 in prices
    assert len(chart.hlines) == 1  # 0% 基準線のみ（±閾値線は対象外）


def test_line_values_match_ma_marod_series_with_nan_dropped():
    df = _ohlc(180, seed=5)
    chart = _FakeChart()
    add_ma_marod(chart, df, source="close", ma_type="sma", length=80)
    line = next(ln for ln in chart.lines if ln.name == "ma_marod")
    values = ma_marod_series(df, source="close", ma_type="sma", length=80)
    finite = values[np.isfinite(values)]
    # NaN（warm-up）は emit されない。
    assert len(line.data) == finite.size
    np.testing.assert_allclose(
        line.data["ma_marod"].to_numpy(), finite, rtol=1e-12, atol=1e-12,
    )
    assert not line.data["ma_marod"].isna().any()


def test_line_color_is_configurable():
    chart = _FakeChart()
    add_ma_marod(chart, _ohlc(120), color="rgba(1, 2, 3, 1)")
    line = next(ln for ln in chart.lines if ln.name == "ma_marod")
    assert line.kwargs.get("color") == "rgba(1, 2, 3, 1)"


# --- 分位バンド・イベント分位水準線の emit -------------------------------------

def test_add_emits_quantile_band_and_event_quantile_series():
    chart = _FakeChart()
    add_ma_marod(chart, _ohlc(400, seed=7), q_low=0.05, q_high=0.95, window_n=200)
    names = {ln.name for ln in chart.lines}
    # 既定 ON: MA_MAROD line ＋ 正常バンド（q5/q95）＋ イベント分位水準線 4 本。
    #   σ バンド・全履歴（_all）系列は描画廃止（認知負荷削減・ユーザー裁定 2026-07-21）。
    assert names == {
        "ma_marod",
        "ma_marod_q5", "ma_marod_q95",
        "ma_marod_evq_med_hi", "ma_marod_evq_med_lo",
        "ma_marod_evq_ext_hi", "ma_marod_evq_ext_lo",
    }


def test_quantile_band_series_values_match_core_nan_dropped():
    df = _ohlc(400, seed=11)
    chart = _FakeChart()
    add_ma_marod(chart, df, q_low=0.05, q_high=0.95, window_n=180)
    m = ma_marod_series(df)
    lo, hi = ma_marod_quantile_bands(m, window_n=180, q_low=0.05, q_high=0.95)
    line_lo = next(ln for ln in chart.lines if ln.name == "ma_marod_q5")
    np.testing.assert_allclose(
        line_lo.data["ma_marod_q5"].to_numpy(), lo[np.isfinite(lo)], rtol=1e-12, atol=1e-12,
    )
    assert not line_lo.data["ma_marod_q5"].isna().any()
    line_hi = next(ln for ln in chart.lines if ln.name == "ma_marod_q95")
    np.testing.assert_allclose(
        line_hi.data["ma_marod_q95"].to_numpy(), hi[np.isfinite(hi)], rtol=1e-12, atol=1e-12,
    )


def test_event_quantile_series_values_match_core_nan_dropped():
    # イベント分位水準線は core（ma_marod_outlier_event_quantiles）と数値一致・NaN 除外。
    df = _ohlc(500, seed=11)
    chart = _FakeChart()
    add_ma_marod(chart, df, q_low=0.05, q_high=0.95, q_out=0.99, k_events=10, window_n=180)
    m = ma_marod_series(df)
    evq = ma_marod_outlier_event_quantiles(
        m, window_n=180, q_low=0.05, q_high=0.95, q_out=0.99, k_events=10,
    )
    for key in ("med_hi", "med_lo", "ext_hi"):
        name = f"ma_marod_evq_{key}"
        line = next(ln for ln in chart.lines if ln.name == name)
        exp = evq[key]
        np.testing.assert_allclose(
            line.data[name].to_numpy(), exp[np.isfinite(exp)], rtol=1e-12, atol=1e-12,
        )
        assert not line.data[name].isna().any()


def test_event_quantile_ext_series_empty_when_q_out_invalid():
    # q_out 無効（None・q_out<=q_high）は極端線（ext_*）が空データ・中央値線は残る。
    for q_out in (None, 0.90):
        chart = _FakeChart()
        add_ma_marod(chart, _ohlc(500, seed=3), q_high=0.95, q_out=q_out, k_events=10, window_n=150)
        ext = next(ln for ln in chart.lines if ln.name == "ma_marod_evq_ext_hi")
        assert len(ext.data) == 0
        med = next(ln for ln in chart.lines if ln.name == "ma_marod_evq_med_hi")
        assert len(med.data) > 0


def test_event_quantile_series_styles_and_colors():
    # イベント分位水準線＝赤系固定色。中央値＝実線・極端分位＝破線（ユーザー裁定）。
    chart = _FakeChart()
    add_ma_marod(chart, _ohlc(500, seed=7), k_events=10, window_n=150)
    by_name = {ln.name: ln for ln in chart.lines}
    assert by_name["ma_marod_evq_med_hi"].kwargs.get("color") == "rgba(210, 67, 58, 1)"
    assert by_name["ma_marod_evq_med_hi"].kwargs.get("style") == "solid"
    assert by_name["ma_marod_evq_ext_hi"].kwargs.get("style") == "dashed"


def test_quantile_series_name_follows_q_params():
    # 系列名は分位に追随（例 q_low=0.1 -> q10・q_high=0.9 -> q90）。
    chart = _FakeChart()
    add_ma_marod(chart, _ohlc(300, seed=3), q_low=0.10, q_high=0.90, window_n=150)
    names = {ln.name for ln in chart.lines}
    assert "ma_marod_q10" in names
    assert "ma_marod_q90" in names


def test_ma_type_changes_series_values():
    # ma_type がアダプタ経由で core に伝播する（sma と ema で系列が異なる）。
    df = _ohlc(200, seed=23)
    c1, c2 = _FakeChart(), _FakeChart()
    add_ma_marod(c1, df, ma_type="sma", length=30)
    add_ma_marod(c2, df, ma_type="ema", length=30)
    v1 = next(ln for ln in c1.lines if ln.name == "ma_marod").data["ma_marod"].to_numpy()
    v2 = next(ln for ln in c2.lines if ln.name == "ma_marod").data["ma_marod"].to_numpy()
    assert v1.size != v2.size or not np.allclose(v1, v2)
