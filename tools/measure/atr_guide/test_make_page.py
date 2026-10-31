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
