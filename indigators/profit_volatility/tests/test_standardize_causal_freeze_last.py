"""``_standardize_causal`` / ``compute_core_volatility`` の ``freeze_last`` 回帰検証。

freeze_last は「形成中（足内）の最新足をティック粒度で評価する際、標準化窓 W の基準
（平均/σ）を 1 足 1 回・足内で固定（凍結）する」ための加算的・既定 OFF オプション。
profit_system._causal_z の freeze_last と整合させる（先頭 NaN 区間 start を考慮し、
直前 W 本が start に掛かる場合は NaN）。

本テストが禁止する誤り（回帰ガード）:
    1. freeze_last=False が従来出力から 1 ビットでも変わること（既定の挙動不変）。
    2. freeze_last=True が out[-1] 以外（out[0..n-2]）を変えること。
    3. freeze_last=True の out[-1] が「直前 W 本（確定足）基準」になっていないこと。
    4. 直前 W 本が満たせない端条件（n-1-W < start）で out[-1] が NaN にならないこと。
    5. compute_core_volatility 公開経路で freeze_last が素通ししないこと。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # = profit_volatility/

from src.core import compute_core_volatility  # noqa: E402
from src.core import _standardize_causal  # noqa: E402


# ---------------------------------------------------------------------------
# 独立参照実装（関数 under test とは別経路。先頭 NaN を持つ系列を素直に処理）。
# ---------------------------------------------------------------------------
def _ref_standardize_causal(v: np.ndarray, window: int) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64)
    n = v.size
    out = np.full(n, np.nan, dtype=np.float64)
    if window < 2 or n == 0:
        return out
    finite = ~np.isnan(v)
    if not finite.any():
        return out
    start = int(np.argmax(finite))
    first = start + window - 1
    for a in range(first, n):
        win = v[a - window + 1 : a + 1]
        mean = float(np.mean(win))
        std = float(np.sqrt(np.mean((win - mean) ** 2)))
        out[a] = (v[a] - mean) / std if std > 0.0 else 0.0
    return out


def _ref_frozen_last(v: np.ndarray, window: int) -> float:
    v = np.asarray(v, dtype=np.float64)
    n = v.size
    finite = ~np.isnan(v)
    start = int(np.argmax(finite)) if finite.any() else n
    lo = n - 1 - window
    if lo < start:
        return float("nan")
    prior = v[lo : n - 1]  # v[n-1-W .. n-2]（最終点を除く直前 W 本）
    mean = float(np.mean(prior))
    std = float(np.sqrt(np.mean((prior - mean) ** 2)))
    return (v[-1] - mean) / std if std > 0.0 else 0.0


def _series_with_warmup(values: list[float], warmup: int) -> np.ndarray:
    """先頭 ``warmup`` 本を NaN にした系列（divergence の warm-up を模す）。"""
    arr = np.array([np.nan] * warmup + values, dtype=np.float64)
    return arr


# =========================================================== 1. 既定の挙動不変
def test_freeze_last_false_matches_legacy_exactly():
    """freeze_last=False は従来出力（=既定引数省略）と完全一致（1 ビットも変えない）。"""
    v = _series_with_warmup([1.0, 2.0, 4.0, 7.0, 3.0, 9.0, 5.0, 6.0], warmup=6)
    w = 4
    legacy = _standardize_causal(v, w)
    explicit_false = _standardize_causal(v, w, freeze_last=False)
    np.testing.assert_array_equal(legacy, explicit_false)


def test_freeze_last_false_matches_independent_reference():
    v = _series_with_warmup([10.0, 11.0, 9.0, 12.0, 8.0, 13.0, 7.0], warmup=6)
    w = 3
    got = _standardize_causal(v, w, freeze_last=False)
    ref = _ref_standardize_causal(v, w)
    np.testing.assert_allclose(
        got[np.isfinite(got)], ref[np.isfinite(ref)], rtol=0, atol=1e-9
    )
    assert np.array_equal(np.isnan(got), np.isnan(ref))


# =========================================== 2. freeze_last=True は out[-1] のみ変える
def test_freeze_last_true_changes_only_last_element():
    """末尾外れ値の系列で out[-1] のみ変わり out[0..-2] は不変。"""
    v = _series_with_warmup([1.0, 2.0, 3.0, 4.0, 5.0, 100.0], warmup=6)
    w = 3
    base = _standardize_causal(v, w, freeze_last=False)
    frozen = _standardize_causal(v, w, freeze_last=True)
    np.testing.assert_array_equal(base[:-1], frozen[:-1])
    assert base[-1] != frozen[-1]


def test_freeze_last_true_last_uses_prior_window_base():
    """out[-1] が「直前 W 本（確定足）基準」の z と一致する（独立参照）。"""
    v = _series_with_warmup([1.0, 2.0, 3.0, 4.0, 5.0, 100.0], warmup=6)
    w = 3
    frozen = _standardize_causal(v, w, freeze_last=True)
    expected_last = _ref_frozen_last(v, w)  # 直前 W=[3,4,5] 基準
    assert np.isclose(frozen[-1], expected_last, rtol=0, atol=1e-9)


def test_freeze_last_true_degenerate_std_zero_is_zero():
    """直前 W 本が定数（std==0）の縮退時、非凍結と同じく out[-1]=0.0。"""
    v = _series_with_warmup([7.0, 5.0, 5.0, 5.0, 42.0], warmup=6)  # 直前 W=[5,5,5]
    w = 3
    frozen = _standardize_causal(v, w, freeze_last=True)
    assert frozen[-1] == 0.0


# ============================= 3. 端条件 直前 W 本不足（n-1-W < start）で out[-1]=NaN
def test_freeze_last_true_edge_prior_window_hits_warmup_is_nan():
    """直前 W 本が先頭 NaN 区間に掛かる（n-1-W < start）と out[-1]=NaN。非凍結は有限。"""
    # warmup=6, 有限 3 本 → n=9, start=6。w=3 → lo = 9-1-3 = 5 < start(6) → NaN。
    v = _series_with_warmup([1.0, 2.0, 5.0], warmup=6)
    w = 3
    base = _standardize_causal(v, w, freeze_last=False)
    frozen = _standardize_causal(v, w, freeze_last=True)
    assert np.isfinite(base[-1])  # 非凍結は最終点 [1,2,5] 窓で算出
    assert np.isnan(frozen[-1])   # 凍結は直前 W 本が warm-up に掛かり NaN


def test_freeze_last_true_edge_prior_window_just_fits_is_finite():
    """直前 W 本がちょうど満たせる（n-1-W == start）で out[-1] は有限。"""
    # warmup=6, 有限 4 本 → n=10, start=6。w=3 → lo = 10-1-3 = 6 == start → 有限。
    v = _series_with_warmup([1.0, 2.0, 3.0, 9.0], warmup=6)
    w = 3
    frozen = _standardize_causal(v, w, freeze_last=True)
    assert np.isfinite(frozen[-1])
    assert np.isclose(frozen[-1], _ref_frozen_last(v, w), rtol=0, atol=1e-9)


def test_freeze_last_true_matches_causal_z_when_no_warmup():
    """warm-up なし（start=0）では端条件が profit_system._causal_z と同じ n<W+1 に一致。"""
    v = np.array([1.0, 2.0, 5.0], dtype=np.float64)  # start=0, n=3, w=3 → n<w+1
    w = 3
    frozen = _standardize_causal(v, w, freeze_last=True)
    assert np.isnan(frozen[-1])  # _causal_z の n<window+1 NaN と一致


# ===================================== 4. compute_core_volatility 公開経路の素通し
def _synth_ohlc(n: int, seed: int = 23):
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.5, n))
    open_ = close + rng.normal(0.0, 0.2, n)
    high = np.maximum(open_, close) + rng.uniform(0.05, 0.5, n)
    low = np.minimum(open_, close) - rng.uniform(0.05, 0.5, n)
    return open_, high, low, close


def test_core_volatility_window_none_ignores_freeze_last():
    """window=None（全期間バッチ）経路は freeze_last 無関係（raw 完全一致）。"""
    o, h, l, c = _synth_ohlc(200)
    off = compute_core_volatility(o, h, l, c, window=None, freeze_last=False)
    on = compute_core_volatility(o, h, l, c, window=None, freeze_last=True)
    np.testing.assert_array_equal(off.raw_level_count, on.raw_level_count)


def test_core_volatility_default_freeze_last_false_unchanged():
    """compute_core_volatility(window=W) は freeze_last 既定 False で raw 完全一致。"""
    o, h, l, c = _synth_ohlc(200)
    w = 120
    legacy = compute_core_volatility(o, h, l, c, window=w)
    explicit_false = compute_core_volatility(o, h, l, c, window=w, freeze_last=False)
    np.testing.assert_array_equal(
        legacy.raw_level_count, explicit_false.raw_level_count
    )


def test_core_volatility_freeze_last_changes_only_last():
    """compute_core_volatility(window=W, freeze_last=True) は raw の最終点のみ変えうる。"""
    o, h, l, c = _synth_ohlc(200)
    # 最終足を外れ値にして凍結 vs 非凍結の差を顕在化させる。
    c = c.copy(); c[-1] += 20.0
    o = o.copy(); o[-1] += 20.0
    h = h.copy(); h[-1] = max(o[-1], c[-1]) + 0.5
    l = l.copy(); l[-1] = min(o[-1], c[-1]) - 0.5
    w = 120
    off = compute_core_volatility(o, h, l, c, window=w, freeze_last=False)
    on = compute_core_volatility(o, h, l, c, window=w, freeze_last=True)
    np.testing.assert_array_equal(off.raw_level_count[:-1], on.raw_level_count[:-1])
    assert off.raw_level_count[-1] != on.raw_level_count[-1]
