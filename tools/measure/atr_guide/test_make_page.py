"""make_page.py の平均の比較の状態検証と計算量検定（CLAUDE.md の計算量テスト規約）。"""
import numpy as np
import pandas as pd

from indigators.btlm_trail.src.core import rolling_ols_window_end, window_end_scalar
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


def _attrib_row(n, hours, base):
    row = dict(n=n, hours=hours, corr=base + 0.5, b_p_is=0.04, b_p_oos=base, l_p_oos=0.0002,
               coef_is_alone=-base, coef_is_with_long=-base / 2, coef_oos_alone=-base / 4, coef_oos_with_long=-base / 8)
    for i, k in enumerate(("gain_beta", "gain_long", "gain_incr", "long_minus_beta")):
        row.update({k: base + i, f"{k}_lo": base + i - 1, f"{k}_hi": base + i + 1})
    return row


def _attrib_frame():
    return pd.DataFrame([_attrib_row(50, "oos_hours", 0.1), _attrib_row(100, "oos_hours", 0.2),
                         _attrib_row(50, "is_hours", 0.3), _attrib_row(100, "is_hours", 0.4)])


def test_fig_attrib_draws_each_interval_around_zero():
    svg = P.fig_attrib(_attrib_frame())
    assert "50 本の平均を足す" in svg and "100 本の平均を足す" in svg
    assert svg.count('class="whisker') == 12 and "改善なし 0" in svg
    assert "OOS の係数" in svg and "IS の係数" in svg


def test_attrib_values_keep_each_condition_apart():
    v = P.attrib_values(_attrib_frame())
    assert v["BETA_O50"] == "-0.90〜+1.10" and v["BETA_O100"] == "-0.80〜+1.20"
    assert v["BETA_I50"] == "-0.70〜+1.30" and v["BETA_I100"] == "-0.60〜+1.40"
    assert v["INCR_O50"] == "+1.10〜+3.10" and v["LONG_O50"] == "+0.10〜+2.10" and v["DIFF_O50"] == "+2.10〜+4.10"
    assert v["BP_I100"] == "0.400" and v["LP_O50"] == "0.001 未満"
    assert v["COEF_IS_100"] == "-0.200 → -0.100" and v["COEF_I50"] == "-0.075 → -0.037"
    assert v["CORR_100"] == "0.70" and v["BP_IS_50"] == "0.040"


def test_grid_methods_name_the_n_they_compute():
    tr = _tr(300)
    methods = dict(P.GRID_METHODS)
    assert np.array_equal(methods["単純平均 14"](tr), P.sma(tr, 14), equal_nan=True)
    assert np.array_equal(methods["ワイルダーの平滑 50"](tr), P.wilder(tr, 50), equal_nan=True)
    assert np.array_equal(methods["btlm_trail の計算 25"](tr)[P.TREND_START:], P.ols_end(tr, 25)[P.TREND_START:])
    names = {label: {k.rsplit(" ", 1)[1] for k in methods if k.startswith(label)} for label in ("単純平均", "ワイルダーの平滑", P.OLS_NAME)}
    assert names["単純平均"] == names["ワイルダーの平滑"] == names[P.OLS_NAME] == {str(n) for n in P.GRID_NS}


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
    return spy.issued, sum(2 + len(corr) for _, _, _, corr in rows)  # 出力の値（追いつく本数 2 つと相関）を数える


def test_follow_rows_issues_only_what_reaches_the_output():
    issued, used = _follow_issued_and_used(300)
    assert issued - used == 0


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


def test_best_rows_report_the_best_and_the_best_of_ols():
    tr = _tr(600)
    bad = lambda t: 1 + np.sin(np.arange(len(t)))
    methods = (("単純平均 25", lambda t: P.sma(t, 25)), ("btlm_trail の計算 1", bad), ("btlm_trail の計算 2", lambda t: 2 * bad(t) + 1))
    r = P.best_rows([("系列", tr)], methods=methods)[0]
    assert r["label"] == "系列" and r["best_one"] == "単純平均 25" and r["best_twenty"] == "単純平均 25"
    assert r["ols_one"].startswith("btlm_trail")
    assert r["ols_err_one"] > r["err_one"]
    assert P.ols_wins([r]) == 0
    assert P.ols_wins([dict(best_one="btlm_trail の計算 5", best_twenty="単純平均 5")]) == 1


def test_ols_end_equals_the_rolling_calculation_of_btlm_trail():
    tr = _tr(120)
    want = rolling_ols_window_end(tr, 10)[0]
    got = P.ols_end(tr, 10)
    assert np.isnan(got[:9]).all() and np.array_equal(got[9:], want[9:])
    assert np.isnan(P.ols_end(tr, 10, first=50)[:50]).all()
    assert np.array_equal(P.ols_end(tr, 10, first=50)[50:], want[50:])


