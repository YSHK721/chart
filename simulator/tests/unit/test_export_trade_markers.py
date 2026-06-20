"""export_trade_markers.py 単体テスト（TDD）。

設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §2.5（列ブリッジ・run・出力・集合包含検証）、
  §4（集合包含: 全マーカー time ⊆ candles time 集合・包含外件数をログ明示・0 件合格）。
構造: Arrange-Act-Assert（AAA）。既存データ非改変（tmp のみ書く）。

列ブリッジ・集合包含検証は純関数として単体テストし、実 marketdata の run は P6 結合検証で担保する
（本ファイルは I/O を伴わない決定論ユニットに限定）。
"""
from __future__ import annotations

import pandas as pd

from simulator.tools import export_trade_markers as ext


def test_bridge_renames_date_to_time_and_adds_zero_spread():
    # Arrange: marketdata 形式（date,open,high,low,close,volume）
    src = pd.DataFrame(
        {
            "date": ["2025-01-02 09:00:00", "2025-01-02 09:01:00"],
            "open": [8568.9, 8569.0],
            "high": [8570.0, 8571.0],
            "low": [8567.0, 8568.0],
            "close": [8569.0, 8570.0],
            "volume": [0.0, 0.0],
        }
    )
    # Act
    bridged = ext.bridge_marketdata_df(src)
    # Assert: engine 形式（time/open/high/low/close/volume/spread）に変換され spread=0
    assert list(bridged.columns) == ["time", "open", "high", "low", "close", "volume", "spread"]
    assert list(bridged["time"]) == ["2025-01-02 09:00:00", "2025-01-02 09:01:00"]
    assert list(bridged["spread"]) == [0, 0]


def test_bridge_does_not_mutate_source_dataframe():
    # Arrange
    src = pd.DataFrame(
        {
            "date": ["2025-01-02 09:00:00"],
            "open": [8568.9], "high": [8570.0], "low": [8567.0],
            "close": [8569.0], "volume": [0.0],
        }
    )
    src_before = src.copy(deep=True)
    # Act
    ext.bridge_marketdata_df(src)
    # Assert: 既存データ非改変（C1）— src は変化しない
    pd.testing.assert_frame_equal(src, src_before)


def test_markers_outside_returns_empty_when_all_times_included():
    # Arrange: 全マーカー time が candle time 集合に含まれる
    payload = {"markers": [
        {"lwc": {"time": 100}}, {"lwc": {"time": 200}}, {"lwc": {"time": 200}},
    ]}
    candle_times = {100, 150, 200, 250}
    # Act
    outside = ext.markers_outside_candle_times(payload, candle_times)
    # Assert
    assert outside == []


def test_markers_outside_lists_each_time_not_in_candle_set():
    # Arrange: 300 と 999 は candle 集合外
    payload = {"markers": [
        {"lwc": {"time": 100}}, {"lwc": {"time": 300}}, {"lwc": {"time": 999}},
    ]}
    candle_times = {100, 200}
    # Act
    outside = ext.markers_outside_candle_times(payload, candle_times)
    # Assert: 包含外の time を漏れなく列挙（無音にしない＝§4）
    assert sorted(outside) == [300, 999]


def test_candle_times_from_bridged_df_uses_same_unix_formula_as_presenter():
    # Arrange: ブリッジ後 time 列（文字列）→ presenter と同一式 int(pd.Timestamp().timestamp())
    bridged = pd.DataFrame({"time": ["2025-01-02 09:00:00", "2025-01-02 09:01:00"]})
    # Act
    times = ext.candle_unix_times(bridged)
    # Assert
    assert times == {
        int(pd.Timestamp("2025-01-02 09:00:00").timestamp()),
        int(pd.Timestamp("2025-01-02 09:01:00").timestamp()),
    }
