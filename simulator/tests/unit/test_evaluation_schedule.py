"""評価点と評価スケジュール（ISSUE-479 Wave2 4-8/4-9・S-1）。

固定する仕様:
    エンジンが口座を再評価する「点」を値として取り出し、その点をいつ生むかを
    スケジュールが決める。バー粒度は 1 バー 1 点、ティック粒度は 1 ティック 1 点。

なぜ点を値にするか:
    移設前、2 つのエンジンは「どこで評価するか」と「評価点で何をするか」を混ぜて
    持っていた。バー用エンジンは bar.close で、ティック用エンジンはティック価格で、
    それぞれ自前に H（SL/TP 到達）・建玉変更・I（口座再評価）を書いていた。
    点を値にすると、「何をするか」は 1 つの手続きになり、「どこで」だけがスケジュールの
    差になる。2 つのエンジンが同じ手続きを共有できるようになる。

なぜ点の数を測るか（計算量）:
    生んだ点をすべて消費していること（発行 − 使用 = 0）は、スケジュールが
    「作ってから捨てる」形になっていないことの表明である。点を余分に作っても出力は
    変わらない（捨てるから）ので、状態検証では原理的に落ちない。
"""
from __future__ import annotations

import numpy as np
import pytest

from simulator.domain.bar import Bar
from simulator.usecase.bar_schedule import BarSchedule
from simulator.usecase.evaluation_point import (
    BAR_GRANULARITY,
    TICK_GRANULARITY,
    EvaluationPoint,
)
from simulator.usecase.ports import EvaluationSchedulePort


def _bar(t="2024-01-01T00:00", o=1.10, h=1.15, low=1.05, c=1.12, spread=0):
    return Bar(
        time=np.datetime64(t), open=o, high=h, low=low, close=c,
        volume=1.0, spread=spread,
    )


def _bar_schedule(basis="close", point_size=0.00001):
    return BarSchedule(floating_pnl_basis=basis, point_size=point_size)


class TestTheBarScheduleYieldsOnePointPerBar:
    """バー粒度は 1 バー 1 点であること。"""

    def test_it_satisfies_the_schedule_port(self):
        assert isinstance(_bar_schedule(), EvaluationSchedulePort)

    def test_it_names_itself(self):
        assert _bar_schedule().id == "bar"

    def test_a_bar_produces_exactly_one_point(self):
        points = list(_bar_schedule().points(3, _bar(), prev_close=1.09))
        assert len(points) == 1

    def test_the_point_carries_the_position_it_was_taken_at(self):
        bar = _bar()
        point = next(iter(_bar_schedule().points(7, bar, prev_close=1.09)))
        assert point.bar_index == 7
        assert point.bar is bar
        assert point.granularity == BAR_GRANULARITY
        # バー点はティックの序数を持たない（-1＝ティックではない）。
        assert point.tick_ordinal == -1

    def test_the_hit_range_is_the_whole_bar_for_both_sides(self):
        """SL/TP 到達はバーの極値で見る（買い・売りとも同じ範囲）。"""
        bar = _bar(h=1.15, low=1.05)
        point = next(iter(_bar_schedule().points(0, bar, prev_close=1.09)))
        assert (point.hit_buy_high, point.hit_buy_low) == (1.15, 1.05)
        assert (point.hit_sell_high, point.hit_sell_low) == (1.15, 1.05)

    def test_the_position_manager_reference_is_the_reached_extreme(self):
        """建玉変更の参照はトレーリング方向の到達価格（買い=high / 売り=low）。"""
        bar = _bar(h=1.15, low=1.05)
        point = next(iter(_bar_schedule().points(0, bar, prev_close=1.09)))
        assert point.pm_ref_buy == 1.15
        assert point.pm_ref_sell == 1.05

    def test_the_evaluation_quote_follows_the_floating_basis(self):
        bar = _bar(c=1.12, spread=100)
        close_point = next(iter(_bar_schedule("close").points(0, bar, prev_close=1.0)))
        assert (close_point.eval_bid, close_point.eval_ask) == (1.12, 1.12)
        bid_ask_point = next(
            iter(_bar_schedule("bid_ask", point_size=0.001).points(0, bar, prev_close=1.0))
        )
        # 売り保有は Ask = close + spread × point で悲観評価する。
        assert bid_ask_point.eval_bid == 1.12
        assert bid_ask_point.eval_ask == pytest.approx(1.12 + 100 * 0.001)

    def test_the_prev_close_is_not_needed_at_bar_granularity(self):
        """バー粒度は前足の終値を見ない（ティック生成に要る値であって評価には無関係）。"""
        bar = _bar()
        a = next(iter(_bar_schedule().points(0, bar, prev_close=None)))
        b = next(iter(_bar_schedule().points(0, bar, prev_close=999.0)))
        assert a == b


