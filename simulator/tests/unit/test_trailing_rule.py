"""domain/trailing_rule.py の TrailingRule テスト（Phase 7 FR-07・トレーリング）.

TrailingRule（Value Object・pandas/JSON 非依存・点数駆動）:
    TrailingRule(trigger_points, distance_points, step_points, point_size)
    new_stop(side, entry, ref_price, current_sl) -> float|None
      - trigger_points 未満の含み益では作動しない（None＝据え置き）。
      - 作動時は ref_price から distance_points 離した価格を新 SL 候補とする。
      - tighten_only: 緩める方向（買い=SL 下げ / 売り=SL 上げ）は None（据え置き）。
      - step_points>0 のときは current_sl から step_points 以上動く場合のみ更新。
      - step_points==0 は連続（厳密に締まる限り常に更新）。
    点数→価格換算は point_size を乗じる（sltp_from_points と同規則）。
    buy/sell は対称。current_sl=None は初回設定として作動する。
"""
from __future__ import annotations

import pytest

# 共通パラメータ（JP225 相当・point_size=0.1）
_PT = 0.1
_ENTRY = 100.0
# trigger=50pt(=5.0) / distance=30pt(=3.0) / step=10pt(=1.0)


def _rule(step_points=10):
    from simulator.domain.trailing_rule import TrailingRule

    return TrailingRule(
        trigger_points=50,
        distance_points=30,
        step_points=step_points,
        point_size=_PT,
    )


# --- buy 側 -----------------------------------------------------------------

def test_buy_not_triggered_returns_none():
    # 含み益 4.0 < trigger 5.0 → 作動しない
    r = _rule()
    assert r.new_stop("buy", _ENTRY, 104.0, None) is None


def test_buy_triggered_first_stop_from_none():
    # 含み益 6.0 >= 5.0・current_sl None → ref 106 - distance 3.0 = 103.0
    r = _rule()
    assert r.new_stop("buy", _ENTRY, 106.0, None) == pytest.approx(103.0)


def test_buy_trigger_boundary_is_inclusive():
    # 含み益ちょうど 5.0（= trigger）で作動する（>=）
    r = _rule()
    assert r.new_stop("buy", _ENTRY, 105.0, None) == pytest.approx(102.0)


def test_buy_tightens_when_candidate_higher_and_step_met():
    # candidate 103.0 > current_sl 102.0・差 1.0 >= step 1.0 → 103.0
    r = _rule()
    assert r.new_stop("buy", _ENTRY, 106.0, 102.0) == pytest.approx(103.0)


def test_buy_loosening_is_rejected_tighten_only():
    # candidate 103.0 <= current_sl 103.5 → 緩めない（据え置き None）
    r = _rule()
    assert r.new_stop("buy", _ENTRY, 106.0, 103.5) is None


def test_buy_step_gate_blocks_small_move():
    # candidate 103.0・current_sl 102.5・差 0.5 < step 1.0 → 更新しない
    r = _rule()
    assert r.new_stop("buy", _ENTRY, 106.0, 102.5) is None


def test_buy_step_zero_is_continuous():
    # step 0 → 厳密に締まる限り常に更新（差 0.01 でも更新）
    r = _rule(step_points=0)
    assert r.new_stop("buy", _ENTRY, 106.0, 102.99) == pytest.approx(103.0)


# --- sell 側（対称） --------------------------------------------------------

def test_sell_not_triggered_returns_none():
    r = _rule()
    assert r.new_stop("sell", _ENTRY, 96.0, None) is None


def test_sell_triggered_first_stop_from_none():
    # 含み益 6.0・current_sl None → ref 94 + distance 3.0 = 97.0
    r = _rule()
    assert r.new_stop("sell", _ENTRY, 94.0, None) == pytest.approx(97.0)


def test_sell_tightens_when_candidate_lower_and_step_met():
    # candidate 97.0 < current_sl 98.0・差 1.0 >= step 1.0 → 97.0
    r = _rule()
    assert r.new_stop("sell", _ENTRY, 94.0, 98.0) == pytest.approx(97.0)


def test_sell_loosening_is_rejected_tighten_only():
    # candidate 97.0 >= current_sl 96.5 → 緩めない（None）
    r = _rule()
    assert r.new_stop("sell", _ENTRY, 94.0, 96.5) is None


def test_sell_step_gate_blocks_small_move():
    # candidate 97.0・current_sl 97.5・差 0.5 < step 1.0 → 更新しない
    r = _rule()
    assert r.new_stop("sell", _ENTRY, 94.0, 97.5) is None
