"""S0（enabler①）の検証（TDD: Red→Green）— Candle volume 追加・cleaning 透過・dukascopy 抽出。

設計正典: ``MARKETDATA_TIMESERIES_BOUNDARY_DESIGN.md`` §2.1（Candle volume 後定義）/
§2.4（cleaning volume 透過）/ §6 S0 行 / §6 順序依存①（3 点を同一ステップで・部分適用禁止）/
付録B（``Candle.volume: float``）。

確定仕様（同一ステップ・部分適用禁止）:
  1. ``marketdata.port.Candle`` TypedDict に ``volume: float`` を追加（既存キーの後）。
  2. ``marketdata.cleaning.repair_ohlc_outliers`` が volume を透過（``cd.get("volume", 0.0)``）。
     OHLC 補正結果自体はバイト不変（volume はパススルー追加のみ）。
  3. ``marketdata.dukascopy_source._to_candles`` が raw DataFrame の ``volume`` 列を Candle.volume へ。
     volume 列が無い / NaN は 0.0。

回帰観点（memory bugfix-pair-with-regression-test）:
  - volume 欠落入力で cleaning が KeyError で落ちない（``.get`` による非破壊保証）。
"""

from __future__ import annotations

import math
from typing import get_type_hints

import pandas as pd

from marketdata.cleaning import repair_ohlc_outliers
from marketdata.dukascopy_source import _to_candles
from marketdata.port import Candle


# --- 1. Candle TypedDict に volume: float が定義されている（型・付録B 定義一致） ---

def test_candle_typeddict_declares_volume_as_float():
    # Arrange / Act: TypedDict の型注釈を解決する。
    hints = get_type_hints(Candle)
    # Assert: volume キーが float 型で宣言されている（付録B）。
    assert "volume" in hints, "Candle に volume キーが宣言されていない"
    assert hints["volume"] is float, f"Candle.volume は float であるべき: {hints['volume']}"


def test_candle_typeddict_preserves_existing_keys():
    # Arrange / Act: 既存キーが volume 追加後も全て残存する（後方互換）。
    hints = get_type_hints(Candle)
    # Assert: time/open/high/low/close が壊れていない。
    for key, typ in (("time", int), ("open", float), ("high", float),
                     ("low", float), ("close", float)):
        assert hints.get(key) is typ, f"Candle.{key} は {typ} であるべき: {hints.get(key)}"


# --- 2. cleaning が volume を透過保持する ---

def test_repair_preserves_volume_on_unmodified_candle():
    # Arrange: 補正不要（OHLC 正常）かつ volume 付きの 1 本。
    candles = [{"time": 1, "open": 100.0, "high": 110.0, "low": 90.0,
                "close": 105.0, "volume": 4242.0}]
    # Act
    repaired, _ = repair_ohlc_outliers(candles)
    # Assert: volume が同値で保持される。
    assert repaired[0]["volume"] == 4242.0


def test_repair_preserves_volume_on_corrected_candle():
    # Arrange: low が中央値から -64% 乖離（補正対象）＋ volume 付き。
    candles = [{"time": 1, "open": 42600.0, "high": 42700.0, "low": 15095.0,
                "close": 42650.0, "volume": 999.0}]
    # Act
    repaired, log = repair_ohlc_outliers(candles)
    # Assert: 補正が走り（ログあり）かつ volume は補正経路でも保持される。
    assert log, "外れ値補正が発生しているべき"
    assert repaired[0]["volume"] == 999.0


