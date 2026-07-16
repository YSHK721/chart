"""outlier_policy 単一定義の検証（ISSUE-094 🔴-3）。

閾値（OUTLIER_THRESHOLD）が両戦略の唯一源であり、cleaning / dataset が本モジュールへ委譲する
ことを固定する。両戦略の byte 挙動は既存 test_volume_s0 / test_dataset_outlier_clamp が担保する。
本ファイルは「委譲の結線」と「両戦略の式が別物である」実測事実（3a）を回帰として固定する。
"""

from __future__ import annotations

import pandas as pd

from marketdata import cleaning, outlier_policy


# --- 閾値の単一化 ---------------------------------------------------------- #
def test_threshold_is_single_source_030():
    assert outlier_policy.OUTLIER_THRESHOLD == 0.3


def test_dataset_threshold_reexports_policy_threshold():
    from marketdata import dataset
    assert dataset.OUTLIER_CLAMP_THRESHOLD is outlier_policy.OUTLIER_THRESHOLD


# --- cleaning は acquisition 戦略へ委譲する ------------------------------- #
def test_cleaning_delegates_to_median_strategy():
    candles = [{"time": 1, "open": 42600.0, "high": 42700.0, "low": 15095.0,
                "close": 42650.0, "volume": 9.0}]
    via_facade, log_f = cleaning.repair_ohlc_outliers(candles)
    via_core, log_c = outlier_policy.repair_ohlc_outliers_median(candles)
    assert via_facade == via_core
    assert log_f == log_c
    # 中央値式の既存挙動（byte 不変）: low のみ閾値超→ref 置換→low=min(fixed)=42600。
    assert (via_facade[0]["open"], via_facade[0]["high"],
            via_facade[0]["low"], via_facade[0]["close"]) == (42600.0, 42700.0, 42600.0, 42650.0)


# --- dataset のクランプは serving 戦略へ委譲する（ref ゲートは dataset が担う） --- #
def test_dataset_clamp_delegates_to_envelope_strategy():
    from marketdata import dataset
    df = pd.DataFrame(
        {"open": [42642.89], "high": [42705.29], "low": [15098.53], "close": [42476.68]},
        index=pd.to_datetime(["2025-08-26"]),
    )
    out = dataset._clamp_outlier_bars(df, "jp225_tick")
    direct = outlier_policy.clamp_ohlc_envelope(df, threshold=0.3)
    assert out["low"].iloc[0] == direct["low"].iloc[0] == 42476.68


# --- 実測（3a）: 二相バーで 2 戦略が乖離する（式が別物である回帰の壁） --- #
def test_two_strategies_diverge_on_bimodal_bar():
    # open/close が別価格帯（15,156 と 42,419）にまたがる二相バー（jp225_tick 1h 2025-08-26 相当）。
    candles = [{"time": 1, "open": 42419.0, "high": 42454.0, "low": 15155.0,
                "close": 15156.0, "volume": 1.0}]
    df = pd.DataFrame(
        {"open": [42419.0], "high": [42454.0], "low": [15155.0], "close": [15156.0]},
        index=pd.to_datetime(["2025-08-26 06:00:00"]),
    )
    median_out, _ = outlier_policy.repair_ohlc_outliers_median(candles)
    env_out = outlier_policy.clamp_ohlc_envelope(df, threshold=0.3)
    # median 式は 4 値を実在しない中間値（~28,787）へ潰す（明白な誤検出）。
    assert median_out[0]["open"] == median_out[0]["close"]
    assert abs(median_out[0]["open"] - 28787.5) < 1.0
    # エンベロープ式は当該二相バーを不変に保つ（同一オブジェクト返却＝no-op）。
    assert env_out is df
