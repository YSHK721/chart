"""zp_grid_scan（ISSUE-079: bp 相対格子の無次元校正スキャン）の純関数検証。

スキャンの目的: 「統計が成立する最小セルあたり分数」（時間不変の無次元定数）を実測で確定し、
bp 幅（価格比・log 一様格子）を導出する。判定は帰無サロゲート自身への z 適用の偽陽性率
（|z|>=閾値の出現率が名目正規裾から膨張しない最細幅）＝Step2c で確立した「シミュレーション
校正で判定」の流儀。実データランは CLI（本テストは合成データで数学の正しさのみ固定する）。
"""
from __future__ import annotations

import numpy as np
import pytest

from zp_grid_scan import (
    bp_to_wlog,
    fpr_of_surrogates,
    null_b_moments_log,
    obs_cell_counts_log,
)


def test_bp_to_wlog_is_log1p_of_fraction():
    # 10bp = 0.1% → w_log = ln(1.001)。セル価格幅は価格に比例（相対格子の定義）。
    assert bp_to_wlog(10) == pytest.approx(np.log(1.001))
    # 隣接セル中心の価格比が一定＝exp(w_log)。
    w = bp_to_wlog(50)
    p0 = np.exp(100 * w)
    p1 = np.exp(101 * w)
    assert p1 / p0 == pytest.approx(np.exp(w))


def test_obs_cell_counts_log_counts_minutes_per_log_cell():
    w = bp_to_wlog(100)  # 1% セル（粗め・検証しやすい）
    # 価格 100 → ln100/w のセル。1% 上の価格は隣セル。
    closes = np.array([100.0, 100.0, 101.05, 100.0])
    klo = int(np.floor(np.log(100.0) / w))
    khi = klo + 1
    obs = obs_cell_counts_log(closes, klo, khi, w)
    assert obs.shape == (2,)
    assert obs[0] == 3 and obs[1] == 1


def test_null_b_moments_log_shapes_and_determinism():
    rng1 = np.random.default_rng(7)
    rng2 = np.random.default_rng(7)
    L, G = 30, 120
    S = np.random.default_rng(1).normal(scale=1e-3, size=(L, G))
    w = bp_to_wlog(20)
    klo = int(np.floor(np.log(20000.0) / w)) - 5
    khi = klo + 10
    m1, v1 = null_b_moments_log(S, 20000.0, klo, khi, w, rng=rng1, m_reps=200)
    m2, v2 = null_b_moments_log(S, 20000.0, klo, khi, w, rng=rng2, m_reps=200)
    assert m1.shape == v1.shape == (11,)
    np.testing.assert_array_equal(m1, m2)  # 同 seed 決定論。
    assert float(m1.sum()) <= G  # 総占有分数はセッション分数以下（レンジ外棄却あり）。
    assert (v1 >= 0).all()


def test_fpr_of_surrogates_mechanics():
    # 同一生成器のサロゲートを同じ帰無モーメントで z 化 → 偽陽性率は小さい（<5%）・
    #   定義セル率（var>0）とともに返る。閾値は z>=3（片側）。
    rng = np.random.default_rng(11)
    L, G = 40, 200
    S = np.random.default_rng(2).normal(scale=8e-4, size=(L, G))
    w = bp_to_wlog(15)
    open_d = 20000.0
    lk = np.log(open_d) / w
    klo, khi = int(lk) - 8, int(lk) + 8
    mean, var = null_b_moments_log(S, open_d, klo, khi, w, rng=rng, m_reps=400)
    out = fpr_of_surrogates(S, open_d, klo, khi, w, mean, var, rng=rng, m_surr=100, z_thr=3.0)
    assert set(out) >= {"fpr", "defined_share", "cells", "exceed"}
    assert 0.0 <= out["fpr"] < 0.05
    assert 0.0 < out["defined_share"] <= 1.0