def test_ols_end_of_a_straight_line_is_the_line():
    assert abs(P.ols_end(np.arange(20.0) * 2 + 1, 5)[-1] - 39.0) < 1e-9


def test_next_errors_twenty_uses_the_mean_of_the_next_twenty_bars():
    tr = np.r_[np.full(300, 1.0), np.full(300, 2.0)]
    a = np.ones(600)
    f = pd.Series(tr).rolling(20).mean().shift(-20).to_numpy()
    ok = np.isfinite(f)
    k = np.median(f[ok])
    want = np.mean(np.abs(f[ok] - k)) / f[ok].mean() * 100
    assert abs(P.next_errors(tr, a, start=0)[1] - want) < 1e-9


def test_next_errors_ignore_bars_before_start_and_non_positive_estimates():
    tr = np.r_[np.full(300, 100.0), np.full(300, 1.0)]
    assert P.next_errors(tr, np.ones(600), start=310) == (0.0, 0.0)
    a = np.ones(600)
    a[:310] = -1.0
    assert P.next_errors(tr, a, start=0) == (0.0, 0.0)


def test_trend_rows_order_of_columns():
    tr = np.r_[np.full(300, 1.0), np.full(300, 2.0)]
    line = 3.0 + np.sin(np.arange(600) / 50.0)
    name, lags, move, e1, e20 = P.trend_rows(tr, methods=(("線", lambda t: line),))[0]
    assert name == "線" and len(lags) == len(P.CENTERS) and move > 0
    assert e1 == P.next_errors(tr, line)[0] and e20 == P.next_errors(tr, line)[1]


def test_trend_rows_divide_the_move_by_the_mean_of_the_line():
    line = np.r_[np.full(300, 10.0), np.linspace(10, 20, 300)]
    tr = _tr(600)
    _, _, move, _, _ = P.trend_rows(tr, methods=(("線", lambda t: line),))[0]
    want = np.mean(np.abs(np.diff(line[200:]))) / np.mean(line[200:]) * 100
    assert abs(move - want) < 1e-12


def test_lag_to_center_reports_a_line_that_moves_first_as_negative():
    x = np.sin(np.arange(2000) / 60.0) + 2.0
    a = pd.Series(x).shift(-5).to_numpy()
    assert P.lag_to_center(x, a, start=100) == -5


def test_beta_rows_take_d_from_oos():
    df = pd.DataFrame([
        dict(period="IS", tf="1D", n=20, H=0.6, H_null=0.5, H_pass=False, D=9.0, D_pass=False, err_gain=0.0, usable=False),
        dict(period="OOS", tf="1D", n=20, H=0.6, H_null=0.5, H_pass=True, D=-0.2, D_pass=False, err_gain=-0.1, usable=False),
    ])
    r = P.beta_rows(df)[0]
    assert r["d_oos"] == -0.2 and r["tf"] == "日足" and r["direction"] == "同じ向き"


def test_fig_hit_draws_is_hollow_and_oos_filled():
    rows = [dict(tf="日足", n=20, h_is=60.0, h_oos=40.0, h_pass=True, direction="同じ向き")]
    svg = P.fig_hit(rows)
    x = lambda v: 110 + (v - 30.0) / 35.0 * 390
    assert f'<circle cx="{x(60.0):.1f}" cy="36" r="5" class="m-is"/>' in svg
    assert f'<circle cx="{x(40.0):.1f}" cy="36" r="5" class="m-real"/>' in svg


class WindowSpy:
    """1 窓の OLS の発行回数を数える Test Spy（注入点 window を通す）。"""

    def __init__(self):
        self.issued = 0

    def __call__(self, z):
        self.issued += 1
        return window_end_scalar(z)


def _ols_issued(warm, bars=100, n=20):
    spy = WindowSpy()
    out = P.ols_end(_tr(warm + bars), n, warm, window=spy)
    return spy.issued, int(np.isfinite(out).sum())


def test_every_issued_window_reaches_the_line():
    issued, used = _ols_issued(300)
    assert issued - used == 0


def test_issued_windows_do_not_grow_with_warm_up_length():
    assert _ols_issued(300)[0] == _ols_issued(3000)[0]


def _best_issued(length):
    spy = AvgSpy()
    methods = (("単純平均 14", lambda t: spy(t, 14)), ("btlm_trail の計算 25", lambda t: spy(t, 25)))
    rows = P.best_rows([("系列", _tr(length))], methods=methods)
    return spy.issued, len(methods) * len(rows)


def test_every_issued_line_reaches_the_best_rows():
    issued, used = _best_issued(400)
    assert issued - used == 0


def test_issued_best_lines_do_not_grow_with_input_length():
    assert _best_issued(400)[0] == _best_issued(1600)[0]
