"""S4（enabler④）の検証（TDD: Red→Green→Refactor）— 銘柄/足種の汎用化・口開けの明確化。

設計正典: ``MARKETDATA_TIMESERIES_BOUNDARY_DESIGN.md`` §5（銘柄/足種の汎用化）/ §6 S4 行 /
§10.3 M-1（INTERVALS 命名変換）/ §10.3 M-3（ref_prefix 伝播）/ 付録B。

確定仕様（§5・§10.3 M-1）:
  1. 銘柄を構築時パラメータへ externalize（既達・本テストで「口が開いている」ことを固定）。
     ``DukascopyCandleSource``/``DukascopyTickSource`` の ``instrument`` 既定は ``JP225`` のまま、
     呼出側が別銘柄を渡せる（後方互換・既定で不変）。``JP225`` 定数は残す。
  2. 足種の命名統一（M-1）: 一次識別子（``TIMEFRAME_RULES`` キー系 ``"5m"/"1h"`` 等）→ ``INTERVALS``
     （``"min_5"/"hour_1"``）への**変換表を adapter 内に新設**（``TIMEFRAME_INTERVALS`` /
     ``interval_for_timeframe``）。既存 ``INTERVALS["min_5"]`` 直利用は不変・両系統サポート。
  3. ref_prefix の汎用化（S1 で両所追加済み）を銘柄汎用化と整合（既定 "jp225_m1" で不変）。
  4. 既定値（JP225・既存命名）で全既存呼出がバイト不変（汎用化は口を開けるのみ）。

回帰観点（memory bugfix-pair-with-regression-test）:
  - 既定 JP225・既存命名で構築時パラメータが汎用化前後で値不変（口開けが既存挙動を壊さない）。
  - M-1 変換表が TIMEFRAME_RULES の各キーを INTERVALS の対応定数へ正しく解決する。
"""

from __future__ import annotations

import pytest

import dukascopy_python

from marketdata.dukascopy_source import (
    INTERVALS,
    JP225,
    TIMEFRAME_INTERVALS,
    DukascopyCandleSource,
    DukascopyTickSource,
    interval_for_timeframe,
)
from marketdata.resample import TIMEFRAME_RULES


# --- 1. 銘柄 externalize（口が開いている・既定 JP225 で不変） ---

def test_candle_source_instrument_defaults_to_jp225():
    # Arrange / Act: 引数なし構築（既定 JP225）。
    src = DukascopyCandleSource()
    # Assert: 既定銘柄が JP225 のまま（後方互換・口開けが既定を変えない）。
    assert src._instrument is JP225


def test_candle_source_accepts_alternate_instrument():
    # Arrange: JP225 と異なる別銘柄定数（口が開いていることの実証）。
    other = dukascopy_python.instruments.INSTRUMENT_FX_MAJORS_EUR_USD
    # Act: 別銘柄を構築時に渡す。
    src = DukascopyCandleSource(instrument=other)
    # Assert: 渡した銘柄がそのまま採用される（externalize された口）。
    assert src._instrument is other
    assert src._instrument is not JP225


def test_tick_source_instrument_defaults_to_jp225():
    # Arrange / Act
    src = DukascopyTickSource()
    # Assert: tick 側も既定 JP225。
    assert src._instrument is JP225


def test_tick_source_accepts_alternate_instrument():
    # Arrange
    other = dukascopy_python.instruments.INSTRUMENT_FX_MAJORS_EUR_USD
    # Act
    src = DukascopyTickSource(instrument=other)
    # Assert
    assert src._instrument is other


# --- 2. M-1 変換表（TIMEFRAME_RULES キー系 → INTERVALS 定数） ---

def test_m1_table_resolves_5m_to_interval_min_5():
    # Arrange / Act / Assert: 設計 §10.3 M-1 の代表例（"5m" → INTERVAL_MIN_5）。
    assert TIMEFRAME_INTERVALS["5m"] is dukascopy_python.INTERVAL_MIN_5


def test_m1_table_resolves_1h_to_interval_hour_1():
    # Arrange / Act / Assert: "1h" → INTERVAL_HOUR_1（命名統一の核心）。
    assert TIMEFRAME_INTERVALS["1h"] is dukascopy_python.INTERVAL_HOUR_1


@pytest.mark.parametrize(
    "tf_code, legacy_name",
    [
        ("1m", "min_1"),
        ("5m", "min_5"),
        ("15m", "min_15"),
        ("30m", "min_30"),
        ("1h", "hour_1"),
        ("4h", "hour_4"),
        ("1D", "day_1"),
    ],
)
def test_m1_table_equals_legacy_intervals_for_every_supported_key(tf_code, legacy_name):
    # Arrange / Act / Assert: 新系統（"5m"）が旧系統（INTERVALS["min_5"]）と同一定数を指す
    # （両系統サポート・等価性）。
    assert TIMEFRAME_INTERVALS[tf_code] is INTERVALS[legacy_name]


