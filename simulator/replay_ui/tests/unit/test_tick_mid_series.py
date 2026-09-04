"""E-4 TickMidSeries.mid_series の境界値 AAA（contact_scan.tick_window に bit 一致）。

窓 [start,end) フィルタ → mid=(bid+ask)/2 → 窓内 mid 中央値 ±threshold 外れ値除去 → cap 無し。
"""
from __future__ import annotations

from simulator.replay_ui.domain.tick_mid_series import mid_series


def test_window_filter_half_open_start_inclusive_end_exclusive():
    # Arrange — sec: 9(除外) / 10(含) / 19(含) / 20(除外)。
    ticks = [(9, 100.0, 100.0), (10, 100.0, 100.0), (19, 100.0, 100.0), (20, 100.0, 100.0)]
    # Act
    out = mid_series(ticks, 10, 20)
    # Assert
    assert [s for s, _ in out] == [10, 19]


def test_mid_is_average_of_bid_ask():
    out = mid_series([(10, 100.0, 102.0)], 0, 100)
    assert out == [(10, 101.0)]


def test_outlier_removed_by_median_relative_threshold():
    # 中央値≈100、±30%超(例 200)を除去。
    ticks = [
        (10, 100.0, 100.0),
        (11, 100.0, 100.0),
        (12, 200.0, 200.0),  # +100% → 除去
    ]
    out = mid_series(ticks, 0, 100, threshold=0.3)
    assert [s for s, _ in out] == [10, 11]


def test_boundary_exactly_at_threshold_is_kept():
    # |mid/m - 1| <= threshold は保持（proto の <= と一致）。中央値=100、125 は +25% ちょうど
    # （0.25 は IEEE754 で厳密表現可＝境界の等号が確実に成立し、float 依存のブレを排除）。
    ticks = [(10, 100.0, 100.0), (11, 100.0, 100.0), (12, 125.0, 125.0)]
    out = mid_series(ticks, 0, 100, threshold=0.25)
    assert [s for s, _ in out] == [10, 11, 12]


def test_no_cap_all_ticks_returned():
    # 5000 点でも間引かない（接点検証の絶対仕様）。
    ticks = [(1000 + i, 100.0, 100.0) for i in range(5000)]
    out = mid_series(ticks, 0, 10_000_000, threshold=0.3)
    assert len(out) == 5000


def test_empty_window_returns_empty():
    assert mid_series([(1, 100.0, 100.0)], 100, 200) == []


def test_median_even_count_matches_pandas_average_of_middle_two():
    # 偶数個: median=中央2点平均（pandas .median() と一致）。
    # mid 列=[100,100,101,101] → median=100.5。閾値大で全保持を確認（除去なし）。
    ticks = [(10, 100.0, 100.0), (11, 100.0, 100.0), (12, 101.0, 101.0), (13, 101.0, 101.0)]
    out = mid_series(ticks, 0, 100, threshold=0.5)
    assert [v for _, v in out] == [100.0, 100.0, 101.0, 101.0]


def test_all_zero_mid_no_outlier_filter_applied():
    # median<=0 のとき外れ値除去をスキップ（proto: m>0 のときのみ）。
    ticks = [(10, 0.0, 0.0), (11, 0.0, 0.0)]
    out = mid_series(ticks, 0, 100, threshold=0.3)
    assert [s for s, _ in out] == [10, 11]


def test_nan_mid_excluded_from_median_and_output():
    # 回帰（code🟡-1）: bid/ask のいずれかが NaN のティックは中央値算出から除外し（pandas
    #   mid.median() の skipna と一致）、出力にも残さない。statistics.median は NaN 混入で破損するため。
    nan = float("nan")
    ticks = [(1, 100.0, 100.0), (2, nan, nan), (3, 101.0, 101.0), (4, 300.0, 300.0)]
    out = mid_series(ticks, 0, 10, threshold=0.3)
    # NaN(sec2) は除外・外れ値300(sec4)も除外・100/101 のみ残る。
    assert out == [(1, 100.0), (3, 101.0)]
