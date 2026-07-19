"""btlm_trail の結線検証（call_binding _TABLE 経由の end-to-end invoke）。

新インジケーター btlm_trail が既存指標と同一の結線様式（CallBinding.resolve→invoke）で
系列を収集できることを固定する（loader が add_btlm_trail を実接続でロード・実行）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from adapter.compute import CallBinding, FakeLineChart, IndicatorComputeAdapter
from adapter.compute.catalog_schema import PARAM_DEFAULTS


def _ohlcv(n: int = 200, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    prices = np.cumsum(rng.normal(0, 1, n)) + 100.0
    return pd.DataFrame({
        "time": pd.date_range("2020-01-01", periods=n, freq="D"),
        "open": prices, "high": prices + 1.0, "low": prices - 1.0, "close": prices + 0.25,
    })


def test_btlm_trail_registered_in_table_and_schema():
    binding = CallBinding.resolve("btlm_trail", "default")
    assert binding.output_kind == "line"
    assert "btlm_trail" in PARAM_DEFAULTS


def test_btlm_trail_invoke_emits_mean_and_band_series():
    chart = FakeLineChart()
    binding = CallBinding.resolve("btlm_trail", "default")
    binding.invoke(chart, _ohlcv(200), {
        "source": "close", "maxbars": 100, "q_low": 0.05, "q_high": 0.95,
        "band_method": "ols", "empirical_n": 500,
    })
    names = {p["name"] for p in chart.to_payloads()}
    assert {"btlm_trail_mean", "btlm_trail_q5", "btlm_trail_q95"} <= names


def test_btlm_trail_invoke_synthetic_source_and_empirical_method():
    chart = FakeLineChart()
    binding = CallBinding.resolve("btlm_trail", "default")
    binding.invoke(chart, _ohlcv(300), {
        "source": "hl2", "maxbars": 100, "q_low": 0.05, "q_high": 0.95,
        "band_method": "empirical", "empirical_n": 150,
    })
    names = {p["name"] for p in chart.to_payloads()}
    assert {"btlm_trail_mean", "btlm_trail_q5", "btlm_trail_q95"} <= names


def test_btlm_trail_payload_carries_display_and_extra_series():
    # 表示層ヒント（point_markers/line_visible）と拡張系列（offset/ma/metrics）が payload に載る。
    chart = FakeLineChart()
    binding = CallBinding.resolve("btlm_trail", "default")
    binding.invoke(chart, _ohlcv(300), {
        "source": "close", "maxbars": 100, "q_low": 0.05, "q_high": 0.95,
        "display_mode": "dots", "offset_pct": 2.5,
        "ma_reference": True, "ma_type": "sma", "ma_length": 21,
        "show_metrics": True, "n_cov": 250,
    })
    payloads = {p["name"]: p for p in chart.to_payloads()}
    # ドット既定: mean payload に描画ヒントが載る（表示層が lwc オプションへ写像）。
    assert payloads["btlm_trail_mean"]["point_markers"] is True
    assert payloads["btlm_trail_mean"]["line_visible"] is False
    # 拡張系列。
    assert {"btlm_trail_off_hi", "btlm_trail_off_lo", "btlm_trail_ma",
            "btlm_trail_beta", "btlm_trail_sigma", "btlm_trail_coverage"} <= set(payloads)


def test_btlm_trail_payload_no_hints_when_absent():
    # 既存指標（ヒント未付与）の payload は従来どおり（point_markers/line_visible を持たない）。
    from adapter.compute.fake_chart import FakeLineChart as FLC
    chart = FLC()
    chart.create_line(name="X", color="red", width=1, style="solid")
    p = chart.to_payloads()[0]
    assert "point_markers" not in p and "line_visible" not in p


# --- 実 runtime 経路（IndicatorComputeAdapter → 統合 FakeChart → to_payloads）の end-to-end ---
#   実 UI/`/compute` と同一経路。単体（FakeLineChart 直叩き）ではなく本経路でヒント伝搬を固定する。
def test_runtime_compute_propagates_display_hints_end_to_end():
    adapter = IndicatorComputeAdapter()
    series = adapter.compute("btlm_trail", "default", _ohlcv(300), {
        "source": "close", "maxbars": 100, "q_low": 0.05, "q_high": 0.95,
        "display_mode": "dots", "band_method": "ols", "empirical_n": 500,
        "show_metrics": True, "n_cov": 250,
    })
    by_name = {s["name"]: s for s in series}
    # ドット既定: mean/q5/q95 に描画ヒントが載る（実応答に反映される）。
    for name in ("btlm_trail_mean", "btlm_trail_q5", "btlm_trail_q95"):
        assert by_name[name]["point_markers"] is True, f"{name} に point_markers が無い"
        assert by_name[name]["line_visible"] is False
        assert by_name[name]["point_markers_radius"] >= 3
    # 数値読取系列は価格軸除外ヒント（readout_only）付き。
    for name in ("btlm_trail_beta", "btlm_trail_sigma", "btlm_trail_coverage"):
        assert by_name[name]["readout_only"] is True, f"{name} に readout_only が無い"
        assert by_name[name]["line_visible"] is False


def test_runtime_compute_line_mode_hints_end_to_end():
    adapter = IndicatorComputeAdapter()
    series = adapter.compute("btlm_trail", "default", _ohlcv(300), {
        "source": "close", "maxbars": 100, "q_low": 0.05, "q_high": 0.95,
        "display_mode": "line",
    })
    mean = next(s for s in series if s["name"] == "btlm_trail_mean")
    assert mean["line_visible"] is True
    assert mean["point_markers"] is False
    assert "point_markers_radius" not in mean  # ライン時は半径ヒント無し
