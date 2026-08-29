"""`POST /reach_sheet` の JSON 契約（arch-spec §9）とサーバ側の単一ソース性を固定する。

契約の要点:
    - シリアライズは `dashboard_ui.usecase.sheet_models` のフィールド名を**そのまま**使う
      （別名を発明しない）。Enum は `.value`、`frozenset[Horizon]` はソート済みリスト、
      `Mapping[Horizon, float | None]` は `{"short":…, "mid/medium":…, "long":…}` 形。
    - フロントは数値の再計算をしない。`p` の算出・並び替え・到達判定はすべてサーバ側で終える。
    - 失敗は `{"ok": false, "error": {...}}`（例外を素通しして 500 にしない）。

計算量（§7・T-1）: 段 2（`mode="tick"`）で epoch が不変なら前進評価の発行は **0** である。
回数そのものは焼き込まない（固定するのは無駄の不在）。
"""
from __future__ import annotations

import json

from dashboard_ui.adapter.breakpoints import BreakpointRegistry
from dashboard_ui.adapter.controller.reach_sheet_controller import (
    ReachSheetController,
    SheetState,
)
from dashboard_ui.adapter.gateway.elapsed_comparison_gateway import (
    ElapsedComparisonGateway,
)
from dashboard_ui.adapter.series_role_table import SeriesRoleTable
from dashboard_ui.domain.bar import Bar

REF = "jp225_tick"
#: 2026-08-28 20:00:00 UTC。
START = 1_787_004_000
PRICE = 65_760.0


def bars(count: int, *, step: int, start: int = START) -> "tuple[Bar, ...]":
    return tuple(
        Bar(time=start + index * step, open=PRICE, high=PRICE + 20.0,
            low=PRICE - 20.0, close=PRICE + index * 0.5, volume=10.0 + index)
        for index in range(count)
    )


def points(count: int, value_of, *, step: int, start: int = START):
    return tuple((start + index * step, float(value_of(index))) for index in range(count))


class SeriesPortFake:
    """P-1 の代役（供給する系列は素材として与える）。"""

    def __init__(self, series_by_key) -> None:
        self._series = dict(series_by_key)
        self.issued: "list[tuple[str, str]]" = []

    def full_series(self, *, indicator_id, variant, params, dataset_ref, timeframe):
        self.issued.append((indicator_id, timeframe))
        return self._series.get((indicator_id, timeframe), {})


class BarPortFake:
    def __init__(self, bars_by_timeframe) -> None:
        self._bars = dict(bars_by_timeframe)

    def bars(self, *, dataset_ref, timeframe):
        return self._bars.get(timeframe, ())

    def forming_bar(self, *, dataset_ref, timeframe, now_unix):
        supplied = self._bars.get(timeframe) or ()
        return supplied[-1] if supplied else None


class ForwardSpy:
    """P-3 の Test Spy（前進評価はこの面からしか出ない）。"""

    def __init__(self) -> None:
        self.calls: "list[tuple[str, str, float]]" = []

    def value_at_close(self, *, indicator_id, variant, params, dataset_ref,
                       timeframe, close):
        self.calls.append((indicator_id, timeframe, close))
        return (2.0 * close + 300.0) / (close + 200.0)


def series_material():
    """1m の MA（水準）・ma_marod（オシレータ）・tickvol（積み上がる量）。"""
    return {
        ("moving_averages", "1m"): {
            "MA": points(60, lambda i: PRICE - 5.0 + i * 0.1, step=60),
        },
        # 値域は Test Spy の前進評価 `(2C + 300) / (C + 200)` が現在値付近で返す 2.0 前後に
        # 合わせる（投影した値が帯の内側に入る素材＝背景が塗られる条件）。
        ("ma_marod", "1m"): {
            "ma_marod": points(60, lambda i: 1.8 + i * 0.005, step=60),
            "ma_marod_q95": points(60, lambda i: 3.0, step=60),
        },
        ("tickvol", "1m"): {
            "tickvol": points(60, lambda i: 10.0 + (i % 7), step=60),
            "tickvol_q90": points(60, lambda i: 15.0, step=60),
        },
    }


