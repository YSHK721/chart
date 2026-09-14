"""session_activity（セッション認識カーネル・ISSUE-094 🔴-2）の純関数契約テスト。

dwell から抽出した純カーネルが (a) dwell の委譲ラッパーと同一結果を返し、(b) I/O 非依存で
月初アンカーの因果窓を注入関数へ委譲することを固定する。
"""

from __future__ import annotations

import numpy as np

from market_profile_api.compute import session_activity as sa
from market_profile_api.compute import market_profile_dwell as mpd


_DAY = 86400


def _synth_secs() -> np.ndarray:
    # 月〜金の日中（12-15 時 UTC）に密・週末は疎、というティック秒列。
    base = 0  # 1970-01-01 木曜。
    secs = []
    for d in range(14):
        day0 = base + d * _DAY
        wd = ((day0 // _DAY) + 3) % 7
        dense = 200 if wd < 5 else 3  # 平日は密・週末は疎。
        for h in (12, 13, 14):
            for i in range(dense):
                secs.append(day0 + h * 3600 + i)
    return np.asarray(secs, dtype=np.int64)


def test_build_active_table_matches_dwell_wrapper():
    secs = _synth_secs()
    assert np.array_equal(sa.build_active_table(secs), mpd._build_active_table(secs))


def test_active_frac_threshold_marks_weekend_closed():
    secs = _synth_secs()
    table = sa.build_active_table(secs)
    # 木曜(0)の 13 時は密＝活発。土曜(5)の 13 時は疎＝休場。
    assert table[0, 13]
    assert not table[5, 13]


def test_active_seconds_cross_integrates_only_active_hours():
    table = np.zeros((7, 24), dtype=bool)
    # 1970-01-01 は wd=((0)+3)%7=3（木曜・Mon0 基準）。その 12 時のみ活発。
    table[3, 12] = True
    a = 12 * 3600      # 12:00
    b = 14 * 3600      # 14:00（12 時台=活発 3600s・13 時台=休場 0s）
    assert sa.active_seconds_cross(a, b, table) == 3600
    assert mpd._active_seconds_cross(a, b, table) == 3600


def test_table_for_day_uses_month_anchor_causal_window():
    calls = []

    def fake_active_table(symbol, at_from, win_to):
        calls.append((symbol, at_from, win_to))
        return np.ones((7, 24), dtype=bool)

    # 2020-03-15 (day_start) → 月初 2020-03-01 アンカー・窓 [月初-120日, 月初)。
    day_start = int(np.datetime64("2020-03-15T00:00:00").astype("datetime64[s]").astype(np.int64))
    sa.table_for_day(
        "JP225", day_start, active_table_days=120, active_table_fn=fake_active_table
    )
    month_start = int(np.datetime64("2020-03-01T00:00:00").astype("datetime64[s]").astype(np.int64))
    assert calls == [("JP225", month_start - 120 * _DAY, month_start)]
