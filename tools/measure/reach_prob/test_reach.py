"""reach.py の状態検証と計算量検定（CLAUDE.md の計算量テスト規約）。"""
import math

import numpy as np
import pytest

from tools.measure.reach_prob import reach as R


def rng_from_seed(seed):
    """乱数は必ずシードから作る（再現性）。"""
    return np.random.default_rng(seed)


def _bars(n, seed=0):
    rng = rng_from_seed(seed)
    c = 1000 + np.cumsum(rng.normal(0, 2, size=n))
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) + rng.uniform(0, 1, size=n)
    l = np.minimum(o, c) - rng.uniform(0, 1, size=n)
    return h, l, c


def test_atr_is_mean_true_range_including_gap_from_previous_close():
    h = np.array([10, 12, 11.0]); l = np.array([9, 11, 10.0]); c = np.array([9.5, 11.5, 10.5])
    # 真の値幅: 1, max(1, 2.5, 1.5)=2.5, max(1, 0.5, 1.5)=1.5
    np.testing.assert_allclose(R.atr(h, l, c, window=2), [np.nan, 1.75, 2.0])


def test_forward_extreme_excludes_the_start_bar():
    x = np.array([5, 1, 3, 2, 9.0])
    np.testing.assert_allclose(R.forward_extreme(x, 2, "max"), [3, 3, 9, np.nan, np.nan])
    np.testing.assert_allclose(R.forward_extreme(x, 2, "min"), [1, 2, 2, np.nan, np.nan])


def test_reach_counts_match_direct_count():
    h, l, c = _bars(400, 1)
    day = np.arange(400) // 50
    start = np.arange(400) >= 20
    out = R.reach_counts(h, l, c, start, day, 8, xs=(0.5, 1.0), ts=(5,))
    a = R.atr(h, l, c)
    n, hits, _ = out[(5, "up")]
    want_n = want_hit = 0
    for t in range(20, 395):
        want_n += 1
        want_hit += h[t + 1:t + 6].max() >= c[t] + 1.0 * a[t]
    assert n.sum() == want_n and hits[1].sum() == want_hit


def test_formula_is_random_walk_first_passage():
    h, l, c = _bars(300, 2)
    day = np.zeros(300, int)
    start = np.zeros(300, bool); start[100] = True
    out = R.reach_counts(h, l, c, start, day, 1, xs=(1.0,), ts=(5,))
    a, s = R.atr(h, l, c)[100], R.diff_std(c)[100]
    z = a / (s * math.sqrt(5))
    assert out[(5, "up")][2][0, 0] == pytest.approx(2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2)))))


# ---- 計算量検定（注入点 forward を数える） ----

class ForwardSpy:
    def __init__(self):
        self.issued = 0

    def __call__(self, x, t, kind):
        self.issued += 1
        return R.forward_extreme(x, t, kind)


def _run(n, xs):
    h, l, c = _bars(n, 3)
    day = np.arange(n) // 100
    spy = ForwardSpy()
    out = R.reach_counts(h, l, c, np.arange(n) >= 20, day, int(day.max()) + 1, xs=xs, ts=(1, 5, 20), forward=spy)
    return spy.issued, len(out)


def test_no_forward_extreme_is_issued_that_is_not_used():
    issued, used = _run(1000, R.XS)
    assert issued - used == 0


def test_forward_extreme_count_does_not_grow_with_number_of_levels():
    assert _run(1000, (1.0,))[0] == _run(1000, R.XS)[0]


def test_forward_extreme_count_does_not_grow_with_series_length():
    assert _run(500, R.XS)[0] == _run(4000, R.XS)[0]
