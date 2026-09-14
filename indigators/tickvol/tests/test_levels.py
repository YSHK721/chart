"""tickvol の外れ値水準（経験的分位 / GPD-POT）の検証。

固定する仕様（indigators/tickvol/src/levels.py の docstring と 1:1）:
  - 因果性: バー t の水準は t より前に確定した観測のみから決まる（非リペイント）。
  - POT: 閾値は当該バー除外の因果ローリング分位。超過の連続はエピソード 1 件へ畳む。
  - 経験的分位と GPD は **同じ観測集合の同じ分位** を 2 通りで推定する（並べて読める）。
  - GPD は観測 30 件未満では出さない（NaN＝描画しない）。
  - q_out 無効（共有規約 q_out_valid）は ext / gpd のみ黙ってオフ。
  - 上側のみ（下側は裾でないため持たない）。
  - 1 バー入口（levels_latest / step_excess_event）はローリング版と同値＝増分計算の基礎。
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from common.event_quantiles import DEFAULT_K_EVENTS  # noqa: E402
from src.levels import (  # noqa: E402
    LEVEL_KEYS,
    MIN_GPD_EVENTS,
    causal_bands,
    causal_threshold,
    gpd_excess_quantile,
    levels_at,
    levels_latest,
    step_excess_event,
    tickvol_levels,
)

_KW = {"window_n": 50, "q_low": 0.20, "q_high": 0.80, "q_out": 0.95, "k_events": 40}


def _spiky(n=1200, seed=7):
    """定常な下地に周期的なスパイクを混ぜた tick 数風の系列（正値・整数的）。"""
    rng = np.random.default_rng(seed)
    base = rng.gamma(shape=2.0, scale=100.0, size=n) + 10.0
    base[::17] *= 3.0          # 単発スパイク
    for s in range(0, n, 101):  # 連続超過（エピソード）を作る
        base[s:s + 4] *= 4.0
    return np.round(base)


# ---- 閾値（POT） ------------------------------------------------------------


def test_threshold_is_causal_and_excludes_the_current_bar():
    v = _spiky(300)
    thr = causal_threshold(v, 50, 0.80)
    # バー t の閾値は v[t-50:t] の分位＝当該バーを含まない。
    for t in (60, 120, 299):
        assert thr[t] == pytest.approx(float(np.quantile(v[t - 50:t], 0.80)))


def test_threshold_warmup_is_nan():
    thr = causal_threshold(_spiky(50), 50, 0.80)
    assert np.isnan(thr[0])          # 直前 0 本＝算出不能


# ---- 正常帯（下側 / 上側の分位） -------------------------------------------


def test_bands_are_the_causal_rolling_quantile_pair():
    v = _spiky(300)
    low, high = causal_bands(v, window_n=50, q_low=0.20, q_high=0.80)
    for t in (60, 120, 299):
        assert low[t] == pytest.approx(float(np.quantile(v[t - 50:t], 0.20)))
        assert high[t] == pytest.approx(float(np.quantile(v[t - 50:t], 0.80)))


def test_band_high_is_the_pot_threshold():
    # 上側帯は POT の閾値そのもの（2 つの定義を持たない）。
    v = _spiky(300)
    _low, high = causal_bands(v, window_n=50, q_low=0.20, q_high=0.80)
    thr = causal_threshold(v, 50, 0.80)
    ok = np.isfinite(thr)
    assert np.array_equal(high[ok], thr[ok])


def test_band_low_is_below_band_high():
    out = tickvol_levels(_spiky(), **_KW)
    ok = np.isfinite(out["band_low"]) & np.isfinite(out["band_high"])
    assert ok.sum() > 100
    assert np.all(out["band_low"][ok] <= out["band_high"][ok])


@pytest.mark.parametrize("pair", [(0.80, 0.20), (0.50, 0.50), (0.0, 0.80), (0.20, 1.0)])
def test_invalid_quantile_pair_raises(pair):
    q_low, q_high = pair
    with pytest.raises(ValueError):
        tickvol_levels(_spiky(200), **{**_KW, "q_low": q_low, "q_high": q_high})


# ---- GPD 分位 ---------------------------------------------------------------


def test_gpd_excess_quantile_needs_min_events():
    ex = np.full(MIN_GPD_EVENTS - 1, 5.0) + np.arange(MIN_GPD_EVENTS - 1)
    assert np.isnan(gpd_excess_quantile(ex, 0.95))
    ex2 = np.full(MIN_GPD_EVENTS, 5.0) + np.arange(MIN_GPD_EVENTS)
    assert np.isfinite(gpd_excess_quantile(ex2, 0.95))


def test_gpd_excess_quantile_is_nan_for_invalid_q():
    ex = np.arange(1, MIN_GPD_EVENTS + 1, dtype=float)
    assert np.isnan(gpd_excess_quantile(ex, None))
    assert np.isnan(gpd_excess_quantile(ex, 1.0))


def test_gpd_recovers_the_exponential_quantile():
    # 指数分布（GPD の xi=0）から生成すれば、q 分位は解析解 -beta*ln(1-q) に一致するはず。
    rng = np.random.default_rng(3)
    beta = 40.0
    ex = rng.exponential(beta, size=4000)
    got = gpd_excess_quantile(ex, 0.99)
    want = -beta * np.log(1.0 - 0.99)
    assert got == pytest.approx(want, rel=0.12)


def test_gpd_is_monotone_in_q():
    rng = np.random.default_rng(11)
    ex = rng.exponential(30.0, size=500)
    lo, hi = gpd_excess_quantile(ex, 0.90), gpd_excess_quantile(ex, 0.99)
    assert lo < hi


# ---- 水準（経験的 / GPD の並列） -------------------------------------------


def test_levels_keys_and_upper_side_only():
    out = tickvol_levels(_spiky(), **_KW)
    assert set(out) == set(LEVEL_KEYS) | {"band_low", "band_high"}
    # イベント水準の下側（_evq_*_lo 相当）は持たない。band_low は表示専用の帯であって裾ではない。
    assert not any(key.endswith("_lo") for key in LEVEL_KEYS)


def test_empirical_and_gpd_estimate_the_same_quantile():
    # 同じ観測集合・同じ分位を 2 通りで推定するので、水準は同 order で並ぶ。
    out = tickvol_levels(_spiky(), **_KW)
    ok = np.isfinite(out["ext"]) & np.isfinite(out["gpd"])
    assert ok.sum() > 50
    ratio = out["gpd"][ok] / out["ext"][ok]
    assert 0.5 < float(np.median(ratio)) < 2.0


def test_levels_are_ordered_threshold_below_median_below_extreme():
    out = tickvol_levels(_spiky(), **_KW)
    ok = np.isfinite(out["band_high"]) & np.isfinite(out["med"]) & np.isfinite(out["ext"])
    assert ok.sum() > 50
    assert np.all(out["band_high"][ok] <= out["med"][ok])
    assert np.all(out["med"][ok] <= out["ext"][ok])


def test_gpd_line_starts_later_than_the_empirical_line():
    # GPD は 30 件、経験的は 5 件（共有既定 _MIN_EVENTS）から出る。
    out = tickvol_levels(_spiky(), **_KW)
    assert np.isfinite(out["gpd"]).sum() < np.isfinite(out["ext"]).sum()


def test_levels_are_causal_and_non_repainting():
    # 系列を後ろへ延ばしても、既存バーの水準は 1 ビットも変わらない（非リペイント）。
    v = _spiky(1200)
    short = tickvol_levels(v[:900], **_KW)
    long = tickvol_levels(v, **_KW)
    for key in ("band_low", "band_high", *LEVEL_KEYS):
        a, b = short[key], long[key][:900]
        both = np.isfinite(a) & np.isfinite(b)
        assert np.array_equal(np.isfinite(a), np.isfinite(b)), key
        assert np.array_equal(a[both], b[both]), key


def test_invalid_q_out_disables_only_extreme_and_gpd():
    for bad in (None, 0.5, 0.80, 1.5):
        out = tickvol_levels(_spiky(), **{**_KW, "q_out": bad})
        assert np.isfinite(out["med"]).any(), bad     # 中央値は残る
        assert not np.isfinite(out["ext"]).any(), bad
        assert not np.isfinite(out["gpd"]).any(), bad


def test_k_events_below_one_raises():
    with pytest.raises(ValueError):
        tickvol_levels(_spiky(200), **{**_KW, "k_events": 0})


def test_empty_input_returns_empty_arrays():
    out = tickvol_levels(np.empty(0), **_KW)
    for key in ("band_low", "band_high", *LEVEL_KEYS):
        assert out[key].size == 0


# ---- 1 バー入口（増分計算の基礎） -------------------------------------------


def test_step_excess_event_folds_a_run_into_one_episode_peak():
    up, run = [], []
    for x in (-1.0, 5.0, 9.0, 3.0, -2.0, -1.0):
        step_excess_event(x, up, run)
    assert up == [9.0]          # 連続超過 3 本 → 極値 1 観測
    for x in (4.0, -1.0):
        step_excess_event(x, up, run)
    assert up == [9.0, 4.0]


def test_step_excess_event_does_not_confirm_a_running_episode():
    up, run = [], []
    for x in (-1.0, 5.0, 9.0):
        step_excess_event(x, up, run)
    assert up == []             # 末尾で進行中＝未確定（非リペイント）
    assert run == [5.0, 9.0]


def test_levels_latest_matches_the_rolling_implementation():
    # ローリング版が各バーで 1 バー入口を呼ぶ構成であること（増分計算の前提）。
    v = _spiky(1200)
    rolling = tickvol_levels(v, **_KW)
    thr = causal_threshold(v, _KW["window_n"], _KW["q_high"])
    up, run = [], []
    for t in range(v.size - 1):
        step_excess_event(v[t] - thr[t], up, run)
    latest = levels_latest(up, q_out=_KW["q_out"], k_events=_KW["k_events"])
    t_last = v.size - 1
    for key in LEVEL_KEYS:
        want = rolling[key][t_last]
        got = thr[t_last] + latest[key]
        if np.isnan(want):
            assert np.isnan(got), key
        else:
            assert got == pytest.approx(want), key


def test_levels_at_uses_only_the_last_k_events():
    ex = list(np.arange(1.0, 201.0))
    a = levels_at(ex, len(ex), 40, 0.95)
    b = levels_at(ex[-40:], 40, 40, 0.95)
    for key in LEVEL_KEYS:
        assert a[key] == pytest.approx(b[key]), key


def test_default_k_events_matches_the_shared_default():
    # 既定値の単一情報源は common.event_quantiles（指標間で揃える）。
    out = tickvol_levels(_spiky(), window_n=50, q_low=0.20, q_high=0.80, q_out=0.95,
                         k_events=DEFAULT_K_EVENTS)
    assert np.isfinite(out["ext"]).any()
