"""CausalComputeGateway の結線テスト（indicator_ui full/latest_compute 再利用・疎通中心）。

実データ非依存: 合成バーで full/latest 計算が通ること、round-trip（df↔bars）が計算結果を
変えないこと、load_source の validation（未知 ref → ValueError）を検証する。
"""
from __future__ import annotations

import pandas as pd
import pytest

from simulator.replay_ui.adapter.causal_compute_gateway import CausalComputeGateway


def _bars():
    return [
        {"time": t, "open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i,
         "close": 100.5 + i, "volume": 1.0}
        for i, t in enumerate([0, 86400, 172800, 259200, 345600])
    ]


def test_compute_full_returns_series():
    gw = CausalComputeGateway()
    series = gw.compute("moving_averages", "default", "full", _bars(), {"ma_type": "sma", "length": 3})
    assert isinstance(series, list) and len(series) >= 1
    assert series[0]["kind"] == "line"


def test_compute_latest_returns_series():
    gw = CausalComputeGateway()
    series = gw.compute("moving_averages", "default", "latest", _bars(), {"ma_type": "sma", "length": 3})
    assert isinstance(series, list) and len(series) >= 1


def test_roundtrip_df_bars_df_does_not_change_compute_output():
    # Arrange — 直接 df で計算した結果と、df→bars→df 復元後の計算結果が bit 一致。
    gw = CausalComputeGateway()
    idx = pd.to_datetime([0, 86400, 172800, 259200, 345600], unit="s")
    df = pd.DataFrame(
        {"open": [100, 101, 102, 103, 104], "high": [101, 102, 103, 104, 105],
         "low": [99, 100, 101, 102, 103], "close": [100.5, 101.5, 102.5, 103.5, 104.5],
         "volume": [1.0, 1.0, 1.0, 1.0, 1.0]},
        index=idx,
    ).astype(float)
    from simulator.replay_ui.adapter._indicator_ui_bridge import load
    bridge = load()
    direct = bridge.full_compute(bridge.adapter, "moving_averages", "default", df, {"ma_type": "sma", "length": 3})
    # Act — round-trip 経由。
    bars = CausalComputeGateway._df_to_bars(df)
    via = gw.compute("moving_averages", "default", "full", bars, {"ma_type": "sma", "length": 3})
    # Assert — series の time/value が完全一致。
    assert via[0]["data"] == direct[0]["data"]


def test_load_source_unknown_ref_raises_valueerror():
    gw = CausalComputeGateway()
    with pytest.raises(ValueError):
        gw.load_source("totally_unknown_ref_xyz", "1D")


def test_load_source_unknown_timeframe_raises_valueerror():
    gw = CausalComputeGateway()
    with pytest.raises(ValueError):
        gw.load_source("jp225_tick", "7z")
