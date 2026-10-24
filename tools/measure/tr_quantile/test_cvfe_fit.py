"""cvfe_fit.py の状態検証と計算量検定（CLAUDE.md の計算量テスト規約）。"""
import numpy as np

from indigators.cvfe.src.ohlc import compute_cvfe_from_ohlc
from tools.measure.tr_quantile import cvfe_fit as F


def rng_from_seed(seed):
    """乱数は必ずシードから作る（再現性）。"""
    return np.random.default_rng(seed)


def _ohlc(n, seed=0):
    rng = rng_from_seed(seed)
    c = 30000 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) * (1 + rng.uniform(0, 0.002, n))
    l = np.minimum(o, c) * (1 - rng.uniform(0, 0.002, n))
    t = 1_600_000_000 + np.arange(n) * 3600.0
    return o, h, l, c, t


def test_sigma_series_without_new_coefficients_equals_cvfe():
    o, h, l, c, t = _ohlc(700)
    meas, params, q = F.prepare(o, h, l, c, t)
    want = compute_cvfe_from_ohlc(o, h, l, c, t).sigma_hat
    np.testing.assert_allclose(F.sigma_series(meas, params, q), want, equal_nan=True)


def test_block_indices_are_contiguous_blocks():
    idx = F.block_indices(50, 5, rng_from_seed(1))
    assert len(idx) == 50
    for k in range(0, 50, 5):
        assert np.all(np.diff(idx[k:k + 5]) == 1)


class FitSpy:
    def __init__(self):
        self.issued = 0

    def __call__(self, x, y):
        self.issued += 1
        b = np.linalg.lstsq(np.column_stack([np.ones(len(y)), x]), y, rcond=None)[0]
        return b, 1.0


def _boot(n_rows, n_boot):
    rng = rng_from_seed(2)
    x = rng.normal(size=(n_rows, 5))
    y = x @ np.ones(5) + rng.normal(size=n_rows)
    spy = FitSpy()
    _, _, level = F.bootstrap_fit(x, y, 10, n_boot, rng, fit=spy)
    return spy.issued, len(level)


def test_every_bootstrap_fit_is_used():
    issued, used = _boot(400, 30)
    assert issued - used == 0


def test_fit_count_does_not_grow_with_number_of_rows():
    assert _boot(200, 20)[0] == _boot(2000, 20)[0]
