"""domain/sltp.py の `sltp_from_points` テスト（Phase 6 F-8・点数→絶対価格換算の単一ソース）.

規則（tc24051901.py:64-72 と同一・写経せず単一ソース化）:
    dist = points × point_size
    buy : sl = base − sl_dist / tp = base + tp_dist
    sell: sl = base + sl_dist / tp = base − tp_dist
    points == 0 → その脚は None（SL または TP を置かない）。
"""
from __future__ import annotations

import math

import pytest


def test_buy_sltp_below_and_above_base():
    # Arrange / Act
    from simulator.domain.sltp import sltp_from_points

    sl, tp = sltp_from_points("buy", 1.2010, 100, 200, 0.0001)

    # Assert: buy は sl=base−dist / tp=base+dist（tc24051901 規則）
    assert sl == pytest.approx(1.2010 - 100 * 0.0001)
    assert tp == pytest.approx(1.2010 + 200 * 0.0001)


def test_sell_sltp_is_mirror_of_buy():
    # Arrange / Act: sell は buy と対称（sl=base+dist / tp=base−dist）
    from simulator.domain.sltp import sltp_from_points

    sl, tp = sltp_from_points("sell", 1.2990, 100, 200, 0.0001)

    # Assert
    assert sl == pytest.approx(1.2990 + 100 * 0.0001)
    assert tp == pytest.approx(1.2990 - 200 * 0.0001)


def test_zero_sl_points_yields_none_sl():
    # Arrange / Act: sl_points==0 は SL を置かない（None）
    from simulator.domain.sltp import sltp_from_points

    sl, tp = sltp_from_points("buy", 1.2000, 0, 150, 0.0001)

    # Assert: SL は None・TP は算出
    assert sl is None
    assert tp == pytest.approx(1.2000 + 150 * 0.0001)


def test_zero_tp_points_yields_none_tp():
    # Arrange / Act: tp_points==0 は TP を置かない（None）
    from simulator.domain.sltp import sltp_from_points

    sl, tp = sltp_from_points("sell", 1.2000, 80, 0, 0.0001)

    # Assert
    assert sl == pytest.approx(1.2000 + 80 * 0.0001)
    assert tp is None


def test_both_zero_points_yields_none_none():
    # Arrange / Act: 両方 0 なら SL/TP なし
    from simulator.domain.sltp import sltp_from_points

    sl, tp = sltp_from_points("buy", 1.5, 0, 0, 0.01)

    # Assert
    assert sl is None and tp is None


def test_unknown_side_raises():
    # Arrange / Act / Assert: 未知の side は黙って売り扱いにせず例外（無音の誤建値禁止）
    from simulator.domain.sltp import sltp_from_points

    with pytest.raises(ValueError):
        sltp_from_points("long", 1.2, 10, 10, 0.0001)