class TestTheEvaluationPointIsAValue:
    """点は評価中に書き換わらない値であること。"""

    def _point(self):
        return next(iter(_bar_schedule().points(0, _bar(), prev_close=1.0)))

    def test_a_point_cannot_be_mutated(self):
        with pytest.raises(Exception):
            self._point().eval_bid = 0.0

    def test_points_compare_by_what_they_carry_not_by_identity(self):
        """点は値なので、同じ材料から作れば別の実体でも等しい（違えば等しくない）。"""
        taken_once = next(iter(_bar_schedule().points(0, _bar(), prev_close=1.0)))
        taken_again = next(iter(_bar_schedule().points(0, _bar(), prev_close=1.0)))
        assert taken_once is not taken_again
        assert taken_once == taken_again
        from_other_bar = next(iter(_bar_schedule().points(0, _bar(h=9.9), prev_close=1.0)))
        assert taken_once != from_other_bar

    def test_the_granularity_names_are_the_two_the_engine_knows(self):
        assert {BAR_GRANULARITY, TICK_GRANULARITY} == {"bar", "tick"}

    def test_a_bar_point_is_not_a_synthetic_carry_forward_point(self):
        assert self._point().is_synthetic_bar_point is False


class TestTheBarScheduleDoesNotWasteWork:
    """計算量検定（発行 − 使用 = 0）。測るのは時間ではなく回数。"""

    def test_the_evaluation_quote_is_resolved_once_per_point(self, monkeypatch):
        # Arrange
        import simulator.usecase.bar_schedule as mod

        resolved: "list[int]" = []
        original = mod.resolve_eval_quote
        monkeypatch.setattr(
            mod, "resolve_eval_quote",
            lambda bar, **kw: (resolved.append(1), original(bar, **kw))[1],
        )
        # Act
        points = list(_bar_schedule().points(0, _bar(), prev_close=1.0))
        # Assert: 発行（クォート解決）− 使用（生んだ点）= 0。
        assert len(resolved) - len(points) == 0

    def test_the_point_count_tracks_bars_not_anything_else(self):
        """バー 50 本 / 200 本の 2 点で「点数 == バー数」（オーダーの表明）。"""
        schedule = _bar_schedule()
        measured = {}
        for bar_count in (50, 200):
            produced = 0
            for i in range(bar_count):
                produced += len(list(schedule.points(i, _bar(), prev_close=1.0)))
            measured[bar_count] = produced
        for bar_count, produced in measured.items():
            assert produced - bar_count == 0, measured

    def test_nothing_is_computed_before_the_first_point_is_asked_for(self, monkeypatch):
        """点は求められて初めて作られる（先回りして作り置きしない）。"""
        import simulator.usecase.bar_schedule as mod

        resolved: "list[int]" = []
        original = mod.resolve_eval_quote
        monkeypatch.setattr(
            mod, "resolve_eval_quote",
            lambda bar, **kw: (resolved.append(1), original(bar, **kw))[1],
        )
        stream = _bar_schedule().points(0, _bar(), prev_close=1.0)
        assert len(resolved) == 0
        next(iter(stream))
        assert len(resolved) - 1 == 0
