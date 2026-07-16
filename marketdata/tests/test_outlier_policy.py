"""outlier_policy 単一定義の検証（ISSUE-094 🔴-3 / ISSUE-095 項目1）。

ISSUE-095 項目1（依頼者裁定＝エンベロープ式へ統一）により、外れ値補正式を **min/max(open,close)
エンベロープ** の単一コアへ一本化した（旧 acquisition=median[o,h,l,c] 式は撤去）。閾値
（OUTLIER_THRESHOLD=0.3）と ref ゲート等の共通判定は維持する。

本ファイルは以下を回帰として固定する:
  - 閾値が唯一源であること（cleaning / dataset が本モジュールへ委譲）。
  - acquisition（cleaning 経路）と serving（dataset 経路）が **同一エンベロープコア** へ委譲し、
    同一 OHLC 入力に対しバーレベルで同一結果を返すこと。
  - 二相バー（open/close が別価格帯にまたがるバー）を両経路が保全すること
    （旧 median 式は実在しない中間値へ潰す誤検出だった）。
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


# --- 式の一本化: median 戦略は撤去済み（ISSUE-095 項目1） ------------------- #
def test_median_strategy_is_removed():
    # 旧 acquisition median 式（repair_ohlc_outliers_median）は撤去され、
    # エンベロープ式（repair_ohlc_outliers_envelope）へ一本化されている。
    assert not hasattr(outlier_policy, "repair_ohlc_outliers_median")
    assert hasattr(outlier_policy, "repair_ohlc_outliers_envelope")


# --- cleaning は acquisition エンベロープ戦略へ委譲する -------------------- #
def test_cleaning_delegates_to_envelope_strategy():
    # low だけが外れる単相バー（open/close ~42,600）。エンベロープは low を ref_lo へクランプ。
    candles = [{"time": 1, "open": 42600.0, "high": 42700.0, "low": 15095.0,
                "close": 42650.0, "volume": 9.0}]
    via_facade, log_f = cleaning.repair_ohlc_outliers(candles)
    via_core, log_c = outlier_policy.repair_ohlc_outliers_envelope(candles)
    assert via_facade == via_core
    assert log_f == log_c
    # エンベロープ式: ref_lo=min(open,close)=42600 → low(15095)<42600*0.7 で 42600 へクランプ。
    # high(42700) は ref_hi=max(open,close)=42650 の +30%(55445) 以下ゆえ不変。open/close は不変。
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


# --- 回帰ガード: acquisition と serving が同一エンベロープコアで一致する ---- #
def _acq_low_high(o, h, low, c):
    """acquisition 経路（cleaning 経路と同一）の補正後 (low, high) を取り出す。"""
    candles = [{"time": 1, "open": o, "high": h, "low": low, "close": c, "volume": 1.0}]
    repaired, _ = outlier_policy.repair_ohlc_outliers_envelope(candles)
    r = repaired[0]
    return r["low"], r["high"]


def _serving_low_high(o, h, low, c):
    """serving 経路（dataset 経路と同一）の補正後 (low, high) を取り出す。"""
    df = pd.DataFrame({"open": [o], "high": [h], "low": [low], "close": [c]})
    out = outlier_policy.clamp_ohlc_envelope(df, threshold=outlier_policy.OUTLIER_THRESHOLD)
    return float(out["low"].iloc[0]), float(out["high"].iloc[0])


def test_acquisition_and_serving_agree_bar_level():
    # 正常バー / 下ヒゲ外れ / 上ヒゲ外れ / 二相バー を両経路へ通し、(low, high) が一致すること。
    cases = [
        (100.0, 130.0, 70.0, 100.0),      # 正常（境界内・不変）
        (42600.0, 42700.0, 15095.0, 42650.0),  # 下ヒゲ外れ → low クランプ
        (42642.89, 90000.0, 42400.0, 42476.68),  # 上ヒゲ外れ → high クランプ
        (42419.0, 42454.0, 15155.0, 15156.0),  # 二相バー（保全）
    ]
    for o, h, low, c in cases:
        assert _acq_low_high(o, h, low, c) == _serving_low_high(o, h, low, c)


def test_bimodal_bar_is_preserved_by_both_paths():
    # open/close が別価格帯（42,419 と 15,156）にまたがる二相バー
    #（jp225_tick 1h 2025-08-26 相当・旧 median 式は ~28,787.5 へ 4 値を潰す誤検出だった）。
    o, h, low, c = 42419.0, 42454.0, 15155.0, 15156.0
    # acquisition: OHLC 4 値すべて不変（保全）・補正ログなし。
    candles = [{"time": 1, "open": o, "high": h, "low": low, "close": c, "volume": 1.0}]
    repaired, log = outlier_policy.repair_ohlc_outliers_envelope(candles)
    r = repaired[0]
    assert (r["open"], r["high"], r["low"], r["close"]) == (o, h, low, c)
    assert log == []
    # serving: 同一二相バーを不変に保つ（no-op で同一オブジェクト返却）。
    df = pd.DataFrame({"open": [o], "high": [h], "low": [low], "close": [c]})
    env_out = outlier_policy.clamp_ohlc_envelope(df, threshold=0.3)
    assert env_out is df


def test_acquisition_facade_preserves_bimodal_bar():
    # 公開ファサード cleaning.repair_ohlc_outliers も二相バーを保全する（式統一の実利用実証）。
    candles = [{"time": 1, "open": 42419.0, "high": 42454.0, "low": 15155.0,
                "close": 15156.0, "volume": 1.0}]
    repaired, log = cleaning.repair_ohlc_outliers(candles)
    r = repaired[0]
    assert (r["open"], r["high"], r["low"], r["close"]) == (42419.0, 42454.0, 15155.0, 15156.0)
    assert log == []
    assert r["volume"] == 1.0
