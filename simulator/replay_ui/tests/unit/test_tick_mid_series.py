"""E-4 の窓内価格列 ``price_series`` の境界値 AAA。

窓 [start,end) フィルタ → 窓内価格の中央値 ±threshold 外れ値除去 → cap 無し。

価格は adapter が ref の価格基準（台帳の ``price_basis``）で畳んだ値として渡る。以前は
本 domain が ``mid=(bid+ask)/2`` を自前で持っていたため、確定足が bid の ref（jp225_mt5）でも
リプレイの足内ティックが mid で描かれていた（ISSUE-515 と同型）。価格の畳み方は
marketdata/tick_m1.py の 1 箇所に置き、本 domain は窓と外れ値だけを担う（ISSUE-512 段階 4 の前提）。
"""
from __future__ import annotations

from simulator.replay_ui.domain.tick_mid_series import price_series


def test_window_filter_half_open_start_inclusive_end_exclusive():
    # Arrange — sec: 9(除外) / 10(含) / 19(含) / 20(除外)。
    prices = [(9, 100.0), (10, 100.0), (19, 100.0), (20, 100.0)]
    # Act
    out = price_series(prices, 10, 20)
    # Assert
    assert [s for s, _ in out] == [10, 19]


def test_prices_pass_through_unchanged():
    """価格は受け取った値のまま（畳み方は adapter 側・ここで再計算しない）。"""
    out = price_series([(10, 101.0)], 0, 100)
    assert out == [(10, 101.0)]


def test_outlier_removed_by_median_relative_threshold():
    # 中央値≈100、±30%超(例 200)を除去。
    prices = [(10, 100.0), (11, 100.0), (12, 200.0)]
    out = price_series(prices, 0, 100, threshold=0.3)
    assert [s for s, _ in out] == [10, 11]


def test_boundary_exactly_at_threshold_is_kept():
    # |p/m - 1| <= threshold は保持。中央値=100、125 は +25% ちょうど（0.25 は IEEE754 で厳密表現可）。
    prices = [(10, 100.0), (11, 100.0), (12, 125.0)]
    out = price_series(prices, 0, 100, threshold=0.25)
    assert [s for s, _ in out] == [10, 11, 12]


def test_no_cap_all_ticks_returned():
    # 5000 点でも間引かない（接点検証の絶対仕様）。
    prices = [(1000 + i, 100.0) for i in range(5000)]
    out = price_series(prices, 0, 10_000_000, threshold=0.3)
    assert len(out) == 5000


def test_empty_window_returns_empty():
    assert price_series([(1, 100.0)], 100, 200) == []


def test_median_even_count_matches_pandas_average_of_middle_two():
    # 偶数個: median=中央2点平均（pandas .median() と一致）。閾値大で全保持を確認（除去なし）。
    prices = [(10, 100.0), (11, 100.0), (12, 101.0), (13, 101.0)]
    out = price_series(prices, 0, 100, threshold=0.5)
    assert [v for _, v in out] == [100.0, 100.0, 101.0, 101.0]


def test_all_zero_prices_no_outlier_filter_applied():
    # median<=0 のとき外れ値除去をスキップ（proto: m>0 のときのみ）。
    prices = [(10, 0.0), (11, 0.0)]
    out = price_series(prices, 0, 100, threshold=0.3)
    assert [s for s, _ in out] == [10, 11]


def test_nan_price_excluded_from_median_and_output():
    # NaN 価格は中央値算出から除外し（pandas median の skipna と一致）、出力にも残さない。
    nan = float("nan")
    prices = [(1, 100.0), (2, nan), (3, 101.0), (4, 300.0)]
    out = price_series(prices, 0, 10, threshold=0.3)
    # NaN(sec2) は除外・外れ値300(sec4)も除外・100/101 のみ残る。
    assert out == [(1, 100.0), (3, 101.0)]
