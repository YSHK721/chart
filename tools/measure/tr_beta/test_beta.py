"""beta.py の状態検証と計算量検定（CLAUDE.md の計算量テスト規約）。"""
import numpy as np
import pandas as pd

from indigators.btlm_trail.src.core import rolling_ols_window_end, window_end_scalar
from tools.measure.tr_beta import beta as B
from tools.measure.tr_beta import run as RUN
from tools.measure.tr_beta import run_attrib as ATTR


def rng_from_seed(seed):
    """乱数は必ずシードから作る（再現性）。"""
    return np.random.default_rng(seed)


def test_future_mean_excludes_the_current_bar():
    x = np.arange(10.0)
    want = (x[1] + x[2]) / 2  # 足 0 の次の 2 本の平均を手で計算した値
    assert B.future_mean(x, 2)[0] == want
    assert B.future_mean(x, 2)[7] == (x[8] + x[9]) / 2
    assert np.isnan(B.future_mean(x, 2)[8])


def test_past_mean_includes_the_current_bar():
    x = np.arange(10.0)
    assert B.past_mean(x, 2)[5] == (x[4] + x[5]) / 2


def test_beta_sign_follows_the_slope_and_marks_short_windows_as_zero():
    s = B.beta_sign(np.arange(10.0), 4)
    assert list(s[:3]) == [0, 0, 0] and s[3] == 1 and s[-1] == 1
    assert B.beta_sign(np.arange(10.0)[::-1], 4)[-1] == -1


def test_beta_sign_equals_the_rolling_calculation_of_btlm_trail():
    rng = rng_from_seed(0)
    x = rng.uniform(1, 2, 80)
    want = np.sign(rolling_ols_window_end(x, 10)[2])
    want[:9] = 0
    assert np.array_equal(B.beta_sign(x, 10), want)
    assert np.array_equal(B.beta_sign(x, 10, first=30), want[30:])


def test_beta_sign_does_not_use_future_bars():
    rng = rng_from_seed(0)
    x = rng.uniform(1, 2, 60)
    z = x.copy()
    z[30:] = 99.0
    assert np.array_equal(B.beta_sign(x, 10)[:30], B.beta_sign(z, 10)[:30])


def test_measures_reads_warm_up_for_a_but_not_for_y():
    x = np.arange(100.0)
    m = B.measures(x, 50, (20,))
    assert m["y"][0] == x[51:71].mean()
    assert m["a"][0] == x[31:51].mean()
    assert m[20][0] == 1
    assert len(m["y"]) == len(m["a"]) == len(m[20]) == 50


def test_measures_at_series_start_has_no_warm_up():
    m = B.measures(np.arange(50.0), 0, (20,))
    assert np.isnan(m["a"][18]) and m["a"][19] == np.arange(20.0).mean()
    assert m[20][18] == 0 and m[20][19] == 1


def test_range_pct_divides_by_the_previous_close():
    d = pd.DataFrame({"close": [100.0, 200.0, 400.0]})
    out = B.range_pct(d, lambda _: np.array([1.0, 4.0, 2.0]))
    assert np.isnan(out[0]) and out[1] == 4.0 and out[2] == 1.0


def test_hit_rate_and_mean_gap():
    sign = np.array([1.0, 1.0, -1.0, -1.0])
    target = np.array([1.0, -1.0, -1.0, -1.0])
    ok = np.ones(4, bool)
    assert B.hit_rate(sign, target, ok) == 0.75
    assert B.mean_gap(sign, np.array([3.0, 1.0, 0.0, 2.0]), ok) == 1.0


def test_hit_rate_and_mean_gap_ignore_bars_outside_ok():
    sign = np.array([1.0, 1.0, -1.0, -1.0])
    ok = np.array([True, False, True, False])
    assert B.hit_rate(sign, np.array([1.0, -1.0, -1.0, 1.0]), ok) == 1.0
    assert B.mean_gap(sign, np.array([3.0, 100.0, 1.0, -100.0]), ok) == 2.0


def test_fit_level_recovers_known_coefficients():
    rng = rng_from_seed(0)
    w = rng.uniform(0.5, 2.0, 200)
    y = np.exp(0.3) * w ** 0.7
    a, b = B.fit_level(y, w, np.ones(200, bool))
    assert abs(a - 0.3) < 1e-9 and abs(b - 0.7) < 1e-9
    assert np.allclose(B.level_residual(y, w, a, b), 0)


