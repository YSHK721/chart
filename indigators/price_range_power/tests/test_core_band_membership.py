"""帯メンバシップ O(M+N) 化（ISSUE-468）の同値性・計算量検定。

旧実装（(M, N) 密行列）は全履歴×既定 interval=0.1 で数十 GB を確保し、dashboard core を
OOM でプロセスごと殺した（2026-08-30 実測: exit 137・cgroup peak 15.93GB）。
本検定は 2 面で欠陥の再発を機械的に禁じる:

  1. 同値性: 旧行列実装を**本テスト内に凍結**し、新実装（searchsorted + bincount）と
     counts / ratios / total の全要素一致を固定する（ISSUE-158 の
     test_plain_bars_vectorized と同じ流儀）。
  2. 計算量（メモリ）: 帯数 M を増やしてもピーク割当が O(M·N) に戻らないことを
     tracemalloc の実測で固定する。回数・バイト数そのものは焼き込まない
     （固定するのは「密行列の不在」であって実装詳細ではない）。
"""

from __future__ import annotations

import tracemalloc

import numpy as np
import pytest

from src.core import (
    WICK_NAMES,
    build_price_bands,
    compute_price_range_power,
    wick_samples,
    wick_stats,
    _sigma_bins,
)


def _reference_counts(open_, high, low, close, interval, range_from, range_to):
    """旧実装（(M, N) 密行列）の凍結写し。同値性の参照としてのみ使う。"""
    samples = wick_samples(open_, high, low, close)
    high_a = np.asarray(high, dtype=np.float64)
    low_a = np.asarray(low, dtype=np.float64)
    bands = build_price_bands(range_from, range_to, interval)
    upper = bands + interval
    m = bands.size
    lo = bands[:, None]
    hi = upper[:, None]
    low_in_f = ((lo <= low_a[None, :]) & (hi > low_a[None, :])).astype(np.float64)
    high_in_f = ((lo <= high_a[None, :]) & (hi > high_a[None, :])).astype(np.float64)
    stats = {name: wick_stats(name, samples[name]) for name in WICK_NAMES}

    def bin_masks(name):
        b = _sigma_bins(samples[name], stats[name])
        return (b == 1).astype(np.float64), (b == 2).astype(np.float64), (b == 3).astype(np.float64)

    hc1, hc2, hc3 = bin_masks("hc")
    ol1, ol2, ol3 = bin_masks("ol")
    hl1, hl2, hl3 = bin_masks("hl")
    lh1, lh2, lh3 = bin_masks("lh")
    counts = np.zeros((m, 14), dtype=np.float64)
    counts[:, 0] = low_in_f.sum(axis=1)
    for col, mask in ((1, ol1), (2, ol2), (3, ol3), (4, lh1), (5, lh2), (6, lh3)):
        counts[:, col] = low_in_f @ mask
    counts[:, 7] = high_in_f.sum(axis=1)
    for col, mask in ((8, hc1), (9, hc2), (10, hc3), (11, hl1), (12, hl2), (13, hl3)):
        counts[:, col] = high_in_f @ mask
    return counts


def _ohlc(n: int, spread: float):
    """決定的な擬似 OHLC（シード付き乱数・NaN 2 本と帯外値を含む）。"""
    rng = np.random.default_rng(seed=468)
    base = 100.0 + np.cumsum(rng.normal(0.0, spread, size=n))
    o = base + rng.normal(0.0, 0.05, size=n)
    c = base + rng.normal(0.0, 0.05, size=n)
    h = np.maximum(o, c) + np.abs(rng.normal(0.0, 0.08, size=n))
    l = np.minimum(o, c) - np.abs(rng.normal(0.0, 0.08, size=n))
    if n >= 10:
        h[3] = np.nan
        l[7] = np.nan
    return o, h, l, c


@pytest.mark.parametrize("interval", [0.1, 0.01])
def test_counts_match_the_frozen_dense_matrix_reference(interval: float) -> None:
    o, h, l, c = _ohlc(400, spread=0.4)
    range_from = float(np.nanmin(l)) - 2 * interval
    range_to = float(np.nanmax(h)) + 2 * interval

    result = compute_price_range_power(
        o, h, l, c, interval=interval, range_from=range_from, range_to=range_to
    )
    reference = _reference_counts(o, h, l, c, interval, range_from, range_to)

    np.testing.assert_array_equal(result.counts, reference)


def test_values_in_gaps_between_bands_stay_uncounted() -> None:
    """RoundUp 由来の帯間の隙間に落ちる値は旧実装同様どの帯にも数えない。"""
    o = np.array([10.0, 10.0])
    c = np.array([10.05, 10.05])
    h = np.array([10.06, 10.09995])   # 2 本目は下端+刻みと次帯下端の間を狙う
    l = np.array([9.99, 9.99])
    result = compute_price_range_power(
        o, h, l, c, interval=0.1, range_from=9.9, range_to=10.2
    )
    reference = _reference_counts(o, h, l, c, 0.1, 9.9, 10.2)
    np.testing.assert_array_equal(result.counts, reference)


def test_peak_allocation_does_not_scale_with_bands_times_bars() -> None:
    """M を 10 倍にしてもピーク割当は O(M+N) に留まる（密行列 O(M·N) の不在を固定）。

    バイト数は焼き込まない。小 M と大 M のピーク差が「増えた帯数ぶんの列ベクトル
    ×小さな定数」以下であることだけを表明する（旧実装は差が ×N 倍で必ず破る）。
    """
    n = 2_000
    o, h, l, c = _ohlc(n, spread=0.4)
    lo_v = float(np.nanmin(l))

    def peak_for(m_scale: float) -> tuple[int, int]:
        range_to = lo_v + m_scale
        m = build_price_bands(lo_v, range_to, 0.01).size
        tracemalloc.start()
        compute_price_range_power(
            o, h, l, c, interval=0.01, range_from=lo_v, range_to=range_to
        )
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return peak, m

    peak_small, m_small = peak_for(2.0)
    peak_large, m_large = peak_for(20.0)
    added_bands = m_large - m_small
    assert added_bands > 0

    # 帯 1 本あたりの追加費用は列 26 本 + 作業配列でも数百バイト。O(M·N) なら
    # 追加 1 帯あたり ≈ N バイト級（bool でも 2,000）で、この上限を必ず破る。
    per_band = (peak_large - peak_small) / added_bands
    assert per_band < 1_000, (
        f"帯 1 本あたり {per_band:.0f} bytes — 密行列 O(M·N) が復活している"
        f"（peak {peak_small} -> {peak_large}, M {m_small} -> {m_large}, N={n}）"
    )