def controller_of(forward: ForwardSpy, series: SeriesPortFake) -> ReachSheetController:
    bar_port = BarPortFake({"1m": bars(60, step=60)})
    return ReachSheetController(
        series_port=series,
        bar_port=bar_port,
        roles=SeriesRoleTable(),
        registry=BreakpointRegistry(),
        forward_port=forward,
        elapsed_gateway=ElapsedComparisonGateway(series_port=series),
        is_intrabar_capable=lambda indicator_id, variant, params: indicator_id != "cvfe",
    )


def body(mode: str = "full") -> dict:
    return {
        "dataset_ref": REF,
        "chart_timeframe": "1m",
        "mode": mode,
        "instances": [
            {"instance_id": "a", "indicator_id": "moving_averages", "variant": "default",
             "params": {"length": 24}},
            {"instance_id": "b", "indicator_id": "ma_marod", "variant": "default",
             "params": {"source": "hlc3", "length": 50, "q_high": 0.95}},
            {"instance_id": "c", "indicator_id": "tickvol", "variant": "default",
             "params": {"q_high": 0.90}},
        ],
    }


def handle(controller: ReachSheetController, request: dict) -> dict:
    return controller.handle(request)


# ------------------------------------------------------------------ 応答の形
def test_the_response_carries_the_contracted_top_level_keys() -> None:
    response = handle(controller_of(ForwardSpy(), SeriesPortFake(series_material())), body())

    assert response["ok"] is True
    assert set(response) == {"ok", "current_price", "rows", "current_index", "cells",
                             "degradations"}


def test_the_response_is_json_serialisable() -> None:
    """numpy スカラを漏らさない（漏らすと `json.dumps` が落ちる）。"""
    response = handle(controller_of(ForwardSpy(), SeriesPortFake(series_material())), body())

    assert json.loads(json.dumps(response))["ok"] is True


def test_the_rows_use_the_model_field_names() -> None:
    response = handle(controller_of(ForwardSpy(), SeriesPortFake(series_material())), body())

    row = response["rows"][0]

    assert set(row) == {"price", "timeframe", "label", "distance", "gap_to_previous",
                        "horizon_marks", "reach", "horizon_p", "instance_key"}
    assert set(row["reach"]) == {"reached", "since_time", "truncated"}


def test_the_rows_can_be_joined_to_the_degradations() -> None:
    """§7: 「その行がバー確定でしか動かないか」を行から辿れること（同じキーで突き合わせ）。"""
    series = SeriesPortFake(series_material())
    controller = ReachSheetController(
        series_port=series,
        bar_port=BarPortFake({"1m": bars(60, step=60)}),
        roles=SeriesRoleTable(),
        registry=BreakpointRegistry(),
        forward_port=ForwardSpy(),
        elapsed_gateway=ElapsedComparisonGateway(series_port=series),
        is_intrabar_capable=lambda indicator_id, variant, params: False,
    )

    response = handle(controller, body())

    degraded = {tuple(entry["instance_key"]) for entry in response["degradations"]}
    assert degraded
    assert all(tuple(row["instance_key"]) in degraded for row in response["rows"])


def test_the_horizon_marks_are_sorted_enum_values() -> None:
    response = handle(controller_of(ForwardSpy(), SeriesPortFake(series_material())), body())

    marks = [mark for row in response["rows"] for mark in row["horizon_marks"]]

    assert marks and set(marks) <= {"short", "medium", "long"}
    for row in response["rows"]:
        assert row["horizon_marks"] == sorted(
            row["horizon_marks"], key=["short", "medium", "long"].index
        )


