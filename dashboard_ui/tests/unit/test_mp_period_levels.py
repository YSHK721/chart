"""第 1 表へ合流させる MP の期間水準（設計書 §3.5・依頼者裁定 2026-09-29）。

裁定:
    1. front が借りた値（`/tf_period_profile` の当期の POC・VAH・VAL）を要求の欄 `mp_levels` で
       受け取り、射影行と同じ入口で `build_ladder` へ合流させる（サーバは MP を計算しない）。
    2. 到達時間は定義 D（「`_level_period_touch`」 → domain の `period_first_touch`）。
    3. 次のターゲット印の候補に入れる（`build_ladder` の地平判定がそのまま効く）。

期待値は宣言（domain の唯一源）から導く。行の並び・距離・差は `build_ladder`、到達は
`period_first_touch` を同じ入力で呼んだ結果と突き合わせる（リテラルで写さない）。

構造は AAA。
"""
from __future__ import annotations

import math

import pytest

from dashboard_ui.adapter.controller.reach_sheet_controller import ReachSheetController
from dashboard_ui.adapter.gateway.elapsed_comparison_gateway import (
    ElapsedComparisonGateway,
)
from dashboard_ui.domain.bar import Bar
from dashboard_ui.domain.horizon import Horizon
from dashboard_ui.domain.price_ladder import LevelInput, build_ladder
from dashboard_ui.domain.reach import period_first_touch
from dashboard_ui.usecase.build_reach_sheet import build_reach_sheet
from dashboard_ui.usecase.sheet_models import (
    MP_PERIOD_INDICATOR_ID,
    MpPeriodLevel,
    ReachSheetRequest,
    SheetInstance,
)
from dashboard_ui.usecase.sheet_supply import BarSupply, SeriesSupply
from dashboard_ui.tests.complexity.conftest import (
    BarSpy,
    ForwardSpy,
    Registry,
    Roles,
    SeriesSpy,
)

REF = "jp225_tick"
#: 1D の現在バーの time（期間の始端）。1m 足はこの前後にまたがる。
DAY = 1_790_640_000


def _fine_bars() -> "tuple[Bar, ...]":
    """1m 足 10 本（期間の始端の 3 本前から）。価格は 100 → 109 と 1 ずつ上がる。"""
    return tuple(
        Bar(time=DAY + (index - 3) * 60, open=100.0 + index, high=100.5 + index,
            low=99.5 + index, close=100.0 + index)
        for index in range(10)
    )


def _period_bars() -> "tuple[Bar, ...]":
    return (Bar(time=DAY - 86_400, open=90.0, high=95.0, low=85.0, close=92.0),
            Bar(time=DAY, open=92.0, high=110.0, low=90.0, close=109.0))


def _ma(length: int, timeframe: str = "1m") -> SheetInstance:
    return SheetInstance("moving_averages", "default", {"length": length}, timeframe,
                         intrabar_capable=True)


def _levels(*triples) -> "tuple[MpPeriodLevel, ...]":
    return tuple(MpPeriodLevel(timeframe=tf, level=level, price=price)
                 for tf, level, price in triples)


def _sheet(levels, *, instances=(), series=None, extra_bars=None):
    request = ReachSheetRequest(dataset_ref=REF, instances=tuple(instances),
                                chart_timeframe="1m")
    bar_port = BarSpy({"1m": _fine_bars(), "1D": _period_bars(),
                       **(extra_bars or {})})
    series_port = SeriesSpy(series or {})
    unique = request.unique_instances()
    supply = BarSupply.load(request, bar_port=bar_port).extended(
        request, unique, bar_port=bar_port
    ).extended_bars(
        request, [level.timeframe for level in levels], bar_port=bar_port
    )
    return build_reach_sheet(
        request,
        series=SeriesSupply.load(request, unique, series_port=series_port),
        bars=supply,
        roles=Roles(),
        mp_period_levels=levels,
    )