def test_fit_sign_recovers_the_half_gap():
    sign = np.array([1.0, -1.0] * 50)
    assert abs(B.fit_sign(0.2 * sign, sign, np.ones(100, bool)) - 0.2) < 1e-12


def test_relative_error_divides_by_the_mean_of_y():
    y = np.array([2.0, 2.0, 100.0])
    ok = np.array([True, True, False])
    assert B.relative_error(y, np.array([3.0, 3.0, 0.0]), ok) == 50.0


def test_two_sided_p_matches_a_hand_calculation():
    # 帰無の平均 1。外れは 2,0,0,2。実測 3 の外れは 2。外れ 2 以上が 2 個なので (1 + 2) / (4 + 1)。
    center, p = B.two_sided_p(3.0, np.array([-1.0, 1.0, 1.0, 3.0]))
    assert center == 1.0
    assert p == (1 + 2) / 5


def test_circular_test_finds_a_planted_relation_and_not_a_missing_one():
    rng = rng_from_seed(1)
    n = 3000
    sign = np.repeat(rng.choice([-1.0, 1.0], n // 30), 30)
    shifts = B.draw_shifts(n, 299, 100, rng)
    planted = 0.3 * sign + rng.normal(0, 1, n)
    assert B.circular_test(sign, planted, B.mean_gap, shifts)[2] < 0.05
    absent = rng.normal(0, 1, n)
    assert B.circular_test(sign, absent, B.mean_gap, shifts)[2] > 0.05


def test_circular_test_excludes_missing_targets_and_zero_signs_in_observed_and_null():
    sign = np.array([1.0, -1.0, 0.0, 1.0])
    target = np.array([1.0, 0.0, 9.0, np.nan])
    seen = []

    def stat(s, t, ok):
        seen.append((list(t), list(ok)))
        return 0.0

    B.circular_test(sign, target, stat, [1])
    assert seen[0][1] == [True, True, False, False]
    # 1 本ずらすと欠損は先頭へ移り、0 の sign は足 2 のまま
    assert seen[1] == ([0.0, 1.0, 0.0, 9.0], [False, True, False, True])


def test_draw_shifts_includes_both_ends():
    rng = rng_from_seed(0)
    s = B.draw_shifts(1000, 500, 250, rng)
    assert s.min() >= 250 and s.max() <= 750
    assert set(B.draw_shifts(10, 200, 4, rng)) == {4, 5, 6}


def _frame(stat, is_p, oos_p, oos_value=1.0):
    rows = []
    for period, ps, value in (("IS", is_p, 1.0), ("OOS", oos_p, oos_value)):
        for n, p in zip((20, 50, 100), ps):
            rows.append({"period": period, "tf": "1h", "n": n, stat: value, f"{stat}_null": 0.0, f"{stat}_p": p})
    return pd.DataFrame(rows)


def _passed(df, stat):
    return df[df[f"{stat}_pass"]].n.tolist()


def test_judge_applies_holm_only_to_the_pairs_that_passed_is():
    # IS の Holm 後: 0.003, 0.003, 0.9 → 2 組通過。OOS は 2 組で Holm: 小さい 0.01 × 2 = 0.02、大きい 0.06 × 1 = 0.06
    df = B.judge(_frame("D", [0.001, 0.001, 0.9], [0.06, 0.01, 0.0001]), "D")
    assert _passed(df, "D") == [50]
    assert np.isnan(df[(df.period == "OOS") & (df.n == 100)].D_holm.iloc[0])
    assert df[(df.period == "OOS") & (df.n == 20)].D_holm.iloc[0] == 0.06


def test_judge_leaves_the_pair_that_failed_is_out_of_the_oos_holm():
    # 2 組の Holm: 0.02 × 2 = 0.04、0.03 × 1 → 単調にして 0.04。不通過の組を混ぜて 3 組にすると 0.06 になり落ちる
    df = B.judge(_frame("D", [0.001, 0.001, 0.9], [0.03, 0.02, 0.5]), "D")
    assert _passed(df, "D") == [20, 50]


def test_judge_rejects_the_opposite_direction():
    df = B.judge(_frame("D", [0.001, 0.001, 0.9], [0.001, 0.001, 0.9], oos_value=-1.0), "D")
    assert _passed(df, "D") == []


def test_series_ends_targets_inside_the_period(monkeypatch):
    dates = pd.date_range("2020-01-01", periods=400, freq="D")
    dates = dates[dates.dayofweek < 5]
    close = 100.0 + np.arange(len(dates), dtype=float) * 0.1
    d = pd.DataFrame({"date": dates, "open": close, "high": close + 1, "low": close - 1, "close": close})
    monkeypatch.setattr(RUN, "load", lambda path, tf: d)
    end = "2020-10-01"
    s = RUN.series("", "1D", "2020-03-02", end)
    want_bars = int(((dates >= "2020-03-02") & (dates < end)).sum())
    assert s["bars"] == want_bars
    assert np.isnan(s["y"][-20:]).all() and np.isfinite(s["y"][-21])
    assert np.isfinite(s["a"][0])


class ObservedTest:
    """統計量の発行と、p の計算に届いた帰無の本数を数える Test Spy（注入点 stat・p_value を通す）。"""

    def __init__(self):
        self.issued = 0
        self.used = 0

    def stat(self, sign, target, ok):
        self.issued += 1
        return B.mean_gap(sign, target, ok)

    def p_value(self, obs, null):
        self.used += 1 + len(null)  # 実測 1 個と帰無の全本数
        return B.two_sided_p(obs, null)


def _observed(n, count=50):
    rng = rng_from_seed(2)
    spy = ObservedTest()
    sign = rng.choice([-1.0, 1.0], n)
    B.circular_test(sign, rng.normal(0, 1, n), spy.stat, B.draw_shifts(n, count, 10, rng), spy.p_value)
    return spy


def test_every_issued_stat_reaches_the_p_value():
    spy = _observed(300)
    assert spy.issued - spy.used == 0


def test_issued_stats_do_not_grow_with_input_length():
    assert _observed(300).issued == _observed(1200).issued


class WindowSpy:
    """1 窓の OLS の発行回数を数える Test Spy（注入点 window を通す）。"""

    def __init__(self):
        self.issued = 0

    def __call__(self, z):
        self.issued += 1
        return window_end_scalar(z)


class MeanSpy:
    """平均に渡した足の本数を数える Test Spy（注入点 mean を通す）。"""

    def __init__(self):
        self.issued = 0

    def __call__(self, x, h):
        self.issued += len(x)
        return B.rolling_mean(x, h)


def _measured(warm, bars=120, n=20):
    rng = rng_from_seed(3)
    window, mean = WindowSpy(), MeanSpy()
    m = B.measures(rng.uniform(1, 2, warm + bars), warm, (n,), window=window, mean=mean)
    used_windows = int(np.count_nonzero(m[n]))
    return window.issued, used_windows, mean.issued


def test_every_issued_window_reaches_the_output():
    issued, used, _ = _measured(warm=300)
    assert issued - used == 0


def test_issued_windows_do_not_grow_with_warm_up_length():
    assert _measured(warm=300)[0] == _measured(warm=3000)[0]


def test_issued_means_do_not_grow_with_warm_up_length():
    assert _measured(warm=300)[2] == _measured(warm=3000)[2]


def _model_series(length, seed):
    rng = rng_from_seed(seed)
    w = rng.uniform(0.5, 2.0, length)
    l = rng.uniform(0.5, 2.0, length)
    sign = rng.choice([-1.0, 1.0], length)
    y = np.exp(0.1 + 0.5 * np.log(w) + 0.25 * np.log(l))  # 長い平均だけに情報がある
    return {"y": y, "w": w, "l100": l, 100: sign}


def test_fit_log_model_recovers_known_coefficients():
    s = _model_series(300, 0)
    coef = B.fit_log_model(s, 100, ("w", "l"), np.ones(300, bool))
    assert np.allclose(coef, [0.1, 0.5, 0.25])
    assert np.allclose(B.predict_log_model(s, 100, ("w", "l"), coef), s["y"])


def test_compare_models_credits_the_long_average_when_only_it_has_information():
    r = ATTR.compare_models(_model_series(400, 1), _model_series(400, 2), 100)
    assert r["gain"]["w"] == 0.0
    assert r["gain"]["long"] > 1.0
    assert abs(r["gain"]["beta"]) < 0.5


def test_compare_models_fits_coefficients_only_on_is():
    is_ = _model_series(400, 1)
    oos = _model_series(400, 2)
    changed = dict(oos, y=oos["y"] * 3.0)
    a = ATTR.compare_models(is_, oos, 100)["coefs"]["long"]
    b = ATTR.compare_models(is_, changed, 100)["coefs"]["long"]
    assert np.array_equal(a, b)


def test_compare_models_measures_errors_on_oos():
    is_ = _model_series(400, 1)
    oos = _model_series(400, 2)
    want = np.mean(np.abs(oos["y"] * 3.0 - oos["y"])) / np.mean(oos["y"] * 3.0) * 100  # 見積もりは y、実際は 3 y
    assert abs(ATTR.compare_models(is_, dict(oos, y=oos["y"] * 3.0), 100)["errs"]["long"] - want) < 1e-6


def test_usable_excludes_zero_signs_and_missing_values():
    s = {"y": np.array([1.0, 1.0, np.nan, 1.0]), "w": np.ones(4), "l100": np.array([1.0, 1.0, 1.0, np.nan]),
         100: np.array([1.0, 0.0, 1.0, 1.0])}
    assert list(ATTR.usable(s, 100)) == [True, False, False, False]


def test_compare_models_correlates_the_sign_with_w_over_l():
    s = _model_series(400, 1)
    up = dict(s, w=s["l100"] * np.exp(0.5 * s[100]))
    r = ATTR.compare_models(s, up, 100)
    assert r["corr"] > 0.99


def test_model_terms_keep_the_sign_of_beta():
    s = _model_series(10, 1)
    assert np.array_equal(B.model_terms(s, 100, ("b",))[0], s[100])


def test_model_terms_build_only_the_requested_terms():
    s = {"y": np.ones(3), 100: np.ones(3), "w": np.ones(3)}  # l100 が無くても w・b だけなら作れる
    assert len(B.model_terms(s, 100, ("w", "b"))) == 2


def test_partial_slope_is_the_regression_slope_through_the_origin():
    r = np.array([1.0, -1.0, 2.0, 99.0])
    e = np.array([2.0, -2.0, 4.0, 0.0])
    assert B.partial_slope(r, e, np.array([True, True, True, False])) == 2.0


def _partial_series(length, seed, beta_effect):
    s = _model_series(length, seed)
    rng = rng_from_seed(seed + 100)
    s["y"] = s["y"] * np.exp(beta_effect * s[100] + rng.normal(0, 0.2, length))
    return s


def test_partial_test_finds_beta_only_when_beta_has_its_own_effect():
    with_effect = ATTR.partial_test(_partial_series(1500, 1, 0.1), _partial_series(1500, 2, 0.1), 100, "b", 50)
    without = ATTR.partial_test(_partial_series(1500, 1, 0.0), _partial_series(1500, 2, 0.0), 100, "b", 50)
    assert with_effect["b_p_is"] < 0.05 and with_effect["b_p_oos"] < 0.05
    assert abs(with_effect["b_slope_oos"] - 0.1) < 0.03
    assert without["b_p_is"] > 0.05 and without["b_p_oos"] > 0.05


def test_partial_residuals_mark_unusable_bars():
    s = _partial_series(50, 1, 0.1)
    s[100] = s[100].copy()
    s[100][3] = 0.0
    r, e = ATTR.partial_residuals(s, 100, "b", np.zeros(3), np.zeros(3))
    assert r[3] == 0.0 and np.isnan(e[3]) and np.isfinite(e[4])


def test_partial_residuals_remove_the_other_terms_from_both_sides():
    s = _partial_series(50, 1, 0.1)
    r, e = ATTR.partial_residuals(s, 100, "b", np.array([0.5, 0.0, 0.0]), np.array([1.0, 0.0, 0.0]))
    assert r[4] == s[100][4] - 1.0
    assert e[4] == np.log(s["y"][4]) - 0.5
    log_w = np.log(s["w"][4])
    r2, _ = ATTR.partial_residuals(s, 100, "b", np.zeros(3), np.array([0.0, 2.0, 0.0]))
    assert r2[4] == s[100][4] - 2.0 * log_w


def test_partial_test_decides_the_removed_terms_only_on_is():
    is_ = _partial_series(600, 1, 0.1)
    oos = _partial_series(600, 2, 0.1)
    ok = ATTR.usable(is_, 100)
    y_coef = B.fit_log_model(is_, 100, ("w", "l"), ok)
    v_coef = B.fit_linear(is_[100], is_, 100, ("w", "l"), ok)
    r, e = ATTR.partial_residuals(oos, 100, "b", y_coef, v_coef)
    want = np.sum(r * e) / np.sum(r * r)
    assert abs(ATTR.partial_test(is_, oos, 100, "b", 50)["b_slope_oos"] - want) < 1e-9


def _brute_force(r, e, margin):
    finite = np.isfinite(e)
    vals = np.where(finite, e, 0.0)
    ok = r != 0
    obs = B.partial_slope(r, vals, finite & ok)
    null = np.array([B.partial_slope(r, np.roll(vals, s), np.roll(finite, s) & ok) for s in range(margin, len(r) - margin + 1)])
    return (obs, *B.two_sided_p(obs, null))


def test_exact_partial_test_equals_counting_every_shift():
    rng = rng_from_seed(7)
    r = rng.normal(0, 1, 400)
    r[::13] = 0.0
    e = 0.2 * r + rng.normal(0, 1, 400)
    e[::17] = np.nan
    got = B.exact_partial_test(r, e, 50)
    assert np.allclose(got, _brute_force(r, e, 50))


class FftSpy:
    """畳み込みの発行回数を数える Test Spy（注入点 rfft を通す）。"""

    def __init__(self):
        self.issued = 0

    def __call__(self, x):
        self.issued += 1
        return np.fft.rfft(x)


def _fft_issued(length):
    rng = rng_from_seed(8)
    spy = FftSpy()
    r = rng.normal(0, 1, length)
    B.exact_partial_test(r, rng.normal(0, 1, length), 10, rfft=spy)
    return spy.issued


def test_exact_partial_test_issues_do_not_grow_with_input_length_or_margin():
    assert _fft_issued(300) == _fft_issued(2400)


def test_gain_interval_is_zero_for_identical_models_and_holds_a_constant_gain():
    rng = rng_from_seed(4)
    y = np.full(100, 2.0)
    week = np.repeat(np.arange(10), 10)
    assert B.gain_interval(y, y + 1, y + 1, week, 50, rng) == (0.0, 0.0)
    lo, hi = B.gain_interval(y, y + 1, y + 0.5, week, 50, rng)  # 外れ 50% → 25% で改善 25 ポイント
    assert abs(lo - 25.0) < 1e-9 and abs(hi - 25.0) < 1e-9


def test_gain_interval_divides_by_the_y_of_the_chosen_weeks():
    rng = rng_from_seed(9)
    week = np.repeat(np.arange(2), 10)
    y = np.where(week == 0, 1.0, 3.0)
    base = y + 1.0  # どの足も外れ 1
    pred = y + 0.0  # 外れ 0
    lo, hi = B.gain_interval(y, base, pred, week, 400, rng, level=1.0)
    # 全部 0 週なら 1 ÷ 1、全部 1 週なら 1 ÷ 3（%ポイント）
    assert abs(hi - 100.0) < 1e-9 and abs(lo - 100.0 / 3) < 1e-9


def test_gain_interval_resamples_whole_weeks():
    rng = rng_from_seed(10)
    week = np.repeat(np.arange(4), 10)
    y = np.ones(40)
    # 週の中で良い足と悪い足が相殺する。週で再標本すれば幅は 0、足で再標本すると幅が出る
    pred = np.where(np.arange(40) % 2 == 0, 1.0, 1.5)
    base = np.full(40, 1.25)
    lo, hi = B.gain_interval(y, base, pred, week, 200, rng)
    assert abs(lo) < 1e-9 and abs(hi) < 1e-9


def test_gain_interval_narrows_with_a_lower_level():
    week = np.repeat(np.arange(20), 5)
    y = np.ones(100)
    pred = 1 + 0.5 * (week % 2)
    base = np.full(100, 1.25)
    wide = B.gain_interval(y, base, pred, week, 2000, rng_from_seed(11), level=0.95)
    narrow = B.gain_interval(y, base, pred, week, 2000, rng_from_seed(11), level=0.5)
    assert wide[1] - wide[0] > narrow[1] - narrow[0]


def test_attrib_row_states_the_directions_of_each_difference():
    is_ = dict(_partial_series(800, 1, 0.0), week=np.repeat(np.arange(80), 10))
    oos = dict(_partial_series(800, 2, 0.0), week=np.repeat(np.arange(80), 10))
    row = ATTR.attrib_row(is_, oos, 100, rng_from_seed(12), count=200, margin=50)
    assert abs(row["gain_incr"] - (row["gain_both"] - row["gain_long"])) < 1e-12
    assert row["gain_long"] > 0.5  # 長い平均だけに情報がある合成データ
    assert row["long_minus_beta_lo"] > 0  # 長い平均の側から β を引いた向き
    assert abs(row["coef_is_alone"]) < 0.05 and abs(row["coef_is_with_long"]) < 0.05


def test_attrib_row_reads_the_beta_coefficient_of_each_model():
    is_ = dict(_partial_series(800, 1, 0.3), week=np.repeat(np.arange(80), 10))
    oos = dict(_partial_series(800, 2, 0.1), week=np.repeat(np.arange(80), 10))
    row = ATTR.attrib_row(is_, oos, 100, rng_from_seed(13), count=50, margin=50)
    assert abs(row["coef_is_alone"] - 0.3) < 0.05 and abs(row["coef_is_with_long"] - 0.3) < 0.05
    assert abs(row["coef_oos_alone"] - 0.1) < 0.05 and abs(row["coef_oos_with_long"] - 0.1) < 0.05


def _partial_issued(length):
    spy = SolveSpy()
    r = ATTR.partial_test(_partial_series(length, 1, 0.1), _partial_series(length, 2, 0.1), 100, "b", 20, solve=spy)
    return spy.issued, 2 * sum(k.startswith("b_slope") for k in r) // 2  # 傾きはどちらの期間も同じ 2 つの回帰から作る


def test_partial_test_issues_the_two_fits_only_once():
    issued, periods = _partial_issued(300)
    assert periods == 2 and issued == len(ATTR.OTHERS)


def test_partial_test_issued_fits_do_not_grow_with_input_length():
    assert _partial_issued(300)[0] == _partial_issued(1200)[0]


def test_gain_interval_widens_when_weeks_disagree():
    rng = rng_from_seed(5)
    y = np.full(100, 1.0)
    week = np.repeat(np.arange(10), 10)
    pred = np.where(week % 2 == 0, 1.5, 1.0)  # 外れは偶数週 0.5・奇数週 0
    base = np.full(100, 1.25)  # 外れはどの週も 0.25
    lo, hi = B.gain_interval(y, base, pred, week, 500, rng)
    assert lo < hi


def test_series_uses_the_given_hour_factors(monkeypatch):
    dates = pd.date_range("2020-01-01", periods=2000, freq="h")
    close = 100.0 + 0.001 * np.arange(2000)
    d = pd.DataFrame({"date": dates, "open": close, "high": close + 1, "low": close - 1, "close": close})
    monkeypatch.setattr(RUN, "load", lambda path, tf: d)
    own = RUN.series("", "1h", "2020-01-10", "2020-03-01", ns=(20,))
    doubled = RUN.series("", "1h", "2020-01-10", "2020-03-01", ns=(20,), hours=own["hours"] * 2)
    assert np.allclose(doubled["w"][300:], own["w"][300:] / 2)


def test_series_builds_long_means_from_past_bars_only(monkeypatch):
    dates = pd.date_range("2020-01-01", periods=400, freq="D")
    dates = dates[dates.dayofweek < 5]
    rng = rng_from_seed(6)
    close = 100.0 + rng.uniform(0, 5, len(dates))
    d = pd.DataFrame({"date": dates, "open": close, "high": close + 1, "low": close - 1, "close": close})
    monkeypatch.setattr(RUN, "load", lambda path, tf: d)
    s = RUN.series("", "1D", "2020-06-01", "2020-12-01", ns=(50,), with_long=True)
    full = RUN.series("", "1D", "2020-01-01", "2020-12-01", ns=(50,), with_long=True)
    k = len(full["y"]) - len(s["y"])
    assert np.isfinite(s["l50"][0])
    assert np.allclose(s["l50"], full["l50"][k:])
    assert s["week"][0] == 0 and s["week"][-1] >= 20


class SolveSpy:
    """最小二乗の発行回数を数える Test Spy（注入点 solve を通す）。"""

    def __init__(self):
        self.issued = 0

    def __call__(self, x, y, rcond=None):
        self.issued += 1
        return np.linalg.lstsq(x, y, rcond=rcond)


def _solves(length):
    spy = SolveSpy()
    r = ATTR.compare_models(_model_series(length, 1), _model_series(length, 2), 100, solve=spy)
    return spy.issued, len(r["errs"])


def test_every_issued_fit_reaches_an_error():
    issued, used = _solves(300)
    assert issued - used == 0


def test_issued_fits_do_not_grow_with_input_length():
    assert _solves(300)[0] == _solves(1200)[0]
