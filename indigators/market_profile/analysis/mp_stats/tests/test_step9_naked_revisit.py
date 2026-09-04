"""Step9（ISSUE-061）の判定部品を決定論的な合成入力で固定する。

実測の本体（15 年分の走査）とは別に、事件抽出と反応判定の規則が事前登録どおりであることを
小さな手計算可能な入力で確認する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_ANALYSIS_ROOT = Path(__file__).resolve().parents[2]
if str(_ANALYSIS_ROOT) not in sys.path:
    sys.path.insert(0, str(_ANALYSIS_ROOT))

from mp_stats.step9_naked_revisit import (  # noqa: E402
    BOUNCE_ROWS,
    N_ROWS_DAILY,
    REACTION_MINUTES,
    _first_touch,
    bounced,
    row_prices,
)


def test_row_prices_maps_index_to_log_grid():
    """``price = exp(k · grid_w · 1e-4)``（対数価格 1e-4 格子）。"""
    got = row_prices(kmin=91108, n_rows=3, grid_w=1.0)
    expect = np.exp(np.array([91108, 91109, 91110]) * 1e-4)
    np.testing.assert_allclose(got, expect, rtol=0, atol=0)


def test_first_touch_detects_entering_the_cell():
    closes = np.array([100.0, 100.0, 100.4, 101.0])
    assert _first_touch(closes, level=100.5, tol=0.25) == 2   # |100.4−100.5| <= 0.25


def test_first_touch_detects_crossing_without_landing_in_the_cell():
    """セル幅は細いため、1 分で跨いだ再訪を近接判定だけでは取りこぼす。

    近接だけで判定すると事件が**系統的に欠落**し、群ごとに欠落率が違えば比較が歪む。
    """
    closes = np.array([100.0, 101.0])                          # 100.5 を跨ぐが近接しない
    assert _first_touch(closes, level=100.5, tol=0.01) == 1


def test_first_touch_returns_none_when_never_reached():
    closes = np.array([100.0, 100.1, 100.2])
    assert _first_touch(closes, level=200.0, tol=0.01) is None


def test_bounce_requires_moving_back_x_rows_within_k_minutes():
    """上から接近 → k 分以内に x 行以上**上へ**戻れば跳ね返り。"""
    row = 1.0
    level = 100.0
    need = BOUNCE_ROWS * row                                   # = 4.0
    # 直前は水準の上（101）→ 接触後に +4 行以上戻る
    closes = np.concatenate([[101.0, 100.0], np.full(REACTION_MINUTES, level + need)])
    assert bounced(closes, idx=1, level=level, row_width=row, cell_width=0.2) is True

    # 戻りが x 行に届かない
    closes_small = np.concatenate([[101.0, 100.0], np.full(REACTION_MINUTES, level + need - 0.1)])
    assert bounced(closes_small, idx=1, level=level, row_width=row, cell_width=0.2) is False


def test_bounce_direction_is_taken_from_the_approach_side():
    """下から接近したときは**下へ**戻ることが跳ね返り（方向を取り違えない）。"""
    row, level = 1.0, 100.0
    need = BOUNCE_ROWS * row
    # 直前は水準の下（99）→ 上へ伸びても跳ね返りではない
    up = np.concatenate([[99.0, 100.0], np.full(REACTION_MINUTES, level + need)])
    assert bounced(up, idx=1, level=level, row_width=row, cell_width=0.2) is False
    # 下へ戻れば跳ね返り
    down = np.concatenate([[99.0, 100.0], np.full(REACTION_MINUTES, level - need)])
    assert bounced(down, idx=1, level=level, row_width=row, cell_width=0.2) is True


def test_bounce_is_undecided_when_approach_direction_is_ambiguous():
    """接触直前が既に水準上なら接近方向が定まらない＝判定不能（両群で同じ規則）。"""
    row, level = 1.0, 100.0
    closes = np.concatenate([[100.05, 100.0], np.full(REACTION_MINUTES, 110.0)])
    assert bounced(closes, idx=1, level=level, row_width=row, cell_width=0.2) is None


def test_bounce_window_is_limited_to_k_minutes():
    """k 分を超えてからの戻りは数えない。"""
    row, level = 1.0, 100.0
    need = BOUNCE_ROWS * row
    tail = np.full(REACTION_MINUTES + 5, level)
    tail[-1] = level + need                                    # k 分より後にだけ到達
    closes = np.concatenate([[101.0], tail])
    assert bounced(closes, idx=1, level=level, row_width=row, cell_width=0.2) is False


@pytest.mark.parametrize("bad_idx", [0])
def test_bounce_needs_a_previous_minute(bad_idx):
    closes = np.full(REACTION_MINUTES + 2, 100.0)
    assert bounced(closes, idx=bad_idx, level=100.0, row_width=1.0, cell_width=0.2) is None


def test_row_unit_is_the_daily_forty_row_grid():
    """反応距離の単位は Step5/Step6 と同一（日レンジ / 40）。

    znull の**セル**幅（対数 1e-4 ≒ 0.9pt @9,000）で測ると 4 行 ≒ 3.6pt となり、30 分あれば
    ほぼ常に到達して検定が飽和する（実測: 本物 72.4% / 偽 72.9% と両群とも高止まりした）。
    """
    assert N_ROWS_DAILY == 40
    assert BOUNCE_ROWS == 4                                     # = 日レンジの 10%（Step6 と同義）