def test_repair_defaults_volume_to_zero_when_absent():
    # Arrange: volume 欠落入力（①前互換）。
    candles = [{"time": 1, "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0}]
    # Act
    repaired, _ = repair_ohlc_outliers(candles)
    # Assert: KeyError で落ちず volume=0.0 が補われる（§2.4 非破壊）。
    assert repaired[0]["volume"] == 0.0


def test_repair_ohlc_values_byte_invariant_with_volume():
    # Arrange: 補正対象 OHLC ＋ volume。OHLC 補正結果が volume 追加前後でバイト不変であること。
    candles = [{"time": 1, "open": 42600.0, "high": 42700.0, "low": 15095.0,
                "close": 42650.0, "volume": 999.0}]
    # Act
    repaired, log = repair_ohlc_outliers(candles)
    out = repaired[0]
    # Assert: OHLC は volume 非依存（中央値補正の既存仕様どおり）。
    # ref = median(42600,42700,15095,42650) = 42625.0、low=15095 のみ閾値超→ref 置換、
    # 再不変条件確立で low=min(fixed)=42600.0・high=max(fixed)=42700.0。
    assert out["open"] == 42600.0
    assert out["high"] == 42700.0
    assert out["low"] == 42600.0
    assert out["close"] == 42650.0
    # 補正ログ（O/H/L/C 表示）も volume を含まない既存形式のまま。
    assert log and "/" in log[0] and "volume" not in log[0]


def test_repair_ohlc_byte_invariant_with_vs_without_volume():
    # Arrange: 同一 OHLC を volume 有り / 無しで用意（OHLC 補正結果の volume 非依存を直接実証）。
    base = {"open": 42600.0, "high": 42700.0, "low": 15095.0, "close": 42650.0}
    with_vol = [{"time": 1, **base, "volume": 999.0}]
    without_vol = [{"time": 1, **base}]
    # Act
    out_with, log_with = repair_ohlc_outliers(with_vol)
    out_without, log_without = repair_ohlc_outliers(without_vol)
    # Assert: OHLC 4 値・ログがバイト不変（volume はパススルー追加のみ）。
    keys = ("open", "high", "low", "close")
    assert {k: out_with[0][k] for k in keys} == {k: out_without[0][k] for k in keys}
    assert log_with == log_without


# --- 回帰: volume 欠落入力で cleaning が KeyError で落ちない（bugfix-pair-with-regression-test） ---

def test_repair_does_not_raise_keyerror_on_volume_missing_input():
    # Arrange: volume を一切持たない複数本（うち 1 本は補正対象）。
    candles = [
        {"time": 1, "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0},
        {"time": 2, "open": 42600.0, "high": 42700.0, "low": 15095.0, "close": 42650.0},
    ]
    # Act / Assert: 例外を送出しない（回帰の壁）。
    try:
        repaired, _ = repair_ohlc_outliers(candles)
    except KeyError as exc:  # pragma: no cover - 回帰検出
        raise AssertionError(f"volume 欠落入力で KeyError: {exc}") from exc
    assert all("volume" in r for r in repaired)


# --- 3. dukascopy _to_candles が raw の volume を Candle.volume へ抽出する ---

def _raw_df(rows):
    """UTC index・open/high/low/close(/volume) 列の raw DataFrame fixture。"""
    idx = pd.to_datetime([r["t"] for r in rows], utc=True)
    data = {
        "open": [r["open"] for r in rows],
        "high": [r["high"] for r in rows],
        "low": [r["low"] for r in rows],
        "close": [r["close"] for r in rows],
    }
    if "volume" in rows[0]:
        data["volume"] = [r["volume"] for r in rows]
    return pd.DataFrame(data, index=idx)


def test_to_candles_extracts_volume_from_raw():
    # Arrange: volume 列を持つ raw（dukascopy fetch 結果の列名は "volume"）。
    df = _raw_df([{"t": "2025-01-02T00:00:00Z", "open": 100.0, "high": 110.0,
                   "low": 90.0, "close": 105.0, "volume": 1234.5}])
    # Act
    candles = _to_candles(df)
    # Assert: volume が Candle へ抽出される。
    assert candles[0]["volume"] == 1234.5


def test_to_candles_defaults_volume_to_zero_when_column_absent():
    # Arrange: volume 列が無い raw（後方互換・欠落入力）。
    df = _raw_df([{"t": "2025-01-02T00:00:00Z", "open": 100.0, "high": 110.0,
                   "low": 90.0, "close": 105.0}])
    # Act
    candles = _to_candles(df)
    # Assert: 列不在は 0.0。
    assert candles[0]["volume"] == 0.0


def test_to_candles_defaults_volume_to_zero_when_nan():
    # Arrange: volume が NaN（欠損）。
    df = _raw_df([{"t": "2025-01-02T00:00:00Z", "open": 100.0, "high": 110.0,
                   "low": 90.0, "close": 105.0, "volume": float("nan")}])
    # Act
    candles = _to_candles(df)
    # Assert: NaN は 0.0 に正規化（assertion が NaN を弾く）。
    assert candles[0]["volume"] == 0.0
    assert not math.isnan(candles[0]["volume"])


def test_to_candles_preserves_ohlc_alongside_volume():
    # Arrange: OHLC + volume が同時に正しく載ること（volume 追加が OHLC を壊さない）。
    df = _raw_df([{"t": "2025-01-02T00:00:00Z", "open": 100.0, "high": 110.0,
                   "low": 90.0, "close": 105.0, "volume": 7.0}])
    # Act
    c = _to_candles(df)[0]
    # Assert
    assert (c["open"], c["high"], c["low"], c["close"]) == (100.0, 110.0, 90.0, 105.0)
    assert c["volume"] == 7.0