def test_every_row_carries_all_three_horizons() -> None:
    """§5.5.5: 塗る単位は地平 3 段。候補が無い地平は `null`（0.5 で埋めない）。"""
    response = handle(controller_of(ForwardSpy(), SeriesPortFake(series_material())), body())

    for row in response["rows"]:
        assert set(row["horizon_p"]) == {"short", "medium", "long"}
        assert all(value is None or 0.0 <= value <= 1.0
                   for value in row["horizon_p"].values())


def test_the_short_horizon_is_painted_for_a_1m_instance() -> None:
    """1m の instance は短期に属する（背景が全 null にならないこと）。"""
    response = handle(controller_of(ForwardSpy(), SeriesPortFake(series_material())), body())

    painted = [row for row in response["rows"] if row["horizon_p"]["short"] is not None]

    assert painted


def test_the_cells_use_the_model_field_names() -> None:
    response = handle(controller_of(ForwardSpy(), SeriesPortFake(series_material())), body())

    cell = response["cells"][0]

    assert set(cell) == {"indicator_id", "timeframe", "value", "p", "tail_unscaled",
                         "reach", "unavailable_reason"}


def test_the_cumulative_cell_on_the_sub_unit_timeframe_says_why_it_has_no_level() -> None:
    """T-8: 1m はサブ単位の供給が無い。確定足の分布へ当てず、理由を出す。"""
    response = handle(controller_of(ForwardSpy(), SeriesPortFake(series_material())), body())

    tickvol = [cell for cell in response["cells"] if cell["indicator_id"] == "tickvol"][0]

    assert tickvol["p"] is None
    assert tickvol["unavailable_reason"]


def test_the_oscillator_cell_carries_a_quantile() -> None:
    response = handle(controller_of(ForwardSpy(), SeriesPortFake(series_material())), body())

    marod = [cell for cell in response["cells"] if cell["indicator_id"] == "ma_marod"][0]

    assert marod["p"] is not None
    assert marod["unavailable_reason"] is None


def test_the_degradations_name_the_instance_key() -> None:
    """§7: 足内更新できない instance は無言で落とさず表へ出す。"""
    series = SeriesPortFake(series_material())
    controller = ReachSheetController(
        series_port=series,
        bar_port=BarPortFake({"1m": bars(60, step=60)}),
        roles=SeriesRoleTable(),
        registry=BreakpointRegistry(),
        forward_port=ForwardSpy(),
        elapsed_gateway=ElapsedComparisonGateway(series_port=series),
        is_intrabar_capable=lambda indicator_id, variant, params: False,
    )

    response = handle(controller, body())

    assert response["degradations"]
    entry = response["degradations"][0]
    assert set(entry) == {"instance_key", "granularity", "reason"}
    assert len(entry["instance_key"]) == 4
    assert entry["granularity"] == "bar_close"


# -------------------------------------------------------------------- 計算量
def test_a_tick_update_with_an_unchanged_epoch_issues_no_forward_evaluation() -> None:
    """§7: バーが確定せず走行 H / L も変わらないティックでの発行は 0（無駄の不在）。"""
    forward = ForwardSpy()
    controller = controller_of(forward, SeriesPortFake(series_material()))
    handle(controller, body())
    issued_after_stage_one = len(forward.calls)

    handle(controller, body(mode="tick"))

    assert len(forward.calls) - issued_after_stage_one == 0
    assert issued_after_stage_one > 0


def test_a_full_rebuild_refits_the_coefficients() -> None:
    """段 1（バー確定）では当てはめ直す（検出力: 上の 0 が「常に 0」ではないこと）。"""
    forward = ForwardSpy()
    controller = controller_of(forward, SeriesPortFake(series_material()))
    handle(controller, body())
    first = len(forward.calls)

    handle(controller, body(mode="full"))

    assert len(forward.calls) > first


# ---------------------------------------------------------------------- 誤り
def test_a_missing_dataset_ref_is_reported_as_a_failure() -> None:
    request = body()
    del request["dataset_ref"]

    response = handle(controller_of(ForwardSpy(), SeriesPortFake(series_material())), request)

    assert response["ok"] is False
    assert response["error"]["type"] == "validation"
    assert "dataset_ref" in response["error"]["message"]


