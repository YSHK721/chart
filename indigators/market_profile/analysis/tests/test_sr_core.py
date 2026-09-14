"""sr_core / step10 の判定部品テスト（ISSUE-248）。

最重要は **参照実装 step9 との等価性**（接触 index・跳ね返り判定）。定義を変えていない
ことをテストで固定し、以後の高速化・拡張がドリフトしないようにする。
"""

from __future__ import annotations

import numpy as np
import pytest

from mp_stats import sr_core as sc
from mp_stats import step10_sr_response as s10
from mp_stats import step9_naked_revisit as s9


def _path(closes, cell=1.0, row=4.0):
    return sc.make_path(np.asarray(closes, dtype=float), cell, row, day=0)


# --------------------------------------------------------------------------- #
# 接触判定: step9 _first_touch との等価性
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("seed", range(8))
def test_first_touch_matches_step9_on_random_paths(seed):
    rng = np.random.default_rng(seed)
    closes = 9000.0 + np.cumsum(rng.normal(0, 3.0, 400))
    cell = 0.9
    p = _path(closes, cell=cell)
    levels = np.linspace(closes.min() - 5, closes.max() + 5, 300)
    got = sc.first_touch_many(p, levels)
    ref = [s9._first_touch(closes, float(v), cell / 2.0) for v in levels]
    ref = np.array([-1 if r is None else r for r in ref])
    assert np.array_equal(got, ref)


def test_first_touch_level_on_start_is_index_zero():
    p = _path([100.0, 101.0, 102.0], cell=1.0)
    assert sc.first_touch_many(p, np.array([100.2]))[0] == 0


def test_first_touch_unreached_level_is_minus_one():
    p = _path([100.0, 100.5, 101.0], cell=0.1)
    assert sc.first_touch_many(p, np.array([200.0]))[0] == -1


def test_first_touch_detects_crossing_without_landing_in_cell():
    # 1 分でセルを跨ぐ（near は不成立・cross のみ）。
    closes = np.array([100.0, 110.0, 111.0])
    p = _path(closes, cell=0.2)
    assert sc.first_touch_many(p, np.array([105.0]))[0] == 1


# --------------------------------------------------------------------------- #
# 跳ね返り判定: step9 bounced との等価性
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("seed", range(6))
def test_bounce_matches_step9_on_random_paths(seed):
    rng = np.random.default_rng(100 + seed)
    closes = 9000.0 + np.cumsum(rng.normal(0, 3.0, 400))
    cell, row = 0.9, 12.0
    p = _path(closes, cell=cell, row=row)
    levels = np.linspace(closes.min(), closes.max(), 200)
    idx = sc.first_touch_many(p, levels)
    wh, wl = sc.window_extremes(closes, s9.REACTION_MINUTES)
    r = sc.measure(p, levels, idx, k=s9.REACTION_MINUTES, x=s9.BOUNCE_ROWS,
                   win_hi=wh, win_lo=wl)
    assert r is not None and r.idx.size > 20
    for j in range(r.idx.size):
        ref = s9.bounced(closes, int(r.idx[j]), float(r.level[j]), row, cell)
        assert ref is not None
        assert bool(ref) == bool(r.bounce[j])


def test_measure_drops_undecided_direction():
    # 接触直前が水準上（tol 内）＝ 接近方向不定 → 除外。
    closes = np.array([100.0, 100.0, 100.0, 105.0, 106.0])
    p = _path(closes, cell=1.0, row=1.0)
    idx = np.array([1])
    wh, wl = sc.window_extremes(closes, 3)
    assert sc.measure(p, np.array([100.0]), idx, k=3, x=1.0, win_hi=wh, win_lo=wl) is None


def test_measure_direction_and_signs():
    # 上から接近して跳ね返る（サポート成立の形）。
    closes = np.array([110.0, 100.0, 104.0, 106.0])
    p = _path(closes, cell=0.5, row=2.0)
    lv = np.array([100.0])
    idx = sc.first_touch_many(p, lv)
    wh, wl = sc.window_extremes(closes, 2)
    r = sc.measure(p, lv, idx, k=2, x=2.0, win_hi=wh, win_lo=wl)
    assert r is not None
    assert bool(r.from_above[0]) is True
    assert r.mre[0] == pytest.approx(3.0)      # (106-100)/2 行
    assert bool(r.bounce[0]) is True
    assert r.end[0] == pytest.approx(3.0)


