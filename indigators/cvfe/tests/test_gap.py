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
    """(バー t の最初のティック − バー t−1 の最後のティック) > 1.5 × Δ* でも成立（§4.7-1）。"""
    assert is_gap_bar(3600.0, 0.0, 3600.0, t_first=3600.0, t_last_prev=2000.0,
                      delta_star_sec=300.0)          # 間隔 1600 > 450
    assert not is_gap_bar(3600.0, 0.0, 3600.0, t_first=3600.0, t_last_prev=3300.0,
                          delta_star_sec=300.0)      # 間隔 300 <= 450


def test_zero_delta_star_makes_every_bar_a_gap_bar():
    """Δ* = 0（RRANGE / PARK）では第 2 条件が常に成立する（仕様の未定義域・ISSUE-209）。

    仕様 §3.2 は RRANGE / PARK で ``delta_star_sec = 0`` と定め、§4.7-1 は
    ``> 1.5 × delta_star_sec`` を課す。ティック時刻は狭義単調増加であるから
    差は必ず正であり、判定は常に真になる。本テストはこの帰結を明示的に固定する。
    """
    assert is_gap_bar(3600.0, 0.0, 3600.0, t_first=3600.0, t_last_prev=3599.9,
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
