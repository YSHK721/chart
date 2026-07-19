"""btlm_trail の結線検証（call_binding _TABLE 経由の end-to-end invoke）。

新インジケーター btlm_trail が既存指標と同一の結線様式（CallBinding.resolve→invoke）で
系列を収集できることを固定する（loader が add_btlm_trail を実接続でロード・実行）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from adapter.compute import CallBinding, FakeLineChart
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
