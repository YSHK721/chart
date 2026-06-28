"""MovingAverageContact: level==ma[i-1] / 先頭 skip / 跨ぎ境界（=high,=low）を固定する純テスト。"""
import pandas as pd

from contact_scan.spec import Level, MovingAverageContact, ScanContext


def _ctx(rows, bar_times, ma_by_time):
    """rows=[(open,high,low,close),...] から ScanContext を作る。"""
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"],
                      index=pd.to_datetime(bar_times, unit="s"))
    return ScanContext(df=df, bar_times=list(bar_times), ma_by_time=dict(ma_by_time))


def test_first_bar_skipped_no_levels():
    # 先頭足（i=0）は前足なし = レベル 0 件
    ctx = _ctx([(100, 110, 90, 105)], [0], {0: 100.0})
    assert MovingAverageContact().levels(ctx, 0) == []


def test_level_equals_ma_of_previous_bar():
    # i=1 の level = ma[i-1] = ma_by_time[bar_times[0]]
    ctx = _ctx([(100, 110, 90, 105), (105, 120, 100, 115)], [0, 60],
               {0: 101.5, 60: 999.0})
    levels = MovingAverageContact().levels(ctx, 1)
    assert levels == [Level(level_id="ma_prev", value=101.5)]


def test_missing_prev_ma_skipped():
    # 前足 MA 無し（ma_by_time に前足 time 無し）= スキップ
    ctx = _ctx([(100, 110, 90, 105), (105, 120, 100, 115)], [0, 60], {60: 110.0})
    assert MovingAverageContact().levels(ctx, 1) == []


def test_straddle_true_when_level_within_low_high():
    ctx = _ctx([(100, 110, 90, 105), (105, 120, 100, 115)], [0, 60], {0: 105.0})
    spec = MovingAverageContact()
    lv = spec.levels(ctx, 1)[0]
    assert spec.straddles(ctx, 1, lv) is True


def test_straddle_boundary_equals_high_inclusive():
    # level == high は跨ぎ（境界含む）
    ctx = _ctx([(100, 110, 90, 105), (105, 120, 100, 115)], [0, 60], {0: 120.0})
    spec = MovingAverageContact()
    lv = spec.levels(ctx, 1)[0]
    assert spec.straddles(ctx, 1, lv) is True


def test_straddle_boundary_equals_low_inclusive():
    ctx = _ctx([(100, 110, 90, 105), (105, 120, 100, 115)], [0, 60], {0: 100.0})
    spec = MovingAverageContact()
    lv = spec.levels(ctx, 1)[0]
    assert spec.straddles(ctx, 1, lv) is True


def test_straddle_false_when_level_outside():
    ctx = _ctx([(100, 110, 90, 105), (105, 120, 100, 115)], [0, 60], {0: 99.9})
    spec = MovingAverageContact()
    lv = spec.levels(ctx, 1)[0]
    assert spec.straddles(ctx, 1, lv) is False


def test_tick_values_is_mid_identity():
    ctx = _ctx([(100, 110, 90, 105)], [0], {0: 100.0})
    ticks = [(10, 100.5), (20, 101.25)]
    assert MovingAverageContact().tick_values(ctx, 0, ticks) == [(10, 100.5), (20, 101.25)]