def test_m1_table_excludes_weekly_monthly_derived_via_rollup():
    # Arrange: dukascopy_python は INTERVAL_WEEK_1 / INTERVAL_MONTH_1 を**持つ**（検証済み事実）。
    assert hasattr(dukascopy_python, "INTERVAL_WEEK_1")
    assert hasattr(dukascopy_python, "INTERVAL_MONTH_1")
    # Act / Assert: それでも "1W"/"1M" は変換表に含めない。本プロジェクトの週足/月足は 1 分足原子
    # から resample で導出する設計（TIMEFRAME_RULES "1W"="W-FRI"/"1M"="ME"・rollup 生成）であり、
    # 「直接 fetch する足種」のみを変換表に載せる（§4 ロールアップ設計・既存 INTERVALS と parity）。
    assert "1W" not in TIMEFRAME_INTERVALS
    assert "1M" not in TIMEFRAME_INTERVALS


def test_m1_table_keys_equal_legacy_intervals_keyset():
    # Arrange / Act / Assert: 変換表は「直接 fetch する足種」= 既存 INTERVALS と同一キー集合を
    # （新系統命名で）覆う（過不足ない 1:1 対応・命名統一の完全性）。
    legacy_to_new = {"min_1": "1m", "min_5": "5m", "min_15": "15m", "min_30": "30m",
                     "hour_1": "1h", "hour_4": "4h", "day_1": "1D"}
    assert set(TIMEFRAME_INTERVALS) == set(legacy_to_new.values())
    assert set(legacy_to_new.keys()) == set(INTERVALS)


def test_m1_table_keys_are_subset_of_timeframe_rules():
    # Arrange / Act / Assert: 変換表のキーは TIMEFRAME_RULES（唯一の規則源）のキー部分集合
    # （命名統一の一貫性・無効キーを増やさない）。
    assert set(TIMEFRAME_INTERVALS).issubset(set(TIMEFRAME_RULES))


# --- 3. interval_for_timeframe（解決ヘルパ・両系統の解決経路） ---

def test_interval_for_timeframe_resolves_new_style_code():
    # Arrange / Act / Assert: 新系統コード（"5m"）を解決する。
    assert interval_for_timeframe("5m") is dukascopy_python.INTERVAL_MIN_5


def test_interval_for_timeframe_resolves_legacy_style_name():
    # Arrange / Act / Assert: 旧系統名（"min_5"）も後方互換で解決する（両系統サポート）。
    assert interval_for_timeframe("min_5") is dukascopy_python.INTERVAL_MIN_5


def test_interval_for_timeframe_raises_keyerror_on_unknown():
    # Arrange / Act / Assert: 未知コードは KeyError（異常系・暗黙の解決を許さない）。
    with pytest.raises(KeyError):
        interval_for_timeframe("nonexistent_tf")


# --- 4. 回帰: 既定値で全既存呼出がバイト不変（口開けが既存挙動を壊さない） ---

def test_legacy_intervals_dict_unchanged_by_generalization():
    # Arrange / Act / Assert: 既存 INTERVALS（旧命名）が S4 で一切変化しない（後方互換の壁）。
    # 既存呼出（prototype_inject/jp225_chart/export_jp225_csv の INTERVALS[args.interval]）が不変。
    assert INTERVALS == {
        "day_1": dukascopy_python.INTERVAL_DAY_1,
        "hour_4": dukascopy_python.INTERVAL_HOUR_4,
        "hour_1": dukascopy_python.INTERVAL_HOUR_1,
        "min_30": dukascopy_python.INTERVAL_MIN_30,
        "min_15": dukascopy_python.INTERVAL_MIN_15,
        "min_5": dukascopy_python.INTERVAL_MIN_5,
        "min_1": dukascopy_python.INTERVAL_MIN_1,
    }


def test_default_candle_source_params_byte_invariant():
    # Arrange / Act: 引数なし構築（全既存の既定呼出を代表）。
    src = DukascopyCandleSource()
    # Assert: instrument/interval/offer_side の既定が S4 前と完全一致（バイト不変・口開けのみ）。
    assert src._instrument is JP225
    assert src._interval is dukascopy_python.INTERVAL_DAY_1
    assert src._offer_side is dukascopy_python.OFFER_SIDE_BID
