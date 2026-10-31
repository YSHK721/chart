"""beta.py の状態検証と計算量検定（CLAUDE.md の計算量テスト規約）。"""
import numpy as np
import pandas as pd

from tools.measure.tr_beta import beta as B


def rng_from_seed(seed):
    """乱数は必ずシードから作る（再現性）。"""
    return np.random.default_rng(seed)


def test_future_mean_excludes_the_current_bar():
    x = np.arange(10.0)
    want = (x[1] + x[2]) / 2  # 足 0 の次の 2 本の平均を手で計算した値
    assert B.future_mean(x, 2)[0] == want
    assert np.isnan(B.future_mean(x, 2)[-1])


def test_past_mean_includes_the_current_bar():
    x = np.arange(10.0)
    assert B.past_mean(x, 2)[5] == (x[4] + x[5]) / 2


def test_future_mean_is_not_changed_by_past_bars():
    rng = rng_from_seed(0)
    x = rng.uniform(1, 2, 50)
    z = x.copy()
    z[:10] += 5
    assert B.future_mean(z, 5)[10] == x[11:16].mean()


def test_beta_sign_follows_the_slope_and_marks_short_windows_as_zero():
    s = B.beta_sign(np.arange(10.0), 4)
    assert list(s[:3]) == [0, 0, 0] and s[3] == 1 and s[-1] == 1
    assert B.beta_sign(np.arange(10.0)[::-1], 4)[-1] == -1


def test_beta_sign_does_not_use_future_bars():
    rng = rng_from_seed(0)
    x = rng.uniform(1, 2, 60)
    z = x.copy()
    z[30:] = 99.0
    assert np.array_equal(B.beta_sign(x, 10)[:30], B.beta_sign(z, 10)[:30])


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


def test_relative_error():
    y = np.array([2.0, 2.0])
    assert B.relative_error(y, np.array([1.0, 3.0]), np.ones(2, bool)) == 50.0


def test_circular_test_finds_a_planted_relation_and_not_a_missing_one():
    rng = rng_from_seed(1)
    n = 3000
    sign = np.repeat(rng.choice([-1.0, 1.0], n // 30), 30)
    shifts = B.draw_shifts(n, 299, 100, rng)
    planted = 0.3 * sign + rng.normal(0, 1, n)
    assert B.circular_test(sign, planted, B.mean_gap, shifts)[2] < 0.05
    absent = rng.normal(0, 1, n)
    assert B.circular_test(sign, absent, B.mean_gap, shifts)[2] > 0.05


def test_circular_test_excludes_missing_targets_and_zero_signs():
    sign = np.array([1.0, -1.0, 0.0, 1.0])
    target = np.array([1.0, 0.0, 9.0, np.nan])
    seen = []
    B.circular_test(sign, target, lambda s, t, ok: seen.append(ok.copy()) or 0.0, [])
    assert list(seen[0]) == [True, True, False, False]


def test_draw_shifts_stays_in_the_margin():
    s = B.draw_shifts(1000, 500, 250, rng_from_seed(0))
    assert s.min() >= 250 and s.max() <= 750


class StatSpy:
    """統計量の発行回数を数える Test Spy（注入点 stat を通す）。"""

    def __init__(self):
        self.issued = 0

    def __call__(self, sign, target, ok):
        self.issued += 1
        return B.mean_gap(sign, target, ok)


def _issued(n, count=50):
    rng = rng_from_seed(2)
    spy = StatSpy()
    sign = rng.choice([-1.0, 1.0], n)
    B.circular_test(sign, rng.normal(0, 1, n), spy, B.draw_shifts(n, count, 10, rng))
    return spy.issued, count + 1  # 実測 1 回＋帰無の分（どれも p の計算に使う）


def test_every_issued_stat_reaches_the_output():
    issued, used = _issued(300)
    assert issued - used == 0


def test_issued_stats_do_not_grow_with_input_length():
    assert _issued(300)[0] == _issued(1200)[0]


class FitSpy:
    """OLS の発行回数を数える Test Spy（注入点 fit を通す）。"""

    def __init__(self):
        self.issued = 0

    def __call__(self, x, n):
        self.issued += 1
        from indigators.btlm_trail.src.core import rolling_ols_window_end
        return rolling_ols_window_end(x, n)


def test_beta_sign_issues_the_fit_once_per_series():
    rng = rng_from_seed(0)
    for length in (50, 200):
        spy = FitSpy()
        B.beta_sign(rng.uniform(1, 2, length), 10, fit=spy)
        assert spy.issued == 1