def _mp_rows(sheet):
    return [row for row in sheet.rows if row.naming and
            row.naming.get("name") == MP_PERIOD_INDICATOR_ID]


class TestMerge:
    def test_each_borrowed_level_becomes_one_row_named_after_its_level(self) -> None:
        # Arrange
        levels = _levels(("1D", "POC*", 104.2), ("1D", "VAH", 107.0), ("1D", "VAL", 101.0))

        # Act
        sheet = _sheet(levels)

        # Assert
        got = {(row.timeframe, row.naming["level"], row.price) for row in _mp_rows(sheet)}
        assert got == {(lv.timeframe, lv.level, lv.price) for lv in levels}
        for row in _mp_rows(sheet):
            assert row.label == f"{MP_PERIOD_INDICATOR_ID} {row.naming['level']}"
            assert row.instance_key is None
            assert row.series is None

    def test_order_distance_gap_and_marks_come_from_the_single_ladder_rule(self) -> None:
        """並び・距離・差・次のターゲット印は build_ladder が唯一源（第 2 の規則を作らない）。"""
        # Arrange
        levels = _levels(("1D", "POC*", 104.2), ("1W", "VAH", 120.0), ("1M", "VAL", 80.0))

        # Act
        sheet = _sheet(levels, extra_bars={"1W": _period_bars(), "1M": _period_bars()})

        # Assert
        expected = build_ladder(
            [LevelInput(price=lv.price, timeframe=lv.timeframe,
                        label=f"{MP_PERIOD_INDICATOR_ID} {lv.level}") for lv in levels],
            current_price=float(_fine_bars()[-1].close),
        )
        assert [(r.label, r.timeframe, r.price, r.distance, r.gap_to_previous,
                 r.horizon_marks) for r in sheet.rows] == [
            (r.label, r.timeframe, r.price, r.distance, r.gap_to_previous,
             r.horizon_marks) for r in expected.rows
        ]
        assert sheet.current_index == expected.current_index

    def test_an_mp_row_can_take_the_next_target_mark_from_an_existing_row(self) -> None:
        """裁定 3: 印の候補に入る（既存行から印が移ることを認める）。"""
        # Arrange: 既存の 1D 行（MA・現在値の上 30）と、それより近い MP 1D 行（上 5）。
        ma = _ma(5, "1D")
        series = {ma.key: {"MA": tuple((DAY - 86_400 * k, 139.0) for k in (1, 0))}}
        current = float(_fine_bars()[-1].close)
        levels = _levels(("1D", "POC*", current + 5.0))

        # Act
        with_mp = _sheet(levels, instances=[ma], series=series)
        without = _sheet((), instances=[ma], series=series)

        # Assert
        def long_above(sheet):
            return [r.label for r in sheet.rows
                    if Horizon.LONG in r.horizon_marks and r.distance > 0]
        assert long_above(without) == [without.rows[0].label]
        assert long_above(with_mp) == [f"{MP_PERIOD_INDICATOR_ID} POC*"]

    def test_no_borrowed_levels_leaves_the_sheet_as_before(self) -> None:
        # Arrange
        ma = _ma(5)
        series = {ma.key: {"MA": tuple((bar.time, 104.0) for bar in _fine_bars())}}

        # Act
        sheet = _sheet((), instances=[ma], series=series)

        # Assert
        assert _mp_rows(sheet) == []
        assert [row.label for row in sheet.rows] == [
            Roles().row_label(instance=ma, series_name="MA")
        ]


