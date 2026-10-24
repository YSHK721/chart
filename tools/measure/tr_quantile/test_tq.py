"""tq.py の状態検証と計算量検定（CLAUDE.md の計算量テスト規約）。"""
import numpy as np
import pytest

from tools.measure.tr_quantile import tq as Q


def rng_from_seed(seed):
    """乱数は必ずシードから作る（再現性）。"""
    return np.random.default_rng(seed)


def _data(n, seed=0):
    rng = rng_from_seed(seed)
    y = rng.gamma(2.0, 0.5, size=n)
    v = rng.uniform(0.2, 2.0, size=n)
    r = rng.uniform(0.5, 1.5, size=n)
    return y, v, r


def test_true_range_takes_the_largest_of_three():
    h = np.array([10, 12, 11.0]); l = np.array([9, 11, 10.0]); c = np.array([9.5, 11.5, 10.5])
    np.testing.assert_allclose(Q.true_range(h, l, c), [1, 2.5, 1.5])


def test_features_target_is_next_bar_true_range_over_close():
    rng = rng_from_seed(1)
    c = 100 + np.cumsum(rng.normal(0, 1, 60)); h = c + 1; l = c - 1
    y, v, r, ok = Q.features(h, l, c)
    idx = np.flatnonzero(ok)
    tr = Q.true_range(h, l, c)
    assert y[0] == pytest.approx(tr[idx[0] + 1] / c[idx[0]] * 100)


def test_cells_for_each_method():
    v = np.array([0.1, 0.5, 0.9]); r = np.array([0.9, 1.0, 1.2]); edges = np.array([0.3, 0.6, 0.8, 0.85])
    assert Q.cells("A", v, r, edges).tolist() == [0, 0, 0]
    assert Q.cells("C", v, r, edges).tolist() == [0, 1, 4]
    assert Q.cells("D", v, r, edges).tolist() == [0, 3, 9]


def test_fit_and_threshold_b_scales_by_current_v():
    y, v, r = _data(1000)
    edges = np.quantile(v, [.2, .4, .6, .8])
    p = Q.fit("B", y, v, r, 0.8, edges)
    assert p[0] == pytest.approx(np.quantile(y / v, 0.8))
    np.testing.assert_allclose(Q.threshold("B", p, v[:3], r[:3], edges), p[0] * v[:3])


def test_fit_c_is_quantile_within_each_bucket():
    y, v, r = _data(1000, 2)
    edges = np.quantile(v, [.2, .4, .6, .8])
    p = Q.fit("C", y, v, r, 0.95, edges)
    b = np.digitize(v, edges)
    for k in range(5):
        assert p[k] == pytest.approx(np.quantile(y[b == k], 0.95))


# ---- 計算量検定（注入点 quantile を数える） ----

class QuantileSpy:
    def __init__(self):
        self.issued = 0

    def __call__(self, x, q):
        self.issued += 1
        return np.quantile(x, q)


def _fit_all(n):
    y, v, r = _data(n, 3)
    edges = np.quantile(v, [.2, .4, .6, .8])
    spy = QuantileSpy()
    used = 0
    for m in Q.METHODS:
        p = Q.fit(m, y, v, r, 0.8, edges, quantile=spy)
        used += int(np.isfinite(p).sum())
    return spy.issued, used


def test_no_quantile_is_computed_that_is_not_used():
    issued, used = _fit_all(2000)
    assert issued - used == 0


def test_quantile_count_does_not_grow_with_series_length():
    assert _fit_all(1000)[0] == _fit_all(8000)[0]
