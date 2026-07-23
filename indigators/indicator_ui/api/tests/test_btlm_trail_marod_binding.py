"""btlm_trail_marod の結線検証（call_binding _TABLE 経由の end-to-end invoke）。

新指標 btlm_trail_marod（MAROD＝移動平均乖離率・別 pane オシレータ）が既存指標と同一の
結線様式（CallBinding.resolve→invoke / IndicatorComputeAdapter.compute）で系列を収集
できることを固定する（loader が add_btlm_trail_marod を実接続でロード・実行し、btlm_trail
core を importlib 動的ロードで参照する経路を通す）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from adapter.compute import CallBinding, FakeChart, IndicatorComputeAdapter
from adapter.compute.catalog_schema import PARAM_DEFAULTS


def _ohlc(n: int = 200, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    prices = np.cumsum(rng.normal(0, 1, n)) + 100.0
    return pd.DataFrame({
        "time": pd.date_range("2020-01-01", periods=n, freq="D"),
        "open": prices, "high": prices + 1.0, "low": prices - 1.0, "close": prices + 0.25,
    })


def test_marod_registered_in_table_and_schema():
    binding = CallBinding.resolve("btlm_trail_marod", "default")
    assert binding.output_kind == "line"
    assert "btlm_trail_marod" in PARAM_DEFAULTS
    assert set(PARAM_DEFAULTS["btlm_trail_marod"]) == {
        "source", "maxbars", "q_low", "q_high", "q_out", "k_events", "event_agg",
        "window_n", "color",
    }
    # 外れ値イベント分位の既定は ma_marod と対称（共有プリミティブ common.event_quantiles が正）。
    assert PARAM_DEFAULTS["btlm_trail_marod"]["q_out"] == 0.99
    assert PARAM_DEFAULTS["btlm_trail_marod"]["k_events"] == 50
    assert PARAM_DEFAULTS["btlm_trail_marod"]["event_agg"] == "episode"


def test_marod_invoke_emits_quantile_band_and_event_quantile_levels():
    # 実 runtime 経路: MAROD line ＋ 0% 基準線に加え、正常バンド＋イベント分位水準線 4 本を
    #   emit する。σ バンドは描画廃止（認知負荷削減・ユーザー裁定 2026-07-21）。
    chart = FakeChart(name="btlm_trail_marod")
    binding = CallBinding.resolve("btlm_trail_marod", "default")
    binding.invoke(chart, _ohlc(400, seed=7), {
        "source": "close", "maxbars": 100, "q_low": 0.05, "q_high": 0.95,
        "q_out": 0.99, "k_events": 10, "event_agg": "episode", "window_n": 200,
    })
    line_names = {p["name"] for p in chart.to_payloads() if p["kind"] == "line"}
    assert {
        "btlm_trail_marod",
        "btlm_trail_marod_q5", "btlm_trail_marod_q95",
        "btlm_trail_marod_evq_med_hi", "btlm_trail_marod_evq_med_lo",
        "btlm_trail_marod_evq_ext_hi", "btlm_trail_marod_evq_ext_lo",
    } <= line_names
    assert "btlm_trail_marod_sig_hi" not in line_names


def test_marod_invoke_emits_line_and_zero_baseline():
    # 実 runtime 経路（統合 FakeChart）: MAROD line 系列 ＋ 0% 水平基準線群 payload。
    chart = FakeChart(name="btlm_trail_marod")
    binding = CallBinding.resolve("btlm_trail_marod", "default")
    binding.invoke(chart, _ohlc(200), {"source": "close", "maxbars": 100})
    payloads = chart.to_payloads()
    line = next(p for p in payloads if p["kind"] == "line")
    hline = next(p for p in payloads if p["kind"] == "horizontal_line")
    assert line["name"] == "btlm_trail_marod"
    assert hline["name"] == "btlm_trail_marod"  # 群 payload name = compute_id（F3 照合）
    assert [ln["price"] for ln in hline["lines"]] == [0.0]
    # warm-up（窓 < 3）は描画除外 → 先頭 2 バー分は line data に載らない。
    assert len(line["data"]) == 198


def test_marod_runtime_compute_synthetic_source():
    # 合成ソース（hl2）でも compute が MAROD line を返す（btlm_trail core の resolve_source 経由）。
    adapter = IndicatorComputeAdapter()
    series = adapter.compute("btlm_trail_marod", "default", _ohlc(150, seed=3), {
        "source": "hl2", "maxbars": 60, "color": "rgba(1, 2, 3, 1)",
    })
    line = next(s for s in series if s["kind"] == "line")
    assert line["name"] == "btlm_trail_marod"
    assert line["color"] == "rgba(1, 2, 3, 1)"
    assert len(line["data"]) > 0
