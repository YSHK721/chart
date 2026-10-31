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


def test_remove_hours_decides_factors_in_the_period_and_aligns_each_hour():
    x = np.array([99.0, 2.0, 6.0, 4.0, 12.0])
    hour = np.array([1, 0, 1, 0, 1])
    seen = []

    def factor(ratio, h):
        seen.append(list(ratio))
        return np.array([2.0, 6.0])

    assert list(B.remove_hours(x, hour, 1, factor)) == [16.5, 1.0, 1.0, 2.0, 2.0]
    assert seen == [[2.0, 6.0, 4.0, 12.0]]


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
    a = ATTR.compare_models(is_, oos, 100)["coef_long"]
    b = ATTR.compare_models(is_, changed, 100)["coef_long"]
    assert np.array_equal(a, b)


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
