"""tf_period_profile（時間足毎の min-unit プロファイル列・ローリング窓）の検証。

対象: tf_period_profiles(secs, mids, tf_sec, unit, from_unix, to_unix, va_pct) -> list[column]
      tick(mid) を tf 周期で分割し、各周期を最小価格単位でビニングした sparse プロファイル列。
      窓 [from_unix, to_unix) 内の周期のみ（ローリング窓配信）。純関数（I/O なし）。

構造: Arrange-Act-Assert。手計算ゴールデンで厳密照合。
"""
from __future__ import annotations

import numpy as np

from market_profile_api.compute.tf_period_profile import tf_period_profiles


def test_splits_by_tf_period_and_bins_at_min_unit_golden():
    # unit=1.0・tf=60s（1m）。period0[0..59]: mids 10,10,11,12 / period60[60..119]: mids 20,21。
    secs = np.array([0, 10, 20, 30, 60, 70])
    mids = np.array([10.0, 10.0, 11.0, 12.0, 20.0, 21.0])
    cols = tf_period_profiles(secs, mids, tf_sec=60, unit=1.0, from_unix=0, to_unix=120)
    assert len(cols) == 2
    c0, c1 = cols
    assert c0["time"] == 0
    assert c0["levels"] == [[10.0, 2], [11.0, 1], [12.0, 1]]  # 最小単位で占有レベル（sparse・価格昇順）
    assert c0["poc"] == 10.0
    assert (c0["va_low"], c0["va_high"]) == (10.0, 11.0)  # 70%: POC10(2)+上11(1)=3/4>=0.7
    assert (c0["price_min"], c0["price_max"], c0["tpo_units"]) == (10.0, 12.0, 4)
    assert c1["time"] == 60
    assert c1["levels"] == [[20.0, 1], [21.0, 1]]
    assert c1["tpo_units"] == 2


def test_rolling_window_filters_periods_by_start():
    secs = np.array([0, 30, 60, 90])
    mids = np.array([10.0, 11.0, 20.0, 21.0])
    # 窓 [0,60) は period0 のみ（period60 は start=60 が窓外）。
    cols = tf_period_profiles(secs, mids, tf_sec=60, unit=1.0, from_unix=0, to_unix=60)
    assert [c["time"] for c in cols] == [0]


def test_min_unit_quantization():
    # unit=0.5: 10.1→10.0, 10.3→10.5（最小単位へ量子化）。
    secs = np.array([0, 1])
    mids = np.array([10.1, 10.3])
    cols = tf_period_profiles(secs, mids, tf_sec=60, unit=0.5, from_unix=0, to_unix=60)
    assert cols[0]["levels"] == [[10.0, 1], [10.5, 1]]


def test_empty_ticks_and_empty_window():
    assert tf_period_profiles(np.array([]), np.array([]), 60, 1.0, 0, 120) == []
    secs = np.array([0, 30]); mids = np.array([10.0, 11.0])
    assert tf_period_profiles(secs, mids, 60, 1.0, 1000, 2000) == []  # 窓外
