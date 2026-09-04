"""価格帯別ブルベアレシオ 計算層の検証。

元 VBA（``TA.PriceRangePower`` 172-417 行）のロジックを、(1) 手計算可能な微小入力での
明示アンカー、(2) VBA 擬似コードを 1:1 転記した独立リファレンス（ベクトル化していない
ループ実装）との突合、で固定する。
import 規約: sys.path.insert(parents[1]) → from src import ...（ガイド §7）。
"""

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import (  # noqa: E402
    COUNT_COLUMNS,
    RATIO_COLUMNS,
    build_price_bands,
    build_price_range_power,
    compute_price_range_power,
    round_up,
    wick_samples,
    wick_stats,
)
from src.core import _sigma_bins  # noqa: E402


# ───────────────────────────── 独立リファレンス（VBA 1:1 転記） ─────────────────────────────
def _ref_stats(samples):
    vals = [v for v in samples if v is not None and not math.isnan(v)]
    avg = math.nan if not vals else sum(vals) / len(vals)
    if len(vals) < 2:
        std = math.nan
    else:
        std = math.sqrt(sum((v - avg) ** 2 for v in vals) / (len(vals) - 1))
    return avg + std, avg + 2 * std, avg + 3 * std


def _ref_bin(x, a1, a2, a3):
    if x is None or math.isnan(x) or any(math.isnan(t) for t in (a1, a2, a3)):
        return 0
    if a1 <= x <= a2:      # Case A1 To A2
        return 1
    if a2 <= x <= a3:      # Case A2 To A3
        return 2
    if x > a3:             # Case Is > A3
        return 3
    return 0


def _ref_prp(o, h, low, c, interval, range_from=None, range_to=None):
    """VBA PriceRangePower の素朴転記（オラクル）。counts(M,14)/ratios(M,12)/total を返す。"""
    n = len(o)
    oc = [c[i] - o[i] for i in range(n)]
    hc = [None] * n
    ol = [None] * n
    hl = [None] * n
    lh = [None] * n
    for i in range(n):
        if oc[i] > 0:
            hc[i] = h[i] - c[i]
            lh[i] = h[i] - low[i]
        elif oc[i] < 0:
            ol[i] = o[i] - low[i]
            hl[i] = h[i] - low[i]
        else:
            hc[i] = h[i] - c[i]
            ol[i] = o[i] - low[i]

    hc_t = _ref_stats(hc)
    ol_t = _ref_stats(ol)
    hl_t = _ref_stats(hl)
    lh_t = _ref_stats(lh)

    if range_from is None:
        range_from = min(low)
    if range_to is None:
        range_to = max(h)
    bands = [float(range_from)]
    while bands[-1] <= range_to:
        bands.append(round_up(bands[-1] + interval))

    m = len(bands)
    counts = np.zeros((m, 14))
    for bi, b in enumerate(bands):
        for j in range(n):
            low_in = (b <= low[j]) and (b + interval > low[j])
            high_in = (b <= h[j]) and (b + interval > h[j])
            if low_in:
                counts[bi, 0] += 1
                counts[bi, 1 + _ref_bin(ol[j], *ol_t) - 1] += 1 if _ref_bin(ol[j], *ol_t) else 0
                counts[bi, 4 + _ref_bin(lh[j], *lh_t) - 1] += 1 if _ref_bin(lh[j], *lh_t) else 0
            if high_in:
                counts[bi, 7] += 1
                counts[bi, 8 + _ref_bin(hc[j], *hc_t) - 1] += 1 if _ref_bin(hc[j], *hc_t) else 0
                counts[bi, 11 + _ref_bin(hl[j], *hl_t) - 1] += 1 if _ref_bin(hl[j], *hl_t) else 0

    ratios = np.full((m, 12), np.nan)
    num_cols = [1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13]
    den_cols = [0, 0, 0, 0, 0, 0, 7, 7, 7, 7, 7, 7]
    for bi in range(m):
        for k, (nc, dc) in enumerate(zip(num_cols, den_cols)):
            num, den = counts[bi, nc], counts[bi, dc]
            if den > 0 and num > 0:
                ratios[bi, k] = num / den
    total = np.nansum(ratios, axis=1)
    return np.asarray(bands), counts, ratios, total


# ───────────────────────────── 微小入力（手計算アンカー） ─────────────────────────────
# bar0 陽線 / bar1 陰線 / bar2 陽線 / bar3 同値
_O = [10.0, 11.0, 10.0, 12.0]
_H = [12.0, 11.5, 13.0, 12.0]
_L = [9.0, 10.0, 9.5, 11.0]
_C = [11.0, 10.5, 12.0, 12.0]


def test_wick_samples_exact():
    s = wick_samples(_O, _H, _L, _C)
    # 陽線(bar0,bar2): hc=h-c, lh=h-l ; 同値(bar3): hc=h-c, ol=o-l ; 陰線(bar1): ol=o-l, hl=h-l
    assert np.allclose(s["hc"], [1.0, np.nan, 1.0, 0.0], equal_nan=True)
    assert np.allclose(s["lh"], [3.0, np.nan, 3.5, np.nan], equal_nan=True)
    assert np.allclose(s["ol"], [np.nan, 1.0, np.nan, 1.0], equal_nan=True)
    assert np.allclose(s["hl"], [np.nan, 1.5, np.nan, np.nan], equal_nan=True)