class TestReachIsDefinitionD:
    @pytest.mark.parametrize("price", [104.2, 101.0, 150.0])
    def test_reach_is_the_period_first_touch_of_the_row_timeframe(self, price) -> None:
        # Arrange
        levels = _levels(("1D", "POC*", price))
        fine = _fine_bars()

        # Act
        row = _mp_rows(_sheet(levels))[0]

        # Assert: 同じ入力で唯一源を呼んだ結果と一致（期間の始端＝1D の最新バーの time）。
        assert row.reach == period_first_touch(
            [b.time for b in fine], [b.high for b in fine], [b.low for b in fine],
            level=price, period_start=_period_bars()[-1].time,
        )

    def test_a_timeframe_without_bars_has_no_reach(self) -> None:
        # Arrange: 1W の足が供給されない。
        levels = _levels(("1W", "VAL", 104.2))

        # Act
        row = _mp_rows(_sheet(levels))[0]

        # Assert: 期間の始端が決まらない＝到達は判定しない（発明しない）。
        assert row.reach.reached is None


# ------------------------------------------------------------ controller 経路
def _controller(series=None, bar_port=None) -> ReachSheetController:
    return ReachSheetController(
        series_port=series or SeriesSpy(),
        bar_port=bar_port or BarSpy({"1m": _fine_bars(), "1D": _period_bars()},
                                    forming=True),
        roles=Roles(),
        registry=Registry(set()),
        forward_port=ForwardSpy(),
        elapsed_gateway=ElapsedComparisonGateway(),
        is_intrabar_capable=lambda indicator_id, variant, params: True,
    )


def _body(mp_levels=None, **extra) -> dict:
    body = {"dataset_ref": REF, "chart_timeframe": "1m", "mode": "full",
            "instances": [], **extra}
    if mp_levels is not None:
        body["mp_levels"] = mp_levels
    return body


class TestControllerContract:
    def test_the_request_field_turns_into_ladder_rows(self) -> None:
        # Arrange
        entries = [{"timeframe": "1D", "level": "POC*", "price": 104.2},
                   {"timeframe": "1D", "level": "VAH", "price": 107.0}]

        # Act
        response = _controller().handle(_body(entries))

        # Assert
        assert response["ok"] is True
        got = {(row["timeframe"], row["naming"]["level"], row["price"])
               for row in response["rows"]}
        assert got == {(e["timeframe"], e["level"], e["price"]) for e in entries}
        for row in response["rows"]:
            assert row["reach"]["reached"] is not None   # 定義 D が当たっている

    def test_a_request_without_the_field_answers_as_before(self) -> None:
        # Act
        without = _controller().handle(_body())
        empty = _controller().handle(_body([]))

        # Assert
        assert without["ok"] is True
        assert without["rows"] == [] and empty["rows"] == []

    @pytest.mark.parametrize("entries", [
        "POC",
        [{"timeframe": "1D", "level": "POC*"}],
        [{"timeframe": "1D", "level": "POC*", "price": math.nan}],
        [{"timeframe": "1D", "level": "POC*", "price": "104"}],
        [{"timeframe": "2D", "level": "POC*", "price": 104.0}],
        [{"timeframe": "1D", "level": "", "price": 104.0}],
        [{"timeframe": "1D", "level": "VAH", "price": 104.0},
         {"timeframe": "1D", "level": "VAH", "price": 105.0}],
    ])
    def test_a_malformed_field_is_a_validation_failure(self, entries) -> None:
        # Act
        response = _controller().handle(_body(entries))

        # Assert
        assert response["ok"] is False
        assert response["error"]["type"] == "validation"

    def test_changed_levels_are_never_answered_as_unchanged(self) -> None:
        """状態トークンに mp_levels が入っていること（MP の値だけ動いても古い版面を残さない）。"""
        # Arrange
        controller = _controller()
        first = controller.handle(_body([{"timeframe": "1D", "level": "POC*", "price": 104.2}]))

        # Act
        same = controller.handle(_body(
            [{"timeframe": "1D", "level": "POC*", "price": 104.2}],
            known_state=first["state"]))
        moved = controller.handle(_body(
            [{"timeframe": "1D", "level": "POC*", "price": 105.2}],
            known_state=first["state"]))

        # Assert
        assert same.get("unchanged") is True
        assert moved.get("unchanged") is not True
        assert [row["price"] for row in moved["rows"]] == [105.2]
