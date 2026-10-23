"""ema_hl.py の状態検証と計算量検定（CLAUDE.md の計算量テスト規約）。"""
import numpy as np
import pytest

from tools.measure.ema_hl import ema_hl as E


def rng_from_seed(seed):
    """乱数は必ずシードから作る（再現性）。"""
    return np.random.default_rng(seed)


def _bars(n, seed=0):
    rng = rng_from_seed(seed)
    c = 30000 + np.cumsum(rng.normal(0, 5, size=n))
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) + rng.uniform(0, 4, size=n)
    l = np.minimum(o, c) - rng.uniform(0, 4, size=n)
    return {"open": o, "high": h, "low": l, "close": c, "minutes": np.arange(n, dtype=float)}


# ---- 状態検証 ----

def test_ema_equals_reference_definition():
    """参照実装 core.exponential_ma_on_buffer の漸化式（初期値 = 最初の値・係数 2/61）を手で書いて比べる。"""
    x = _bars(500)["low"]
    a = 2.0 / 61.0
    want = np.empty_like(x)
    want[0] = x[0]
    for i in range(1, len(x)):
        want[i] = x[i] * a + want[i - 1] * (1.0 - a)
    np.testing.assert_allclose(E.ema(x, 60), want, rtol=0, atol=1e-9)


def test_touch_of_previous_value_equals_touch_of_forming_ema():
    """安値が L[i-1] 以下 ⇔ 触れた価格を足の安値とした形成中の EMA に触れる（代数的同値）。"""
    b = _bars(3000, 1)
    a = 2 / 61
    base = E.ema(b["low"])
    prev = np.concatenate([[np.nan], base[:-1]])
    forming = a * b["low"] + (1 - a) * prev
    lhs = b["low"][1:] <= prev[1:]
    rhs = b["low"][1:] <= forming[1:]
    assert (lhs == rhs).all()


def test_touches_require_open_outside_the_line():
    o = np.array([10, 10, 10, 8.0]); h = np.array([11, 11, 11, 10.5]); l = np.array([9, 9, 8, 7.0])
    prev = np.array([np.nan, 8.5, 8.5, 9.0])
    assert E.touches(o, h, l, prev, "lower").tolist() == [2]      # 足 3 は始値 8 < 9 で外側ではない
    assert E.touches(o, h, l, prev, "upper").tolist() == [3]      # 始値 8 < 9 かつ高値 10.5 ≥ 9


def test_outcomes_sign_gap_and_hold():
    c = np.array([100, 101, 102, 103, 104, 105, 106.0])
    minutes = np.array([0, 1, 2, 3, 4, 5, 500.0])
    line = np.full(7, 100.5)
    out = E.outcomes(np.array([0, 1]), np.array([100.0, 100.0]), c, line, minutes, "lower", 1e9)
    assert out["pnl5"][0] == pytest.approx(np.log(105 / 100) * 1e4)
    assert np.isnan(out["pnl5"][1])          # 足 1 の 5 本後（足 6）は 499 分後＝空白をまたぐ
    assert out["hold"].tolist() == [0.0, 1.0]
    short = E.outcomes(np.array([0]), np.array([100.0]), c, line, minutes, "upper", 1e9)
    assert short["pnl5"][0] == pytest.approx(-np.log(105 / 100) * 1e4)


def test_bootstrap_with_identity_weights_equals_observed_d():
    rng = rng_from_seed(2)
    sc = [(rng.normal(size=20), rng.integers(1, 5, size=20).astype(float)) for _ in range(5)]
    d, _ = E.d_statistic(sc)
    assert E.bootstrap_d(sc, np.ones((1, 20)))[0] == pytest.approx(d)


# ---- 計算量検定（注入点 aggregate を数える） ----

class AggregateSpy:
    def __init__(self):
        self.issued = 0

    def __call__(self, day, values, n_days):
        self.issued += 1
        return E.day_sums(day, values, n_days)


def _measure(n_bars):
    b = _bars(n_bars, 3)
    day = (b["minutes"] // 100).astype(int)
    spy = AggregateSpy()
    res = E.measure_lines(b, "lower", np.ones(n_bars, bool), day, int(day.max()) + 1, 1e18, aggregate=spy)
    used = sum(len(v) for v in res.values())  # D の計算に渡る (指標, 線) の区画数
    return spy.issued, used, res


def test_no_aggregation_is_issued_that_is_not_used():
    issued, used, _ = _measure(2000)
    assert issued - used == 0


def test_aggregation_count_does_not_grow_with_series_length():
    assert _measure(2000)[0] == _measure(8000)[0]


def test_bootstrap_does_not_recount_events_as_resamples_grow():
    """ブートストラップは日別の和だけを使い、回数を増やしても集計を発行しない。"""
    issued, _, res = _measure(2000)
    n_days = len(res["hold"][0][0])
    rng = rng_from_seed(4)
    for b in (10, 400):
        w = rng.multinomial(n_days, np.full(n_days, 1 / n_days), size=b).astype(float)
        assert E.bootstrap_d(res["hold"], w).shape == (b,)
    assert _measure(2000)[0] == issued
