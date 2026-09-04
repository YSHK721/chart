"""``_causal_z`` / ``ps_level_count`` の ``freeze_last`` 回帰検証。

freeze_last は「形成中（足内）の最新足をティック粒度で評価する際、標準化窓 W の基準
（平均/σ）を 1 足 1 回・足内で固定（凍結）する」ための加算的・既定 OFF オプション。

本テストが禁止する誤り（回帰ガード）:
    1. freeze_last=False が従来出力から 1 ビットでも変わること（既定の挙動不変）。
    2. freeze_last=True が out[-1] 以外（out[0..n-2]）を変えること。
    3. freeze_last=True の out[-1] が「直前 W 本（確定足）基準」になっていないこと。
    4. n<=W の端条件で out[-1] が NaN にならないこと。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# src（profit_system 配下）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import ps_level_count  # noqa: E402
from src.core import _causal_z, _normalize  # noqa: E402


# ---------------------------------------------------------------------------
# 独立参照実装（関数 under test とは別経路。母標準偏差・分母 window で素直に計算）。
# ---------------------------------------------------------------------------
def _reference_causal_z(a: np.ndarray, window: int) -> np.ndarray:
    """``_causal_z(freeze_last=False)`` の独立参照（np.mean / 母 std を直接使用）。"""
    a = np.asarray(a, dtype=np.float64)
    n = a.size
    out = np.full(n, np.nan, dtype=np.float64)
    if window < 2 or n < window:
        return out
    for i in range(window - 1, n):
        win = a[i - window + 1 : i + 1]
        mean = float(np.mean(win))
        std = float(np.sqrt(np.mean((win - mean) ** 2)))
        out[i] = _normalize((a[i] - mean) / std) if std > 0.0 else 0.0
    return out


def _reference_frozen_last(a: np.ndarray, window: int) -> float:
    """freeze_last=True 時の out[-1] 独立参照（直前 W 本＝確定足基準）。"""
    a = np.asarray(a, dtype=np.float64)
    n = a.size
    if n < window + 1:
        return float("nan")
    prior = a[n - 1 - window : n - 1]  # a[n-1-W .. n-2]（最終点を除く直前 W 本）
    mean = float(np.mean(prior))
    std = float(np.sqrt(np.mean((prior - mean) ** 2)))
    return _normalize((a[-1] - mean) / std) if std > 0.0 else 0.0


# =========================================================== 1. 既定の挙動不変
def test_freeze_last_false_matches_legacy_exactly():
    """freeze_last=False は従来出力（=既定引数省略）と完全一致（1 ビットも変えない）。"""
    a = np.array([1.0, 2.0, 4.0, 7.0, 3.0, 9.0, 5.0, 6.0], dtype=np.float64)
    w = 4
    legacy = _causal_z(a, w)                       # 既定（引数省略）
    explicit_false = _causal_z(a, w, freeze_last=False)
    np.testing.assert_array_equal(legacy, explicit_false)


def test_freeze_last_false_matches_independent_reference():
    """freeze_last=False が独立参照（np.mean/母std）と一致する。"""
    a = np.array([10.0, 11.0, 9.0, 12.0, 8.0, 13.0, 7.0], dtype=np.float64)
    w = 3
    got = _causal_z(a, w, freeze_last=False)
    ref = _reference_causal_z(a, w)
    np.testing.assert_allclose(got[np.isfinite(got)], ref[np.isfinite(ref)], rtol=0, atol=1e-9)
    # NaN 位置（warm-up）も一致。
    assert np.array_equal(np.isnan(got), np.isnan(ref))


# =========================================== 2. freeze_last=True は out[-1] のみ変える
def test_freeze_last_true_changes_only_last_element():
    """最終値を外れ値にした系列で、out[-1] のみ変わり out[0..-2] は不変。"""
    a = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 100.0], dtype=np.float64)  # 末尾外れ値
    w = 3
    base = _causal_z(a, w, freeze_last=False)
    frozen = _causal_z(a, w, freeze_last=True)
    # out[0..n-2] は完全一致。
    np.testing.assert_array_equal(base[:-1], frozen[:-1])
    # out[-1] は変化する（自窓を含む非凍結 vs 直前 W 本の凍結）。
    assert base[-1] != frozen[-1]


def test_freeze_last_true_last_uses_prior_window_base():
    """out[-1] が「直前 W 本（確定足）基準」の z と一致する（独立参照）。"""
    a = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 100.0], dtype=np.float64)
    w = 3
    frozen = _causal_z(a, w, freeze_last=True)
    expected_last = _reference_frozen_last(a, w)  # 直前 W=[3,4,5] 基準
    assert np.isclose(frozen[-1], expected_last, rtol=0, atol=1e-9)


def test_freeze_last_true_degenerate_std_zero_is_zero():
    """直前 W 本が定数（std==0）の縮退時、非凍結と同じく out[-1]=0.0。"""
    a = np.array([7.0, 5.0, 5.0, 5.0, 42.0], dtype=np.float64)  # 直前 W=[5,5,5]
    w = 3
    frozen = _causal_z(a, w, freeze_last=True)
    assert frozen[-1] == 0.0


# =================================================== 3. 端条件 n<=W で out[-1]=NaN
def test_freeze_last_true_edge_n_equals_window_is_nan():
    """n==W（直前 W 本を満たせない）で out[-1]=NaN。非凍結では有限値。"""
    a = np.array([1.0, 2.0, 5.0], dtype=np.float64)
    w = 3  # n == w → n < w+1
    base = _causal_z(a, w, freeze_last=False)
    frozen = _causal_z(a, w, freeze_last=True)
    assert np.isfinite(base[-1])      # 非凍結は最終点を算出する
    assert np.isnan(frozen[-1])       # 凍結は直前 W 本不足で NaN


def test_freeze_last_true_edge_n_just_above_window_is_finite():
    """n==W+1（直前 W 本がちょうど満たせる）で out[-1] は有限。"""
    a = np.array([1.0, 2.0, 3.0, 9.0], dtype=np.float64)
    w = 3  # n == w+1
    frozen = _causal_z(a, w, freeze_last=True)
    assert np.isfinite(frozen[-1])
    assert np.isclose(frozen[-1], _reference_frozen_last(a, w), rtol=0, atol=1e-9)


# ===================================== 4. ps_level_count 公開経路の素通し（挙動不変＋伝播）
def test_ps_level_count_window_none_ignores_freeze_last():
    """window=None（全期間バッチ）経路は freeze_last 無関係（完全一致）。"""
    a = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 100.0], dtype=np.float64)
    off = ps_level_count(a, window=None, freeze_last=False)
    on = ps_level_count(a, window=None, freeze_last=True)
    np.testing.assert_array_equal(off, on)


def test_ps_level_count_default_freeze_last_false_unchanged():
    """ps_level_count(window=W) は freeze_last 既定 False で従来と完全一致。"""
    a = np.array([1.0, 2.0, 4.0, 7.0, 3.0, 9.0, 5.0, 6.0], dtype=np.float64)
    w = 4
    legacy = ps_level_count(a, window=w)
    explicit_false = ps_level_count(a, window=w, freeze_last=False)
    np.testing.assert_array_equal(legacy, explicit_false)


def test_ps_level_count_propagates_freeze_last_to_causal_z():
    """ps_level_count(window=W, freeze_last=True) が _causal_z 凍結結果を加算する。"""
    a = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 100.0], dtype=np.float64)
    w = 3
    got = ps_level_count(a, window=w, freeze_last=True)  # res=None → 0 + z
    expected = _causal_z(a, w, freeze_last=True)
    np.testing.assert_array_equal(got, expected)
