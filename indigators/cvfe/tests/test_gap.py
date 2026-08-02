"""ギャップ成分の検証（仕様 §4.7）。"""

import sys
from pathlib import Path

import numpy as np
import pytest

_PKG_DIR = Path(__file__).resolve().parents[1]
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

from src.gap import (  # noqa: E402
    GAP_FACTOR,
    GAP_INIT_BARS,
    GapEwma,
    initial_gap_variance,
    is_gap_bar,
)


def test_gap_constants_match_specification():
    """判定係数 1.5・初期化本数 200（仕様 §4.7-1・§4.7-3）。"""
    assert GAP_FACTOR == 1.5
    assert GAP_INIT_BARS == 200


def test_bar_length_criterion():
    """bar_edges[t] − bar_edges[t−1] > 1.5 × bar_interval_sec でギャップ保有（§4.7-1）。"""
    # バー t−1 の長さが公称の 2 倍 → 第 1 条件で成立。
    assert is_gap_bar(edge_t=7200.0, edge_prev=0.0, bar_interval_sec=3600.0,
                      t_first=7200.0, t_last_prev=7195.0, delta_star_sec=300.0)
    # 長さが公称どおりでティック間隔も小さい → 非保有。
    assert not is_gap_bar(edge_t=3600.0, edge_prev=0.0, bar_interval_sec=3600.0,
                          t_first=3600.0, t_last_prev=3595.0, delta_star_sec=300.0)


def test_tick_interval_criterion():
    """``bar_edges[t] − バー t−1 の最後のティック > 1.5 × Δ*`` でも成立（§4.7-1・ISSUE-216 改訂後）。"""
    assert is_gap_bar(3600.0, 0.0, 3600.0, t_first=3600.0, t_last_prev=2000.0,
                      delta_star_sec=300.0)          # 3600 − 2000 = 1600 > 450
    assert not is_gap_bar(3600.0, 0.0, 3600.0, t_first=3600.0, t_last_prev=3300.0,
                          delta_star_sec=300.0)      # 3600 − 3300 = 300 <= 450


def test_tick_interval_criterion_does_not_read_current_bar_ticks():
    """条件 2 は当該バーのティックを参照しない（§4 柱書の因果律・ISSUE-216 の裁定）。

    v1.0 は被減数が「バー t の**最初の**ティック時刻」であったため、``σ̂_t`` をバー開始時点で
    確定できなかった（``t_first ∈ [bar_edges[t], bar_edges[t+1])``）。裁定後は被減数が
    ``bar_edges[t]`` であるから、``t_first`` をどう動かしても判定は変わらない。
    """
    base = dict(edge_t=3600.0, edge_prev=0.0, bar_interval_sec=3600.0,
                t_last_prev=2000.0, delta_star_sec=300.0)
    verdicts = {is_gap_bar(t_first=tf, **base) for tf in (3600.0, 3900.0, 7199.0, float("nan"))}
    assert verdicts == {True}, "t_first の値・欠損に依らず判定が一定であること"

    base_no = dict(base, t_last_prev=3300.0)
    verdicts_no = {is_gap_bar(t_first=tf, **base_no) for tf in (3600.0, 3900.0, 7199.0)}
    assert verdicts_no == {False}


def test_zero_delta_star_disables_the_second_criterion():
    """Δ* = 0（RRANGE / PARK）では条件 2 を評価しない（§4.7-1・ISSUE-209 の裁定）。

    v1.0 の式へ ``delta_star_sec = 0`` を代入すると条件 2 は「差 > 0」となり、ティック時刻が
    狭義単調増加である以上つねに成立した（＝全バーがギャップ保有と判定され、実際にはギャップの
    無いバーにも ``σ̂_CO,t > 0`` が加算されて ``σ̂_t`` が系統的に過大になった）。
    ``delta_star_sec = 0`` は「サンプリング間隔を持たない」ことを表す番兵であって閾値 0 秒では
    ないため、裁定後は条件 1 のみで判定する。
    """
    # 公称どおりの長さ＝条件 1 は不成立。Δ*=0 でもギャップ保有にはならない。
    assert not is_gap_bar(3600.0, 0.0, 3600.0, t_first=3600.0, t_last_prev=3599.9,
                          delta_star_sec=0.0)
    # 前バーの最後のティックがどれだけ古くても、Δ*=0 では条件 2 を見ない。
    assert not is_gap_bar(3600.0, 0.0, 3600.0, t_first=3600.0, t_last_prev=0.0,
                          delta_star_sec=0.0)
    # 条件 1（バー長）は Δ*=0 でも従来どおり効く。
    assert is_gap_bar(7200.0, 0.0, 3600.0, t_first=7200.0, t_last_prev=7195.0,
                      delta_star_sec=0.0)


def test_ewma_reads_previous_state_before_update():
    """σ̂_CO,t = sqrt(v_{t−1})：更新前の値を読む（仕様 §4.7-4・因果性）。"""
    e = GapEwma(4.0, 0.97)
    assert e.current() == 4.0
    e.update(10.0)                                   # v = 0.97*4 + 0.03*100 = 6.88
    assert e.current() == pytest.approx(0.97 * 4.0 + 0.03 * 100.0)


def test_ewma_copy_is_independent():
    e = GapEwma(1.0, 0.9)
    c = e.copy()
    e.update(2.0)
    assert c.current() == 1.0


def test_initial_gap_variance_uses_first_200_or_all():
    """先頭 200 本の g² の平均。200 本未満なら存在する全本数の平均（仕様 §4.7-3）。"""
    g2 = np.arange(1.0, 301.0)
    assert initial_gap_variance(g2, 200) == pytest.approx(np.arange(1.0, 201.0).mean())
    short = np.array([2.0, 4.0, 6.0])
    assert initial_gap_variance(short, 200) == pytest.approx(4.0)
    assert initial_gap_variance(np.empty(0), 200) == 0.0
