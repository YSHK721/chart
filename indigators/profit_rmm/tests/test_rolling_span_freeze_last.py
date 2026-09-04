"""``rolling_span`` / ``compute_rmm`` の ``freeze_last`` 回帰検証。

freeze_last は「形成中（足内）の最新足をティック粒度で採点する際、スパン（採点の
分母）の標準化基準（avg±3σ・窓 W）を 1 足 1 回・足内で固定（凍結）する」ための
加算的・既定 OFF オプション。profit_system._causal_z の freeze_last と整合させる。

本テストが禁止する誤り（回帰ガード）:
    1. freeze_last=False が従来出力から 1 ビットでも変わること（既定の挙動不変）。
    2. freeze_last=True が out[-1] 以外（out[0..n-2]）を変えること。
    3. freeze_last=True の out[-1] が「直前 W 本（確定足）基準」になっていないこと。
    4. n<=W の端条件で out[-1] が NaN にならないこと。
    5. compute_rmm 公開経路で freeze_last が rolling_span へ素通ししないこと。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # = profit_rmm/

from src.core import compute_rmm, rolling_span  # noqa: E402


# ---------------------------------------------------------------------------
# 独立参照実装（関数 under test とは別経路。np.mean / 母 std を直接使用）。
# ---------------------------------------------------------------------------
def _ref_rolling_span(a: np.ndarray, window: int, clamp: bool) -> np.ndarray:
    """``rolling_span(freeze_last=False)`` の独立参照（窓スライスを素直に計算）。"""
    a = np.asarray(a, dtype=np.float64)
    n = a.size
    out = np.full(n, np.nan, dtype=np.float64)
    if window < 2 or n < window:
        return out
    for i in range(window - 1, n):
        win = a[i - window + 1 : i + 1]
        avg = float(np.mean(win))
        dev = float(np.sqrt(np.mean((win - avg) ** 2)))
        x3p, x3m = avg + 3.0 * dev, avg - 3.0 * dev
        if clamp:
            x3p, x3m = min(100.0, x3p), max(0.0, x3m)
        out[i] = x3p - x3m
    return out


def _ref_frozen_last_span(a: np.ndarray, window: int, clamp: bool) -> float:
    """freeze_last=True 時の out[-1] 独立参照（直前 W 本＝確定足基準）。"""
    a = np.asarray(a, dtype=np.float64)
    n = a.size
    if n < window + 1:
        return float("nan")
    prior = a[n - 1 - window : n - 1]  # a[n-1-W .. n-2]（最終点を除く直前 W 本）
    avg = float(np.mean(prior))
    dev = float(np.sqrt(np.mean((prior - avg) ** 2)))
    x3p, x3m = avg + 3.0 * dev, avg - 3.0 * dev
    if clamp:
        x3p, x3m = min(100.0, x3p), max(0.0, x3m)
    return x3p - x3m


# =========================================================== 1. 既定の挙動不変
def test_freeze_last_false_matches_legacy_exactly():
    """freeze_last=False は従来出力（=既定引数省略）と完全一致（1 ビットも変えない）。"""
    a = np.array([10.0, 20.0, 40.0, 70.0, 30.0, 90.0, 50.0, 60.0], dtype=np.float64)
    w = 4
    for clamp in (True, False):
        legacy = rolling_span(a, w, clamp=clamp)               # 既定（引数省略）
        explicit_false = rolling_span(a, w, clamp=clamp, freeze_last=False)
        np.testing.assert_array_equal(legacy, explicit_false)


def test_freeze_last_false_matches_independent_reference():
    """freeze_last=False が独立参照（窓スライス mean/母std）と一致する。"""
    a = np.array([10.0, 11.0, 9.0, 12.0, 8.0, 13.0, 7.0], dtype=np.float64)
    w = 3
    for clamp in (True, False):
        got = rolling_span(a, w, clamp=clamp, freeze_last=False)
        ref = _ref_rolling_span(a, w, clamp)
        np.testing.assert_allclose(
            got[np.isfinite(got)], ref[np.isfinite(ref)], rtol=0, atol=1e-9
        )
        assert np.array_equal(np.isnan(got), np.isnan(ref))


# =========================================== 2. freeze_last=True は out[-1] のみ変える
def test_freeze_last_true_changes_only_last_element():
    """末尾外れ値の系列で out[-1] のみ変わり out[0..-2] は不変。"""
    a = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 100.0], dtype=np.float64)  # 末尾外れ値
    w = 3
    base = rolling_span(a, w, clamp=False, freeze_last=False)
    frozen = rolling_span(a, w, clamp=False, freeze_last=True)
    np.testing.assert_array_equal(base[:-1], frozen[:-1])
    assert base[-1] != frozen[-1]


def test_freeze_last_true_last_uses_prior_window_base():
    """out[-1] が「直前 W 本（確定足）基準」のスパンと一致する（独立参照）。"""
    a = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 100.0], dtype=np.float64)
    w = 3
    for clamp in (True, False):
        frozen = rolling_span(a, w, clamp=clamp, freeze_last=True)
        expected_last = _ref_frozen_last_span(a, w, clamp)  # 直前 W=[3,4,5] 基準
        assert np.isclose(frozen[-1], expected_last, rtol=0, atol=1e-9)


def test_freeze_last_true_degenerate_dev_zero_span_is_zero():
    """直前 W 本が定数（dev==0）の縮退時、span=0.0（clamp なし）。"""
    a = np.array([7.0, 5.0, 5.0, 5.0, 42.0], dtype=np.float64)  # 直前 W=[5,5,5]
    w = 3
    frozen = rolling_span(a, w, clamp=False, freeze_last=True)
    assert frozen[-1] == 0.0


# =================================================== 3. 端条件 n<=W で out[-1]=NaN
def test_freeze_last_true_edge_n_equals_window_is_nan():
    """n==W（直前 W 本を満たせない）で out[-1]=NaN。非凍結では有限値。"""
    a = np.array([1.0, 2.0, 5.0], dtype=np.float64)
    w = 3  # n == w → n < w+1
    base = rolling_span(a, w, clamp=False, freeze_last=False)
    frozen = rolling_span(a, w, clamp=False, freeze_last=True)
    assert np.isfinite(base[-1])
    assert np.isnan(frozen[-1])


def test_freeze_last_true_edge_n_just_above_window_is_finite():
    """n==W+1（直前 W 本がちょうど満たせる）で out[-1] は有限。"""
    a = np.array([1.0, 2.0, 3.0, 9.0], dtype=np.float64)
    w = 3  # n == w+1
    frozen = rolling_span(a, w, clamp=False, freeze_last=True)
    assert np.isfinite(frozen[-1])
    assert np.isclose(
        frozen[-1], _ref_frozen_last_span(a, w, clamp=False), rtol=0, atol=1e-9
    )


# ===================================== 4. compute_rmm 公開経路の素通し（挙動不変＋伝播）
def _synth_ohlcv(n: int, seed: int = 7):
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0.0, 1.0, n))
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    vol = rng.uniform(100.0, 1000.0, n)
    return high, low, close, vol


def test_compute_rmm_window_none_ignores_freeze_last():
    """window=None（全期間バッチ）経路は freeze_last 無関係（level_count 完全一致）。"""
    h, l, c, v = _synth_ohlcv(200)
    off = compute_rmm(h, l, c, v, window=None, freeze_last=False)
    on = compute_rmm(h, l, c, v, window=None, freeze_last=True)
    np.testing.assert_array_equal(off.level_count, on.level_count)


def test_compute_rmm_default_freeze_last_false_unchanged():
    """compute_rmm(window=W) は freeze_last 既定 False で従来と完全一致。"""
    h, l, c, v = _synth_ohlcv(200)
    w = 120
    legacy = compute_rmm(h, l, c, v, window=w)
    explicit_false = compute_rmm(h, l, c, v, window=w, freeze_last=False)
    np.testing.assert_array_equal(legacy.level_count, explicit_false.level_count)


def test_compute_rmm_freeze_last_changes_only_last_level_count():
    """compute_rmm(window=W, freeze_last=True) は level_count の最終点のみ変えうる。

    最終足を外れ値にした系列で、level_count[0..-2] は False と一致し、
    level_count[-1] は凍結により変化する（採点の分母 span が直前 W 本基準になる）。
    """
    h, l, c, v = _synth_ohlcv(200)
    # 最終足を強い外れ値にして span（凍結 vs 非凍結）の差を顕在化させる。
    c = c.copy(); c[-1] += 50.0
    h = h.copy(); h[-1] = c[-1] + 1.0
    l = l.copy(); l[-1] = c[-1] - 1.0
    w = 120
    off = compute_rmm(h, l, c, v, window=w, freeze_last=False)
    on = compute_rmm(h, l, c, v, window=w, freeze_last=True)
    np.testing.assert_array_equal(off.level_count[:-1], on.level_count[:-1])
    assert off.level_count[-1] != on.level_count[-1]
