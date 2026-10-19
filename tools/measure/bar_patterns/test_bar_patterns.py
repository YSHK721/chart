"""patterns.py の状態検証と計算量検定（CLAUDE.md の計算量テスト規約）。"""
import numpy as np
import pytest

from tools.measure.bar_patterns import patterns as P


def rng_from_seed(seed):
    """乱数は必ずシードから作る（再現性）。"""
    return np.random.default_rng(seed)


# ---- 状態検証 ----

def test_classify_direction_and_causal_size():
    o = np.array([10, 10, 10, 10, 10.0])
    c = np.array([11, 9, 10, 13, 9.5])
    sym_a, sym_b = P.classify(o, c, size_window=2)
    assert sym_a.tolist() == [1, 0, -1, 1, 0]
    # 足 3 の前 2 本の |実体| は [1, 0] → 中央値 0.5、|3| ≥ 0.5 で大陽（=3）
    # 足 4 の前 2 本は [0, 3] → 中央値 1.5、|0.5| < 1.5 で小陰（=0）。足 2 は同値で不明
    assert sym_b.tolist() == [-1, -1, -1, 3, 0]


def test_pattern_labels_oldest_bar_is_high_digit_and_unknown_breaks():
    sym = np.array([1, 1, 0, -1, 1, 0])
    lab = P.pattern_labels(sym, 2, 3)
    assert lab.tolist() == [-1, -1, 6, -1, -1, -1]
    assert P.pattern_name("A", 3, 6) == "UUD"
    assert P.pattern_name("B", 2, 3 * 4 + 0) == "大陽 小陰"


def test_tstats_matches_direct_formula():
    rng = rng_from_seed(0)
    vals = rng.normal(size=200)
    lab = rng.integers(0, 3, size=200)
    m = P.grouped_moments(lab, vals, np.ones(200, bool), 3)
    t = P.tstats(m, vals.mean())
    for g in range(3):
        x = vals[lab == g]
        want = (x.mean() - vals.mean()) / (x.std(ddof=1) / np.sqrt(len(x)))
        assert t[g] == pytest.approx(want)


def test_null_with_zero_shift_equals_observed():
    rng = rng_from_seed(1)
    fwd = rng.normal(size=300)
    fwd[-2:] = np.nan
    sets = [(rng.integers(-1, 4, size=300), 4), (rng.integers(-1, 2, size=300), 2)]
    t_obs, _, _ = P.observed(sets, fwd)
    t_null = P.null_tstats(sets, fwd, [0])
    np.testing.assert_allclose(t_null[0], t_obs)


def test_bh_and_holm_known_values():
    p = np.array([0.01, 0.04, 0.03, 0.5])
    np.testing.assert_allclose(P.bh_qvalues(p), [0.04, 0.0533333, 0.0533333, 0.5], rtol=1e-5)
    np.testing.assert_allclose(P.holm(p), [0.04, 0.09, 0.09, 0.5])


# ---- 計算量検定（注入点 moments を数える） ----

class MomentsSpy:
    def __init__(self):
        self.issued = 0

    def __call__(self, labels, values, valid, n_groups):
        self.issued += 1
        return P.grouped_moments(labels, values, valid, n_groups)


def _run(n, n_shifts, groups):
    rng = rng_from_seed(2)
    fwd = rng.normal(size=n)
    sets = [(rng.integers(-1, g, size=n), g) for g in groups]
    spy = MomentsSpy()
    out = P.null_tstats(sets, fwd, list(range(1, n_shifts + 1)), moments=spy)
    # 出力に使った計算 = 出力の (ずらし, 並び集合) の区画数（各区画は 1 回の計算から書かれる）
    used = out.shape[0] * len(groups)
    assert out.shape[1] == sum(groups)
    return spy.issued, used


def test_null_issues_no_moment_computation_that_is_not_used():
    issued, used = _run(500, 7, [2, 4, 8])
    assert issued - used == 0


@pytest.mark.parametrize("n_a,n_b", [(400, 1600)])
def test_null_issue_count_does_not_grow_with_series_length(n_a, n_b):
    assert _run(n_a, 5, [2, 4])[0] == _run(n_b, 5, [2, 4])[0]


def test_null_issue_count_does_not_grow_with_number_of_patterns_per_set():
    assert _run(500, 5, [2, 4])[0] == _run(500, 5, [32, 64])[0]


def test_observed_issues_one_computation_per_set():
    rng = rng_from_seed(3)
    for groups in ([2], [2, 4, 8, 16]):
        sets = [(rng.integers(-1, g, size=300), g) for g in groups]
        spy = MomentsSpy()
        t, _, _ = P.observed(sets, rng.normal(size=300), moments=spy)
        assert spy.issued - len(sets) == 0
        assert len(t) == sum(groups)
