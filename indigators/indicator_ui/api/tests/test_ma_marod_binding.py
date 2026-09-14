"""ma_marod の結線検証（call_binding _TABLE 経由の end-to-end invoke）。

新指標 ma_marod（移動平均乖離率・MA 種別選択式・別 pane オシレータ）が既存指標と同一の
結線様式（CallBinding.resolve→invoke / IndicatorComputeAdapter.compute）で系列を収集
できることを固定する（loader が add_ma_marod を実接続でロード・実行し、moving_averages
core（4 種 MA）と btlm_trail_marod core（バンド）を importlib 動的ロードで参照する経路を通す）。
btlm_trail_marod の結線テストと対称。
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


def test_ma_marod_registered_in_table_and_schema():
    binding = CallBinding.resolve("ma_marod", "default")
    assert binding.output_kind == "line"
    assert "ma_marod" in PARAM_DEFAULTS
    # timeframe（計算.時間足）は全指標共通のため indicator_param_defaults が注入する（ISSUE-274）。
    assert set(PARAM_DEFAULTS["ma_marod"]) == {
        "source", "ma_type", "length", "q_low", "q_high", "q_out", "k_events",
        "event_agg", "window_n", "color", "timeframe",
    }
    assert PARAM_DEFAULTS["ma_marod"]["q_out"] == 0.99    # イベント極端分位（裁定 2026-07-21）
    assert PARAM_DEFAULTS["ma_marod"]["k_events"] == 50   # イベント分位の直近観測件数
    # 集計単位はエピソード極値が既定（バー値＝旧方式へ UI から復帰可能・裁定 2026-07-21）。
    assert PARAM_DEFAULTS["ma_marod"]["event_agg"] == "episode"


def test_ma_marod_invoke_emits_bands_and_event_quantile_levels():
    # 実 runtime 経路: MA_MAROD line ＋ 0% 基準線に加え、正常バンド＋イベント分位水準線 4 本
    #   （{med|ext} × {hi|lo}）を emit する。σ バンド・_all 系列は描画廃止（裁定 2026-07-21）。
    chart = FakeChart(name="ma_marod")
    binding = CallBinding.resolve("ma_marod", "default")
    binding.invoke(chart, _ohlc(400, seed=7), {
        "source": "close", "ma_type": "ema", "length": 50,
        "q_low": 0.05, "q_high": 0.95, "q_out": 0.99, "k_events": 10, "window_n": 200,
    })
    line_names = {p["name"] for p in chart.to_payloads() if p["kind"] == "line"}
    assert {
        "ma_marod",
        "ma_marod_q5", "ma_marod_q95",
        "ma_marod_evq_med_hi", "ma_marod_evq_med_lo",
        "ma_marod_evq_ext_hi", "ma_marod_evq_ext_lo",
    } <= line_names
    assert "ma_marod_sig_hi" not in line_names
    assert "ma_marod_evq_med_hi_all" not in line_names


def test_ma_marod_invoke_ext_levels_empty_when_q_out_invalid():
    # q_out 無効（None＝空欄・q_out<=q_high）は極端線（ext_*）が空データ（黙って無効化・前例規約）。
    for q_out in (None, 0.90):
        chart = FakeChart(name="ma_marod")
        binding = CallBinding.resolve("ma_marod", "default")
        binding.invoke(chart, _ohlc(400, seed=7), {
            "source": "close", "ma_type": "ema", "length": 50,
            "q_low": 0.05, "q_high": 0.95, "q_out": q_out, "k_events": 10, "window_n": 200,
        })
        payloads = chart.to_payloads()
        ext = next(p for p in payloads if p["name"] == "ma_marod_evq_ext_hi")
        assert ext["data"] == []


def test_ma_marod_invoke_emits_line_and_zero_baseline():
    # 実 runtime 経路（統合 FakeChart）: MA_MAROD line 系列 ＋ 0% 水平基準線群 payload。
    chart = FakeChart(name="ma_marod")
    binding = CallBinding.resolve("ma_marod", "default")
    binding.invoke(chart, _ohlc(200), {"source": "close", "ma_type": "sma", "length": 50})
    payloads = chart.to_payloads()
    line = next(p for p in payloads if p["kind"] == "line")
    hline = next(p for p in payloads if p["kind"] == "horizontal_line")
    assert line["name"] == "ma_marod"
    assert hline["name"] == "ma_marod"  # 群 payload name = compute_id（F3 照合）
    assert [ln["price"] for ln in hline["lines"]] == [0.0]
    # sma warm-up（先頭 length-1 本）は描画除外 → line data は n-(length-1) 本。
    assert len(line["data"]) == 151


def test_ma_marod_runtime_compute_synthetic_source():
    # 合成ソース（hl2）でも compute が MA_MAROD line を返す（moving_averages と同一写像の
    #   resolve_source 経由・計算の原子は MA と同期）。
    adapter = IndicatorComputeAdapter()
    series = adapter.compute("ma_marod", "default", _ohlc(150, seed=3), {
        "source": "hl2", "ma_type": "lwma", "length": 30, "color": "rgba(1, 2, 3, 1)",
    })
    line = next(s for s in series if s["kind"] == "line")
    assert line["name"] == "ma_marod"
    assert line["color"] == "rgba(1, 2, 3, 1)"
    assert len(line["data"]) > 0