def test_window_extremes_truncates_at_tail():
    c = np.array([1.0, 5.0, 2.0])
    hi, lo = sc.window_extremes(c, 3)
    assert list(hi) == [5.0, 5.0, 2.0]
    assert list(lo) == [1.0, 2.0, 2.0]


# --------------------------------------------------------------------------- #
# 水準の構成
# --------------------------------------------------------------------------- #
def _day(z, prices=None):
    z = np.asarray(z, dtype=float)
    prices = np.arange(z.size, dtype=float) + 100.0 if prices is None else prices
    return s10.Day(day=0, row_price=prices, z=z, closes=np.zeros(200) + 100.0,
                   cell_width=1.0, row_width=1.0)


def test_real_peaks_collapses_contiguous_run_to_argmax():
    d = _day([0, 3.5, 4.9, 3.2, 0, 5.0])
    assert list(s10.real_peaks(d, 3.0)) == [102.0, 105.0]


def test_real_cells_keeps_every_cell():
    d = _day([0, 3.5, 4.9, 3.2, 0, 5.0])
    assert len(s10.real_cells(d, 3.0)) == 4


def test_placebo_offsets_both_sides_and_rejects_high_z_landing():
    # ピークは 101（単独）と 107（塊 106-107 の argmax）。5 行ずらし先のうち
    # 101+5=106 は高 z セルなので棄却され、96 / 102 / 112 が残る。
    z = np.zeros(16)
    z[1] = 5.0
    z[6], z[7] = 3.5, 4.0
    d = _day(z)
    assert sorted(s10.real_peaks(d, 3.0)) == [101.0, 107.0]
    got = sorted(s10.placebo_levels(d, 3.0, rows=5.0))
    assert 106.0 not in got
    assert got == [96.0, 102.0, 112.0]


def test_z_band_selects_half_open_interval():
    d = _day([-1.0, 0.0, 0.5, 1.0, 2.0])
    assert list(s10.z_band_cells(d, 0.0, 1.0)) == [101.0, 102.0]


# --------------------------------------------------------------------------- #
# 推定量: 日 FE + クラスタ頑健の閉形式
# --------------------------------------------------------------------------- #
def test_paired_fe_matches_explicit_fixed_effects_ols():
    rng = np.random.default_rng(7)
    a, b = s10.Acc(), s10.Acc()
    ys, ds, gs = [], [], []
    for d in range(40):
        n1, n0 = int(rng.integers(1, 6)), int(rng.integers(1, 9))
        y1 = (rng.random(n1) < 0.4).astype(float)
        y0 = (rng.random(n0) < 0.3).astype(float)
        z = np.zeros((0, s10.PROFILE_MINUTES + 1))
        a.add(d, y1.astype(bool), y1.astype(bool), y1, y1, z)
        b.add(d, y0.astype(bool), y0.astype(bool), y0, y0, z)
        ys += list(y1) + list(y0)
        ds += [1.0] * n1 + [0.0] * n0
        gs += [d] * (n1 + n0)
    got = s10.paired_fe(a, b, metric="bounce", B=50)
    # 明示的な日固定効果 OLS（群内デミーン）
    y, dvec, g = np.array(ys), np.array(ds), np.array(gs)
    yd = y - np.array([y[g == k].mean() for k in g])
    dd = dvec - np.array([dvec[g == k].mean() for k in g])
    beta_ref = float((dd @ yd) / (dd @ dd))
    assert got["beta"] == pytest.approx(beta_ref, rel=1e-10)


def test_paired_fe_returns_nan_when_too_few_days():
    a, b = s10.Acc(), s10.Acc()
    z = np.zeros((0, s10.PROFILE_MINUTES + 1))
    for d in range(3):
        one = np.array([True])
        a.add(d, one, one, np.array([1.0]), np.array([1.0]), z)
        b.add(d, one, one, np.array([1.0]), np.array([1.0]), z)
    assert np.isnan(s10.paired_fe(a, b)["beta"])
