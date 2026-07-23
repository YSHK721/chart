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

from src import (  # noqa: E402
    marod_outlier_event_quantiles,
    marod_quantile_bands,
    marod_series,
)
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


# --- 分位バンド・イベント分位水準線の emit ---------------------------------------

def test_add_emits_quantile_band_and_event_quantile_series():
    chart = _FakeChart()
    add_btlm_trail_marod(chart, _ohlc(400, seed=7), q_low=0.05, q_high=0.95, window_n=200)
    names = {ln.name for ln in chart.lines}
    # 既定 ON: MAROD line ＋ 正常バンド（q5/q95）＋ イベント分位水準線 4 本（ma_marod と対称）。
    #   σ バンドは描画廃止（認知負荷削減・ユーザー裁定 2026-07-21）。
    assert names == {
        "btlm_trail_marod",
        "btlm_trail_marod_q5", "btlm_trail_marod_q95",
        "btlm_trail_marod_evq_med_hi", "btlm_trail_marod_evq_med_lo",
        "btlm_trail_marod_evq_ext_hi", "btlm_trail_marod_evq_ext_lo",
    }


def test_quantile_band_series_values_match_core_nan_dropped():
    df = _ohlc(400, seed=11)
    chart = _FakeChart()
    add_btlm_trail_marod(chart, df, q_low=0.05, q_high=0.95, window_n=180)
    m = marod_series(df, source="close", maxbars=100)
    lo, hi = marod_quantile_bands(m, window_n=180, q_low=0.05, q_high=0.95)
    line_lo = next(ln for ln in chart.lines if ln.name == "btlm_trail_marod_q5")
    np.testing.assert_allclose(
        line_lo.data["btlm_trail_marod_q5"].to_numpy(), lo[np.isfinite(lo)], rtol=1e-12, atol=1e-12,
    )
    assert not line_lo.data["btlm_trail_marod_q5"].isna().any()
    line_hi = next(ln for ln in chart.lines if ln.name == "btlm_trail_marod_q95")
    np.testing.assert_allclose(
        line_hi.data["btlm_trail_marod_q95"].to_numpy(), hi[np.isfinite(hi)], rtol=1e-12, atol=1e-12,
    )


def test_event_quantile_series_values_match_core_nan_dropped():
    # イベント分位水準線は core（marod_outlier_event_quantiles）と数値一致・NaN 除外・赤系固定色。
    df = _ohlc(500, seed=17)
    chart = _FakeChart()
    add_btlm_trail_marod(chart, df, q_out=0.99, k_events=10, window_n=150)
    m = marod_series(df, source="close", maxbars=100)
    evq = marod_outlier_event_quantiles(m, window_n=150, q_out=0.99, k_events=10)
    for key in ("med_hi", "med_lo", "ext_hi"):
        name = f"btlm_trail_marod_evq_{key}"
        line = next(ln for ln in chart.lines if ln.name == name)
        exp = evq[key]
        np.testing.assert_allclose(
            line.data[name].to_numpy(), exp[np.isfinite(exp)], rtol=1e-12, atol=1e-12,
        )
        assert not line.data[name].isna().any()
    med = next(ln for ln in chart.lines if ln.name == "btlm_trail_marod_evq_med_hi")
    assert med.kwargs.get("color") == "rgba(210, 67, 58, 1)"
    assert med.kwargs.get("style") == "solid"
    ext = next(ln for ln in chart.lines if ln.name == "btlm_trail_marod_evq_ext_hi")
    assert ext.kwargs.get("style") == "dashed"


def test_event_quantile_ext_series_empty_when_q_out_invalid():
    # q_out 無効（None・q_out<=q_high）は極端線（ext_*）が空データ・中央値線は残る（黙って無効化）。
    for q_out in (None, 0.90):
        chart = _FakeChart()
        add_btlm_trail_marod(chart, _ohlc(500, seed=3), q_high=0.95, q_out=q_out, k_events=10, window_n=150)
        ext = next(ln for ln in chart.lines if ln.name == "btlm_trail_marod_evq_ext_hi")
        assert len(ext.data) == 0
        med = next(ln for ln in chart.lines if ln.name == "btlm_trail_marod_evq_med_hi")
        assert len(med.data) > 0


def test_quantile_series_name_follows_q_params():
    # 系列名は分位に追随（例 q_low=0.1 -> q10・q_high=0.9 -> q90）。
    chart = _FakeChart()
    add_btlm_trail_marod(chart, _ohlc(300, seed=3), q_low=0.10, q_high=0.90, window_n=150)
    names = {ln.name for ln in chart.lines}
    assert "btlm_trail_marod_q10" in names
    assert "btlm_trail_marod_q90" in names
