"""§4 価格ラダー（価格降順・距離・直前行差・地平 3 段の直上直下印・行ラベル一意）を固定する。

固定する核心:
  - **束ねない**（依頼者裁定 2026-08-29）。近接水準を帯へ潰さず、重なりは「直前行との
    価格差」で読み取れるようにする（§4.1）。
  - 行の識別子は**パラメータまで含めて一意**（§11-2。`MA` は各時間足に 3〜9 本あり、
    「系列名＋時間足」では 88 行が 68 行へ潰れて測定が狂った）。
"""
from __future__ import annotations

import pytest

from dashboard_ui.domain.horizon import Horizon
from dashboard_ui.domain.price_ladder import (
    DuplicateRowLabelError,
    LevelInput,
    build_ladder,
)


def _level(price: float, timeframe: str = "1m", label: str | None = None) -> LevelInput:
    return LevelInput(price=price, timeframe=timeframe,
                      label=label if label is not None else f"L{price}")


class TestOrderAndColumns:
    def test_rows_are_sorted_by_price_descending(self) -> None:
        ladder = build_ladder(
            [_level(100.0), _level(300.0), _level(200.0)], current_price=150.0)

        assert [row.price for row in ladder.rows] == [300.0, 200.0, 100.0]

    def test_distance_is_signed_against_the_current_price(self) -> None:
        ladder = build_ladder([_level(160.0), _level(140.0)], current_price=150.0)

        assert [row.distance for row in ladder.rows] == [10.0, -10.0]

    def test_the_gap_column_is_the_difference_to_the_row_just_above(self) -> None:
        """§4.7「差」= 直前行との価格差。小さい値が続く所ほど水準が重なっている。"""
        ladder = build_ladder(
            [_level(300.0), _level(280.0), _level(279.0)], current_price=150.0)

        assert [row.gap_to_previous for row in ladder.rows] == [None, 20.0, 1.0]

    def test_the_current_price_takes_its_own_position_in_the_price_order(self) -> None:
        """§4.1 現在値は独立行として価格順の位置に入る。"""
        ladder = build_ladder(
            [_level(300.0), _level(160.0), _level(140.0), _level(100.0)],
            current_price=150.0)

        assert ladder.current_index == 2
        assert ladder.current_price == 150.0

    def test_an_empty_level_set_yields_an_empty_ladder(self) -> None:
        ladder = build_ladder([], current_price=150.0)

        assert ladder.rows == ()
        assert ladder.current_index == 0

    def test_ordering_is_deterministic_for_equal_prices(self) -> None:
        """同値の水準が入れ替わると行が踊る（同じ入力は必ず同じ並び）。"""
        levels = [_level(200.0, "5m", "b"), _level(200.0, "1m", "a")]

        first = build_ladder(levels, current_price=150.0)
        second = build_ladder(list(reversed(levels)), current_price=150.0)

        assert [r.label for r in first.rows] == [r.label for r in second.rows]


class TestHorizonMarks:
    def test_the_nearest_level_above_and_below_is_marked_for_each_horizon(self) -> None:
        ladder = build_ladder(
            [
                _level(400.0, "1D", "long-up"),
                _level(300.0, "4h", "mid-up"),
                _level(160.0, "1m", "short-up"),
                _level(140.0, "1m", "short-dn"),
                _level(80.0, "1h", "mid-dn"),
                _level(50.0, "1W", "long-dn"),
            ],
            current_price=150.0,
        )
        marks = {row.label: row.horizon_marks for row in ladder.rows}

        assert marks["short-up"] == frozenset({Horizon.SHORT})
        assert marks["short-dn"] == frozenset({Horizon.SHORT})
        assert marks["mid-up"] == frozenset({Horizon.MEDIUM})
        assert marks["mid-dn"] == frozenset({Horizon.MEDIUM})
        assert marks["long-up"] == frozenset({Horizon.LONG})
        assert marks["long-dn"] == frozenset({Horizon.LONG})

    def test_one_row_can_be_the_next_target_of_several_horizons(self) -> None:
        """§4.7 の `1W MA ema13 hlc3 ← 中期・下／長期・下` と同じ形。"""
        ladder = build_ladder(
            [_level(160.0, "1m", "s"), _level(60.0, "1W", "w")], current_price=150.0)
        marks = {row.label: row.horizon_marks for row in ladder.rows}

        assert marks["s"] == frozenset({Horizon.SHORT})
        assert marks["w"] == frozenset({Horizon.SHORT, Horizon.MEDIUM, Horizon.LONG})

    def test_a_level_equal_to_the_current_price_is_not_a_next_target(self) -> None:
        """境界値: 「次のターゲット」は現在値より上（下）。同値は上でも下でもない。"""
        ladder = build_ladder(
            [_level(150.0, "1m", "same"), _level(160.0, "1m", "up")], current_price=150.0)
        marks = {row.label: row.horizon_marks for row in ladder.rows}

        assert marks["same"] == frozenset()
        assert marks["up"] == frozenset({Horizon.SHORT})

    def test_a_horizon_without_any_level_on_one_side_simply_has_no_mark(self) -> None:
        ladder = build_ladder([_level(160.0, "1m", "up")], current_price=150.0)

        assert ladder.next_target(Horizon.SHORT, above=True).label == "up"
        assert ladder.next_target(Horizon.SHORT, above=False) is None
        assert ladder.next_target(Horizon.LONG, above=True) is None


class TestValidation:
    def test_duplicate_label_and_timeframe_pairs_are_rejected(self) -> None:
        """§11-2: 識別子の衝突は行を潰し、測定・表示の両方を狂わせる。"""
        with pytest.raises(DuplicateRowLabelError):
            build_ladder(
                [_level(200.0, "5m", "MA ema24 hlc3"), _level(210.0, "5m", "MA ema24 hlc3")],
                current_price=150.0)

    def test_the_same_label_on_different_timeframes_is_allowed(self) -> None:
        ladder = build_ladder(
            [_level(200.0, "5m", "MA ema24 hlc3"), _level(210.0, "1m", "MA ema24 hlc3")],
            current_price=150.0)

        assert len(ladder.rows) == 2

    def test_a_non_finite_level_price_is_rejected(self) -> None:
        """NaN（水準なし）はラダーへ入れない。入れると並びが壊れ、無言で最下段に沈む。"""
        with pytest.raises(ValueError):
            build_ladder([_level(float("nan"))], current_price=150.0)

    def test_a_non_finite_current_price_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            build_ladder([_level(200.0)], current_price=float("nan"))

    def test_an_unknown_timeframe_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            build_ladder([_level(200.0, "2h")], current_price=150.0)
