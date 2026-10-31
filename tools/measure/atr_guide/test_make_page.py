"""make_page.py の平均の比較の状態検証と計算量検定（CLAUDE.md の計算量テスト規約）。"""
import numpy as np
import pandas as pd

from tools.measure.atr_guide import make_page as P


def _tr(n, seed=0):
    return np.random.default_rng(seed).uniform(50, 200, n)


def test_wilder_matches_ema_with_alpha_one_over_n():
    tr = _tr(400)
    n = 14
    ema = pd.Series(tr).ewm(span=2 * n - 1, adjust=False).mean().to_numpy()
    assert abs(P.wilder(tr, n)[-1] - ema[-1]) < 1e-3  # 出発点の差だけが残り、十分な本数で消える


def test_impulse_exits_at_once_in_sma_but_gradually_in_wilder():
    n = 4
    tr = np.array([100.0] * 10 + [500.0] + [100.0] * 10)
    a, w = P.sma(tr, n), P.wilder(tr, n)
    assert a[10] == 200.0 and a[13] == 200.0 and a[14] == 100.0
    assert w[10] == 200.0 and w[11] == 175.0
    assert 100.0 < w[14] < w[13]


def test_smooth_rows_returns_one_row_per_method_and_n():
    rows = P.smooth_rows(_tr(300), ns=(14, 20))
    assert [(r[0], r[1]) for r in rows] == [(name, n) for n in (14, 20) for name, _ in P.SMOOTHERS]
    assert all(r[2] > 0 and r[3] > 0 for r in rows)


def test_bars_to_catch_up_follows_the_weights():
    assert P.bars_to_catch_up(lambda t: P.sma(t, 14), 0.5) == 7
    n = 14
    want = int(np.ceil(np.log(0.1) / np.log(1 - 1 / n)))  # ワイルダーの平滑は 1 − (1 − 1/n)^k まで追いつく
    assert P.bars_to_catch_up(lambda t: P.wilder(t, n), 0.9) == want


def test_lag_to_center_finds_a_known_delay():
    x = np.sin(np.arange(2000) / 60.0) + 2.0
    a = pd.Series(x).shift(7).to_numpy()
    assert P.lag_to_center(x, a, start=100) == 7


def test_next_errors_are_zero_for_a_perfect_estimate():
    x = np.full(400, 5.0)
    assert P.next_errors(x, np.full(400, 5.0)) == (0.0, 0.0)


def test_next_errors_divide_by_the_mean_of_the_value():
    x = np.r_[np.full(300, 1.0), np.full(300, 3.0)]
    a = np.full(600, 1.0)  # k は中央値の 1〜3。値が 2 通りなので外れの式を手で確かめられる
    e1, _ = P.next_errors(x, a, start=0)
    f = x[1:]
    k = np.median(f)
    assert abs(e1 - np.mean(np.abs(f - k)) / f.mean() * 100) < 1e-9


def test_beta_rows_merges_is_and_oos():
    df = pd.DataFrame([
        dict(period="IS", tf="1h", n=50, H=0.4, H_null=0.5, H_pass=False, D=-0.1, D_pass=False, err_gain=0.0, usable=False),
        dict(period="OOS", tf="1h", n=50, H=0.41, H_null=0.5, H_pass=True, D=-0.2, D_pass=True, err_gain=0.1, usable=False),
    ])
    r = P.beta_rows(df)[0]
    assert r["tf"] == "1 時間足" and r["direction"] == "逆向き" and r["h_is"] == 40.0 and r["h_oos"] == 41.0
    assert r["d_pass"] and r["gain"] == 0.1


def test_figures_show_only_the_passed_pairs_in_the_gain_chart():
    rows = [dict(tf="日足", n=20, gain=0.3, d_pass=False), dict(tf="1 時間足", n=50, gain=0.1, d_pass=True)]
    svg = P.fig_gain(rows)
    assert "1 時間足 n=50" in svg and "日足 n=20" not in svg


def test_fig_attrib_shows_both_additions_for_each_n():
    df = pd.DataFrame([dict(n=50, gain_beta=0.02, gain_long=0.015), dict(n=100, gain_beta=0.07, gain_long=0.1)])
    svg = P.fig_attrib(df)
    assert "50 本の平均を足す" in svg and "100 本の平均を足す" in svg
    assert "0.070 ポイント" in svg and "0.100 ポイント" in svg


class AvgSpy:
    """平均の計算の発行回数を数える Test Spy（注入点 smoothers を通す）。"""

    def __init__(self):
        self.issued = 0

    def __call__(self, tr, n):
        self.issued += 1
        return P.sma(tr, n)


def _issued_and_used(length, ns=(14, 20)):
    spy = AvgSpy()
    rows = P.smooth_rows(_tr(length), ns=ns, smoothers=(("単純平均", spy),))
    return spy.issued, len(rows)


def test_every_issued_average_reaches_the_output():
    issued, used = _issued_and_used(300)
    assert issued - used == 0


def test_issued_averages_do_not_grow_with_input_length():
    assert _issued_and_used(300)[0] == _issued_and_used(1200)[0]


def _follow_issued_and_used(length):
    spy = AvgSpy()
    rows = P.follow_rows([_tr(length)], methods=(("単純平均", lambda t: spy(t, 14)),))
    return spy.issued, len(rows)


def test_follow_rows_issues_only_what_reaches_the_output():
    issued, used = _follow_issued_and_used(300)
    assert issued - 3 * used == 0  # 1 行に 相関 1 回＋追いつく本数 2 回


def test_follow_rows_issued_does_not_grow_with_input_length():
    assert _follow_issued_and_used(300)[0] == _follow_issued_and_used(1200)[0]


def _trend_issued(length):
    spy = AvgSpy()
    tr = _tr(length)
    rows = P.trend_rows(tr, methods=(("a", lambda t: spy(t, 14)), ("b", lambda t: spy(t, 25))))
    return spy.issued, len(rows)


def test_every_issued_line_reaches_a_trend_row():
    issued, used = _trend_issued(400)
    assert issued - used == 0


def test_issued_lines_do_not_grow_with_input_length():
    assert _trend_issued(400)[0] == _trend_issued(1600)[0]


def test_best_method_count_counts_series_won_on_both_measures():
    tr = _tr(600)
    methods = (("良い", lambda t: P.sma(t, 25)), ("悪い", lambda t: np.full(len(t), 1.0) * (1 + np.sin(np.arange(len(t))))))
    assert P.best_method_count([tr, tr], methods=methods, name="良い") == 2
    assert P.best_method_count([tr], methods=methods, name="悪い") == 0
