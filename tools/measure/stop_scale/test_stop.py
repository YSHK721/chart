"""stop.py の状態検証と計算量検定（CLAUDE.md の計算量テスト規約）。"""
import numpy as np
import pandas as pd

from indigators.btlm_trail.src.core import rolling_ols_window_end, window_end_scalar
from tools.measure.stop_scale import run as RUN
from tools.measure.stop_scale import stop as S


def rng_from_seed(seed):
    """乱数は必ずシードから作る（再現性）。"""
    return np.random.default_rng(seed)


def test_adverse_moves_read_only_the_next_bars():
    high = np.array([100.0, 110.0, 105.0, 120.0, 90.0])
    low = np.array([99.0, 95.0, 90.0, 80.0, 85.0])
    close = np.array([100.0, 100.0, 100.0, 100.0, 100.0])
    long_, short = S.adverse_moves(high, low, close, 2)
    assert long_[0] == (100 - 90) / 100 * 100  # 足 1・2 の安値の最小
    assert short[0] == (110 - 100) / 100 * 100  # 足 1・2 の高値の最大
    assert long_[1] == (100 - 80) / 100 * 100
    assert np.isnan(long_[3]) and np.isnan(short[4])


def test_residual_sigma_equals_btlm_trail():
    rng = rng_from_seed(0)
    x = 100 + np.cumsum(rng.normal(0, 1, 120))
    want = rolling_ols_window_end(x, 20)[3]
    got = S.residual_sigma(x, 20)
    assert np.isnan(got[:19]).all() and np.array_equal(got[19:], want[19:])
    assert np.array_equal(S.residual_sigma(x, 20, first=60)[60:], want[60:])
    assert np.isnan(S.residual_sigma(x, 20, first=60)[:60]).all()


def test_residual_sigma_of_a_straight_line_is_zero():
    assert abs(S.residual_sigma(np.arange(30.0) * 2, 10)[-1]) < 1e-9


def test_common_events_join_long_and_short_and_keep_common_bars():
    long_ = np.array([1.0, 2.0, 3.0, np.nan])
    short = np.array([4.0, 5.0, 6.0, 7.0])
    scales = {"a": np.array([2.0, 2.0, 2.0, 2.0]), "b": np.array([np.nan, 1.0, 1.0, 1.0])}
    block = np.array([0, 0, 1, 1])
    ev = S.common_events((long_, short), scales, block)
    assert list(ev["ratio"]["a"]) == [1.0, 1.5, 2.5, 3.0]
    assert list(ev["ratio"]["b"]) == [2.0, 3.0, 5.0, 6.0]
    assert list(ev["block"]) == [0, 1, 0, 1]
    assert list(ev["scale"]["a"]) == [2.0] * 4


def test_common_events_ignore_bars_before_first_and_non_positive_scales():
    long_ = short = np.ones(4)
    scales = {"a": np.array([1.0, 0.0, 1.0, 1.0])}
    ev = S.common_events((long_, short), scales, np.zeros(4, int), first=1)
    assert len(ev["ratio"]["a"]) == 4  # 足 2・3 の買いと売り


def test_multiple_hit_rate_and_distance():
    ratio = np.arange(1.0, 11.0)
    assert S.multiple_for(ratio, 0.5) == 5.5
    assert S.hit_rate(ratio, 9.0) == 20.0
    scale = np.full(10, 2.0)
    assert S.distance(ratio, scale, 0.5) == 11.0


def test_block_rates_and_gap():
    ratio = np.array([1.0, 3.0, 1.0, 1.0])
    block = np.array([0, 0, 1, 1])
    assert list(S.block_rates(ratio, block, 2.0)) == [50.0, 0.0]
    assert S.rate_gap(ratio, block, 2.0, 10.0) == (40.0 + 10.0) / 2


def test_two_sided_p():
    assert S.two_sided_p(np.array([1.0, 1.0, 1.0])) == 2 * 1 / 4
    assert S.two_sided_p(np.array([1.0, -1.0, 1.0, -1.0])) == 1.0