def test_wick_stats_sample_std_skips_nan():
    s = wick_samples(_O, _H, _L, _C)
    hc = wick_stats("hc", s["hc"])  # valid=[1,1,0]
    assert hc.avg == pytest.approx(2 / 3)
    assert hc.std == pytest.approx(math.sqrt(((1 - 2/3)**2 * 2 + (0 - 2/3)**2) / 2))
    # 1 サンプルのみ（hl）は標本標準偏差が未定義 → NaN。
    hl = wick_stats("hl", s["hl"])
    assert math.isnan(hl.std)
    assert math.isnan(hl.a1)
    # 同値サンプル（ol=[1,1]）は std=0 → a1=a2=a3=avg。
    ol = wick_stats("ol", s["ol"])
    assert ol.std == pytest.approx(0.0)
    assert ol.a1 == ol.a2 == ol.a3 == pytest.approx(1.0)


def test_sigma_bins_select_case_order():
    st = wick_stats("x", np.array([0.0, 4.0]))  # avg=2, std=sqrt(8)=2.828; a1=4.828,a2=7.657,a3=10.485
    x = np.array([st.a1, st.a2, st.a3, st.a3 + 1, st.a1 - 0.1, np.nan])
    bins = _sigma_bins(x, st)
    # a1→1, a2→1(先勝ち), a3→2, >a3→3, <a1→0, NaN→0
    assert list(bins) == [1, 1, 2, 3, 0, 0]


def test_build_price_bands_includes_one_past_range_to():
    bands = build_price_bands(9.0, 13.0, 1.0)
    # 直前<=range_to の間生成 → 最後の 1 本(14)は range_to を初めて超える。
    assert np.allclose(bands, [9, 10, 11, 12, 13, 14])


def test_build_price_bands_rejects_nonpositive_interval():
    with pytest.raises(ValueError):
        build_price_bands(0.0, 1.0, 0.0)


def test_fda_counts_exact():
    res = compute_price_range_power(_O, _H, _L, _C, interval=1.0)
    # bands=[9..14]。安値=[9,10,9.5,11] / 高値=[12,11.5,13,12]
    fda_l = res.counts[:, COUNT_COLUMNS.index("fda_f_l")]
    fda_h = res.counts[:, COUNT_COLUMNS.index("fda_f_h")]
    assert list(fda_l) == [2, 1, 1, 0, 0, 0]
    assert list(fda_h) == [0, 0, 1, 2, 1, 0]


# ───────────────────────────── オラクル突合 ─────────────────────────────
@pytest.mark.parametrize("interval", [1.0, 0.5, 0.1])
def test_matches_reference_small(interval):
    res = compute_price_range_power(_O, _H, _L, _C, interval=interval)
    b, counts, ratios, total = _ref_prp(_O, _H, _L, _C, interval)
    assert np.allclose(res.bands, b)
    assert np.allclose(res.counts, counts)
    assert np.allclose(res.ratios, ratios, equal_nan=True)
    assert np.allclose(res.total, total)


def test_matches_reference_random():
    rng = np.random.default_rng(20240606)
    n = 80
    base = 1.10 + np.cumsum(rng.normal(0, 0.01, n))
    o = base
    c = base + rng.normal(0, 0.01, n)
    span = rng.uniform(0.0, 0.02, n)
    h = np.maximum(o, c) + span
    low = np.minimum(o, c) - rng.uniform(0.0, 0.02, n)
    res = compute_price_range_power(o, h, low, c, interval=0.01)
    b, counts, ratios, total = _ref_prp(list(o), list(h), list(low), list(c), 0.01)
    assert np.allclose(res.bands, b)
    assert np.allclose(res.counts, counts)
    assert np.allclose(res.ratios, ratios, equal_nan=True)
    assert np.allclose(res.total, total)


def test_ratio_empty_when_numerator_zero():
    res = compute_price_range_power(_O, _H, _L, _C, interval=1.0)
    # 度数 0 の帯では比率は NaN（元 Empty）であり 0 ではない。
    for k in range(len(RATIO_COLUMNS)):
        col = res.ratios[:, k]
        zero_num_band0 = res.counts[0, [1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13][k]] == 0
        if zero_num_band0:
            assert math.isnan(col[0])


# ───────────────────────────── 成果物 DataFrame / 異常系 ─────────────────────────────
def test_build_frame_columns_and_index():
    df = pd.DataFrame({"Open": _O, "High": _H, "Low": _L, "Close": _C})  # 列名大小不問
    out = build_price_range_power(df, interval=1.0)
    assert list(out.columns) == list(COUNT_COLUMNS) + list(RATIO_COLUMNS) + ["total"]
    assert out.index.name == "prp"
    assert np.allclose(out.index.to_numpy(), [9, 10, 11, 12, 13, 14])


def test_build_frame_missing_column_raises():
    df = pd.DataFrame({"open": _O, "high": _H, "low": _L})  # close 欠落
    with pytest.raises(KeyError):
        build_price_range_power(df)


def test_wick_samples_length_mismatch_raises():
    with pytest.raises(ValueError):
        wick_samples([1, 2], [1], [1, 2], [1, 2])


def test_wick_samples_empty_raises():
    with pytest.raises(ValueError):
        wick_samples([], [], [], [])
