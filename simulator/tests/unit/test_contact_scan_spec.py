"""bit 一致回帰: MovingAverageContact（脱 pandas・plain 配列 ScanContext）。

level==ma[i-1] / 先頭 skip / 前足 MA 無し skip / 跨ぎ境界（=high,=low 含む）/ tick=mid 恒等。
参照実装 prototype_260626-01/contact_scan/spec.py と同一規約（df 依存を highs/lows 配列へ置換）。
"""
from __future__ import annotations

from simulator.usecase.contact_scan.spec import (
    Level,
    MovingAverageContact,
    ScanContext,
)


def _ctx(highs, lows, closes, bar_times, ma_by_time):
    return ScanContext(
        highs=[float(h) for h in highs],
        lows=[float(l) for l in lows],
        closes=[float(c) for c in closes],
        bar_times=[int(t) for t in bar_times],
        ma_by_time={int(k): float(v) for k, v in ma_by_time.items()},
    )


def test_first_bar_skipped_no_levels():
    # 先頭足（i=0）は前足なし = レベル 0 件
    ctx = _ctx([110], [90], [105], [0], {0: 100.0})
    assert MovingAverageContact().levels(ctx, 0) == []


def test_level_equals_ma_of_previous_bar():
    # i=1 の level = ma[i-1] = ma_by_time[bar_times[0]]
    ctx = _ctx([110, 120], [90, 100], [105, 115], [0, 60], {0: 101.5, 60: 999.0})
    levels = MovingAverageContact().levels(ctx, 1)
    assert levels == [Level(level_id="ma_prev", value=101.5)]


def test_missing_prev_ma_skipped():
    # 前足 MA 無し（ma_by_time に前足 time 無し）= スキップ
    ctx = _ctx([110, 120], [90, 100], [105, 115], [0, 60], {60: 110.0})
    assert MovingAverageContact().levels(ctx, 1) == []


def test_straddle_true_when_level_within_low_high():
    ctx = _ctx([110, 120], [90, 100], [105, 115], [0, 60], {0: 105.0})
    spec = MovingAverageContact()
    lv = spec.levels(ctx, 1)[0]
    assert spec.straddles(ctx, 1, lv) is True


def test_straddle_boundary_equals_high_inclusive():
    # level == high は跨ぎ（境界含む）
    ctx = _ctx([110, 120], [90, 100], [105, 115], [0, 60], {0: 120.0})
    spec = MovingAverageContact()
    lv = spec.levels(ctx, 1)[0]
    assert spec.straddles(ctx, 1, lv) is True


def test_straddle_boundary_equals_low_inclusive():
    ctx = _ctx([110, 120], [90, 100], [105, 115], [0, 60], {0: 100.0})
    spec = MovingAverageContact()
    lv = spec.levels(ctx, 1)[0]
    assert spec.straddles(ctx, 1, lv) is True


def test_straddle_false_when_level_outside():
    ctx = _ctx([110, 120], [90, 100], [105, 115], [0, 60], {0: 99.9})
    spec = MovingAverageContact()
    lv = spec.levels(ctx, 1)[0]
    assert spec.straddles(ctx, 1, lv) is False


def test_tick_values_is_mid_identity():
    ctx = _ctx([110], [90], [105], [0], {0: 100.0})
    ticks = [(10, 100.5), (20, 101.25)]
    assert MovingAverageContact().tick_values(ctx, 0, ticks) == [(10, 100.5), (20, 101.25)]