def test_an_unknown_mode_is_reported_as_a_failure() -> None:
    response = handle(
        controller_of(ForwardSpy(), SeriesPortFake(series_material())), body(mode="nope")
    )

    assert response["ok"] is False
    assert response["error"]["type"] == "validation"


def test_instances_must_be_a_list_of_objects() -> None:
    request = body()
    request["instances"] = "moving_averages"

    response = handle(controller_of(ForwardSpy(), SeriesPortFake(series_material())), request)

    assert response["ok"] is False
    assert response["error"]["type"] == "validation"


def test_a_supply_failure_is_reported_not_raised() -> None:
    """素材が足りないときも 500 にしない（理由の出る失敗にする）。"""
    controller = ReachSheetController(
        series_port=SeriesPortFake({}),
        bar_port=BarPortFake({}),
        roles=SeriesRoleTable(),
        registry=BreakpointRegistry(),
        forward_port=ForwardSpy(),
        elapsed_gateway=ElapsedComparisonGateway(series_port=SeriesPortFake({})),
        is_intrabar_capable=lambda indicator_id, variant, params: True,
    )

    response = handle(controller, body())

    assert response["ok"] is False
    assert response["error"]["type"] == "supply"


def test_two_requests_share_the_epoch_through_the_state() -> None:
    """要求ごとに口を組み直しても、当てはめの epoch は引き継がれる（§7 段 2）。

    サーバは素材の鮮度のために**要求ごとに新しい gateway**を組む（古い足を配らない）。
    それでも走行 H / L が変わっていないティックで係数を当て直してはならない。epoch と
    当てはめ結果は `SheetState` が持ち、controller の寿命に縛られない。
    """
    forward = ForwardSpy()
    state = SheetState()
    series = SeriesPortFake(series_material())

    def controller() -> ReachSheetController:
        return ReachSheetController(
            series_port=series,
            bar_port=BarPortFake({"1m": bars(60, step=60)}),
            roles=SeriesRoleTable(),
            registry=BreakpointRegistry(),
            forward_port=forward,
            elapsed_gateway=ElapsedComparisonGateway(series_port=series),
            is_intrabar_capable=lambda indicator_id, variant, params: True,
            state=state,
        )

    handle(controller(), body())
    issued_after_stage_one = len(forward.calls)
    handle(controller(), body(mode="tick"))

    assert issued_after_stage_one > 0
    assert len(forward.calls) - issued_after_stage_one == 0


def test_a_new_instance_gets_its_coefficients_even_on_a_tick_update() -> None:
    """束が変わったら、epoch が同じでも新しい instance の係数を用意する。

    epoch（バー時刻・走行 H / L）は「素材が変わったか」を答える量であって「何を表示するか」
    ではない。束の追加を epoch 不変で素通しすると、その instance だけ背景が塗られないまま
    次のバー確定まで残る（無言の欠落）。
    """
    forward = ForwardSpy()
    state = SheetState()
    series = SeriesPortFake(series_material())

    def controller() -> ReachSheetController:
        return ReachSheetController(
            series_port=series,
            bar_port=BarPortFake({"1m": bars(60, step=60)}),
            roles=SeriesRoleTable(),
            registry=BreakpointRegistry(),
            forward_port=forward,
            elapsed_gateway=ElapsedComparisonGateway(series_port=series),
            is_intrabar_capable=lambda indicator_id, variant, params: True,
            state=state,
        )

    first = body()
    first["instances"] = [entry for entry in first["instances"]
                          if entry["indicator_id"] != "ma_marod"]
    handle(controller(), first)
    issued_before = len(forward.calls)

    added = body(mode="tick")          # ma_marod を足した束をティック更新で送る
    response = handle(controller(), added)

    assert len(forward.calls) > issued_before
    assert any(row["horizon_p"]["short"] is not None for row in response["rows"])
