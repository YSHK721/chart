"""``rolling_span`` / ``compute_rmm_level_count`` / ``compute_rmmmacd`` の
``freeze_last`` 回帰検証。

freeze_last は「形成中（足内）の最新足をティック粒度で採点する際、スパン（採点の
分母）の標準化基準（avg±3σ・窓 W）を 1 足 1 回・足内で固定（凍結）する」ための
加算的・既定 OFF オプション。rolling_span は profit_rmm の verbatim 複製であり、
freeze_last も同一実装。profit_system._causal_z の freeze_last と整合させる。

本テストが禁止する誤り（回帰ガード）:
    1. freeze_last=False が従来出力から 1 ビットでも変わること（既定の挙動不変）。
    2. freeze_last=True が rolling_span の out[-1] 以外（out[0..n-2]）を変えること。
    3. freeze_last=True の out[-1] が「直前 W 本（確定足）基準」になっていないこと。
    4. n<=W の端条件で out[-1] が NaN にならないこと。
    5. compute_rmm_level_count / compute_rmmmacd 公開経路で freeze_last が
       rolling_span へ素通ししないこと。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # = profit_rmm_macd/

from src.core import (  # noqa: E402
    compute_rmm_level_count,
    compute_rmmmacd,
    rolling_span,
)


# ---------------------------------------------------------------------------
# 独立参照実装（関数 under test とは別経路）。
# ---------------------------------------------------------------------------
def _ref_rolling_span(a: np.ndarray, window: int, clamp: bool) -> np.ndarray:
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
    a = np.asarray(a, dtype=np.float64)
    n = a.size
    if n < window + 1:
        return float("nan")
    prior = a[n - 1 - window : n - 1]  # a[n-1-W .. n-2]
    avg = float(np.mean(prior))
    dev = float(np.sqrt(np.mean((prior - avg) ** 2)))
    x3p, x3m = avg + 3.0 * dev, avg - 3.0 * dev
    if clamp:
        x3p, x3m = min(100.0, x3p), max(0.0, x3m)
    return x3p - x3m


# =========================================================== 1. 既定の挙動不変
def test_freeze_last_false_matches_legacy_exactly():
    a = np.array([10.0, 20.0, 40.0, 70.0, 30.0, 90.0, 50.0, 60.0], dtype=np.float64)
    w = 4
    for clamp in (True, False):
        legacy = rolling_span(a, w, clamp=clamp)
        explicit_false = rolling_span(a, w, clamp=clamp, freeze_last=False)
        np.testing.assert_array_equal(legacy, explicit_false)


def test_freeze_last_false_matches_independent_reference():
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
    a = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 100.0], dtype=np.float64)
    w = 3
    base = rolling_span(a, w, clamp=False, freeze_last=False)
    frozen = rolling_span(a, w, clamp=False, freeze_last=True)
    np.testing.assert_array_equal(base[:-1], frozen[:-1])
    assert base[-1] != frozen[-1]


def test_freeze_last_true_last_uses_prior_window_base():
    a = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 100.0], dtype=np.float64)
    w = 3
    for clamp in (True, False):
        frozen = rolling_span(a, w, clamp=clamp, freeze_last=True)
        expected_last = _ref_frozen_last_span(a, w, clamp)
        assert np.isclose(frozen[-1], expected_last, rtol=0, atol=1e-9)


def test_freeze_last_true_degenerate_dev_zero_span_is_zero():
    a = np.array([7.0, 5.0, 5.0, 5.0, 42.0], dtype=np.float64)
    w = 3
    frozen = rolling_span(a, w, clamp=False, freeze_last=True)
    assert frozen[-1] == 0.0


# =================================================== 3. 端条件 n<=W で out[-1]=NaN
def test_freeze_last_true_edge_n_equals_window_is_nan():
    a = np.array([1.0, 2.0, 5.0], dtype=np.float64)
    w = 3
    base = rolling_span(a, w, clamp=False, freeze_last=False)
    frozen = rolling_span(a, w, clamp=False, freeze_last=True)
    assert np.isfinite(base[-1])
    assert np.isnan(frozen[-1])


def test_freeze_last_true_edge_n_just_above_window_is_finite():
    a = np.array([1.0, 2.0, 3.0, 9.0], dtype=np.float64)
    w = 3
    frozen = rolling_span(a, w, clamp=False, freeze_last=True)
    assert np.isfinite(frozen[-1])
    assert np.isclose(
        frozen[-1], _ref_frozen_last_span(a, w, clamp=False), rtol=0, atol=1e-9
    )


# ============================ 4. 公開経路の素通し（level_count / macd・挙動不変＋伝播）
def _synth_ohlcv(n: int, seed: int = 11):
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0.0, 1.0, n))
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    vol = rng.uniform(100.0, 1000.0, n)
    return high, low, close, vol


def test_level_count_window_none_ignores_freeze_last():
    h, l, c, v = _synth_ohlcv(200)
    off = compute_rmm_level_count(h, l, c, v, window=None, freeze_last=False)
    on = compute_rmm_level_count(h, l, c, v, window=None, freeze_last=True)
    np.testing.assert_array_equal(off, on)


def test_level_count_default_freeze_last_false_unchanged():
    h, l, c, v = _synth_ohlcv(200)
    w = 120
    legacy = compute_rmm_level_count(h, l, c, v, window=w)
    explicit_false = compute_rmm_level_count(h, l, c, v, window=w, freeze_last=False)
    np.testing.assert_array_equal(legacy, explicit_false)


def test_level_count_freeze_last_changes_only_last():
    """compute_rmm_level_count(window=W, freeze_last=True) は最終点のみ変えうる。"""
    h, l, c, v = _synth_ohlcv(200)
    c = c.copy(); c[-1] += 50.0
    h = h.copy(); h[-1] = c[-1] + 1.0
    l = l.copy(); l[-1] = c[-1] - 1.0
    w = 120
    off = compute_rmm_level_count(h, l, c, v, window=w, freeze_last=False)
    on = compute_rmm_level_count(h, l, c, v, window=w, freeze_last=True)
    np.testing.assert_array_equal(off[:-1], on[:-1])
    assert off[-1] != on[-1]


def test_rmmmacd_default_freeze_last_false_unchanged():
    """compute_rmmmacd(window=W) は freeze_last 既定 False で全フィールド従来一致。"""
    h, l, c, v = _synth_ohlcv(200)
    w = 120
    legacy = compute_rmmmacd(h, l, c, v, window=w)
    explicit_false = compute_rmmmacd(h, l, c, v, window=w, freeze_last=False)
    for name in ("level_count", "fast", "slow", "macd", "signal", "histogram"):
        np.testing.assert_array_equal(
            getattr(legacy, name), getattr(explicit_false, name)
        )


def test_rmmmacd_freeze_last_propagates_to_level_count():
    """compute_rmmmacd(freeze_last=True) の level_count が
    compute_rmm_level_count(freeze_last=True) と一致する（素通し）。"""
    h, l, c, v = _synth_ohlcv(200)
    c = c.copy(); c[-1] += 50.0
    h = h.copy(); h[-1] = c[-1] + 1.0
    l = l.copy(); l[-1] = c[-1] - 1.0
    w = 120
    macd = compute_rmmmacd(h, l, c, v, window=w, freeze_last=True)
    expected_lc = compute_rmm_level_count(h, l, c, v, window=w, freeze_last=True)
    np.testing.assert_array_equal(macd.level_count, expected_lc)
    # 凍結で level_count の最終点が変わると histogram の最終点も EMA 連鎖で追従する。
    base = compute_rmmmacd(h, l, c, v, window=w, freeze_last=False)
    assert base.level_count[-1] != macd.level_count[-1]
    # 回帰ガード: 凍結は「最終点のみ」変える。全下流フィールドの非最終点 [:-1] は不変、
    #   最終点のみ変化（EMA 連鎖が level_count の最終点変化を追従）。これが破れると将来の
    #   EMA 実装変更で「凍結が過去点まで書き換える」退行を静かに見逃す。
    for name in ("level_count", "fast", "slow", "macd", "signal", "histogram"):
        off_f = getattr(base, name)
        on_f = getattr(macd, name)
        np.testing.assert_array_equal(off_f[:-1], on_f[:-1])  # 非最終点は不変（NaN 同値許容）
        assert off_f[-1] != on_f[-1]  # 最終点のみ変化