def _events(n=2000, seed=0, blocks=20):
    rng = rng_from_seed(seed)
    adv = rng.exponential(1.0, n)
    block = np.repeat(np.arange(blocks), n // blocks)
    return dict(block=block, ratio={"a": adv / 1.0, "b": adv / 2.0}, scale={"a": np.ones(n), "b": np.full(n, 2.0)})


def test_compare_distance_is_zero_for_the_same_distance_and_detects_a_shorter_one():
    ev = _events()
    draws = S.picks(20, 100, rng_from_seed(1))
    same = S.compare_distance(ev, "b", "a", draws)
    assert abs(same["point"]) < 1e-9 and abs(same["lo"]) < 1e-9 and abs(same["hi"]) < 1e-9
    ev2 = dict(ev, ratio={"a": ev["ratio"]["a"], "b": ev["ratio"]["a"]}, scale={"a": ev["scale"]["a"], "b": np.full(2000, 0.5)})
    r = S.compare_distance(ev2, "b", "a", draws)
    assert abs(r["point"] + 50.0) < 1e-9 and r["p"] < 0.05


def test_compare_distance_resamples_whole_blocks():
    ev = _events(n=200, blocks=2)
    seen = []

    def measure(ratio, scale, survival):
        seen.append(len(ratio))
        return 1.0

    S.compare_distance(ev, "a", "b", np.array([[0, 0], [1, 1]]), measure=measure)
    assert seen == [200, 200, 200, 200, 200, 200]


def test_compare_gap_reads_each_scale_with_its_own_multiple():
    ev = dict(block=np.array([0, 0, 1, 1]), ratio={"a": np.array([1.0, 3.0, 1.0, 1.0]), "b": np.array([1.0, 1.0, 1.0, 3.0])},
              scale={})
    draws = np.array([[0, 1], [1, 0]])
    r = S.compare_gap(ev, "a", "b", 2.0, 2.0, draws)
    # a は 50・0 → 外れ 40・10、b は 0・50 → 外れ 10・40。平均は同じ
    assert r["point"] == 0.0 and r["lo"] == 0.0
    r2 = S.compare_gap(ev, "a", "b", 9.0, 2.0, draws)  # a は掛からない → 外れ 10・10、b の外れは 10・40
    assert r2["point"] == 10.0 - 25.0


def _frame(is_p, oos_p, oos_point=-1.0):
    rows = []
    for period, ps, value in (("IS", is_p, -1.0), ("OOS", oos_p, oos_point)):
        for key, p in zip(("x", "y", "z"), ps):
            rows.append({"period": period, "key": key, "m_point": value, "m_p": p})
    return pd.DataFrame(rows)


def test_judge_applies_holm_only_to_the_pairs_that_passed_is():
    # IS の Holm 後: 0.003, 0.003, 0.9 → 2 組。OOS は 0.03・0.02: 0.02 × 2 = 0.04、0.03 → 単調で 0.04（不通過の 0.5 を混ぜると 0.06）
    df = S.judge(_frame([0.001, 0.001, 0.9], [0.03, 0.02, 0.5]), "m")
    assert df[df.m_pass].key.tolist() == ["x", "y"]
    assert np.isnan(df[(df.period == "OOS") & (df.key == "z")].m_holm.iloc[0])


def test_judge_rejects_the_opposite_direction():
    df = S.judge(_frame([0.001, 0.001, 0.9], [0.001, 0.001, 0.9], oos_point=1.0), "m")
    assert df[df.m_pass].key.tolist() == []


def _row(**kw):
    base = dict(distance_pass=False, distance_point=0.0, gap_pass=False, gap_point=0.0)
    return type("R", (), {**base, **kw})


def test_verdict_uses_the_registered_thresholds():
    assert S.verdict(_row(distance_pass=True, distance_point=-6.0)) == "σ が良い"
    assert S.verdict(_row(distance_pass=True, distance_point=-4.0)) == "差があるとは言えない"
    assert S.verdict(_row(gap_pass=True, gap_point=-1.5)) == "σ が良い"
    assert S.verdict(_row(distance_pass=True, distance_point=7.0)) == "ATR が良い"
    assert S.verdict(_row(gap_pass=True, gap_point=2.0)) == "ATR が良い"
    assert S.verdict(_row(distance_pass=False, distance_point=-9.0)) == "差があるとは言えない"


def test_frame_reads_warm_up_and_ends_at_the_period(monkeypatch):
    dates = pd.date_range("2013-01-01", periods=600, freq="B")
    rng = rng_from_seed(2)
    close = 1000 + np.cumsum(rng.normal(0, 5, 600))
    d = pd.DataFrame({"date": dates, "open": close, "high": close + 3, "low": close - 3, "close": close})
    monkeypatch.setattr(RUN, "load", lambda path, tf: d)
    f = RUN.frame("", "1D", "2014-01-01", "2015-01-01")
    keep = dates < "2015-01-01"
    assert len(f["close"]) == keep.sum()
    assert f["first"] == int((dates >= "2014-01-01").argmax())
    assert np.isfinite(f["scales"]["s100"][f["first"]])
    assert np.isnan(f["scales"]["s100"][f["first"] - 1])
    want = rolling_ols_window_end(close[keep], 20)[3][-1] / close[keep][-1] * 100
    assert abs(f["scales"]["s20"][-1] - want) < 1e-12


class WindowSpy:
    """1 窓の OLS の発行回数を数える Test Spy（注入点 window を通す）。"""

    def __init__(self):
        self.issued = 0

    def __call__(self, z):
        self.issued += 1
        return window_end_scalar(z)


def _sigma_issued(warm, bars=100):
    rng = rng_from_seed(3)
    spy = WindowSpy()
    out = S.residual_sigma(100 + np.cumsum(rng.normal(0, 1, warm + bars)), 20, warm, window=spy)
    return spy.issued, int(np.isfinite(out).sum())


def test_every_issued_window_reaches_the_output():
    issued, used = _sigma_issued(300)
    assert issued - used == 0


def test_issued_windows_do_not_grow_with_warm_up_length():
    assert _sigma_issued(300)[0] == _sigma_issued(3000)[0]


def _distance_issued(n, count=10):
    ev = _events(n=n)
    calls = []

    def measure(ratio, scale, survival):
        calls.append(1)
        return S.distance(ratio, scale, survival)

    S.compare_distance(ev, "b", "a", S.picks(20, count, rng_from_seed(4)), measure=measure)
    return len(calls), 2 * (count + 1)  # 実測と各抽出で、比の分子と分母に 1 回ずつ使う


def test_every_issued_measure_reaches_the_ratio():
    issued, used = _distance_issued(2000)
    assert issued - used == 0


def test_issued_measures_do_not_grow_with_input_length():
    assert _distance_issued(2000)[0] == _distance_issued(8000)[0]
