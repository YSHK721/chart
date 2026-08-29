"""計算量 7（§5.3 / §5.5.5）: 背景色の目盛りは第 2 表のセルと同じ当てはめを共有する。

第 2 表のセル（§5.2 / §5.3）と価格セルの背景（§5.5.5）は、**同じ instance の同じ観測**から
出る量である。突き合わせと因果境界（当該バーを観測に含めない）を 2 か所に手書きすると、
片方だけを直したときに 2 つの窓が食い違い、同じ epoch でも毎回 GPD を当てはめ直す
（当てはめキャッシュの署名が交互に上書きされる）。**出力はどちらも「それらしい色」のまま**
なので、状態検証では原理的に落ちない（ISSUE-450 と同型の「作ってから捨てる」）。

固定するのは**無駄の不在**であって回数ではない:
    - 素材が動いていない要求を繰り返しても、当てはめの追加は 0。
    - オーダーの表明（2 点固定）: ラダー行が増えても当てはめは増えない。
"""
from __future__ import annotations

import pytest

from dashboard_ui.adapter.controller.reach_sheet_controller import (
    ReachSheetController,
    SheetState,
)
from dashboard_ui.domain import continuous_quantile as _cq
from dashboard_ui.tests.complexity.conftest import (
    BarSpy,
    ForwardSpy,
    Registry,
    Roles,
    SeriesSpy,
    bars,
    ma_instance,
    points,
    rsi_spec,
)
from dashboard_ui.usecase.sheet_models import SheetInstance

REF = "jp225_tick"
_OSC = SheetInstance("profit_rsi", "default", {}, "1m", intrabar_capable=True)

#: 帯超が繰り返す列（エピソードが閉じる＝確定観測が増える形）。
_VALUES = [10.0] * 3 + [95.0, 10.0] * 20


class FitSpy:
    """当てはめの Test Spy（GPD の当てはめはこの面からしか起きない）。"""

    def __init__(self, monkeypatch) -> None:
        self.calls = 0
        original = _cq.fit_tail

        def counted(events, *, k_events):
            self.calls += 1
            return original(events, k_events=k_events)

        monkeypatch.setattr(_cq, "fit_tail", counted)


class NoElapsed:
    """積み上がる量を持たない束の比較集合（空）。"""

    def comparisons(self, *, dataset_ref, entries, now_unix):
        return {}


@pytest.fixture
def fit_spy(monkeypatch) -> FitSpy:
    return FitSpy(monkeypatch)


def _controller(spy: SeriesSpy, state: SheetState) -> ReachSheetController:
    bar_spy = BarSpy({"1m": bars([100.0] * len(_VALUES))}, forming=True)
    return ReachSheetController(
        series_port=spy,
        bar_port=bar_spy,
        roles=Roles({"profit_rsi": rsi_spec()}),
        registry=Registry({"profit_rsi"}),
        forward_port=ForwardSpy(),
        elapsed_gateway=NoElapsed(),
        is_intrabar_capable=lambda indicator_id, variant, params: True,
        state=state,
    )


def _material(row_count: int) -> "tuple[SeriesSpy, list[SheetInstance]]":
    spy = SeriesSpy()
    spy.add(_OSC, {"rsi": points(_VALUES), "rsi_q90": points([90.0] * len(_VALUES))})
    rows = [ma_instance(index) for index in range(1, row_count + 1)]
    for index, instance in enumerate(rows):
        spy.add(instance, {"MA": points([100.0 + index] * len(_VALUES))})
    return spy, rows


def _body(instances: "list[SheetInstance]", mode: str = "full") -> dict:
    return {
        "dataset_ref": REF,
        "chart_timeframe": "1m",
        "mode": mode,
        "instances": [
            {
                "indicator_id": instance.indicator_id,
                "variant": instance.variant,
                "params": dict(instance.params),
            }
            for instance in instances
        ],
    }


def test_the_sheet_is_produced_with_a_background(fit_spy: FitSpy) -> None:
    """検出力の自己検査: 背景と第 2 表のセルが実際に出ていること（空なら以下は無意味）。"""
    spy, rows = _material(3)
    state = SheetState()

    response = _controller(spy, state).handle(_body([_OSC, *rows]))

    assert response["ok"] is True
    assert response["cells"] != []
    assert any(
        value is not None
        for row in response["rows"]
        for value in row["horizon_p"].values()
    )


def test_repeating_a_tick_request_adds_no_fit(fit_spy: FitSpy) -> None:
    """素材が動いていない要求を繰り返しても当てはめは増えない（2 つの窓が同じ証拠）。

    セルと背景が別々の窓を組んでいると、当てはめキャッシュの署名が要求ごとに交互へ
    上書きされ、繰り返すたびに当てはめが増える。
    """
    spy, rows = _material(3)
    state = SheetState()
    controller = _controller(spy, state)
    controller.handle(_body([_OSC, *rows]))
    after_first = fit_spy.calls

    for _ in range(5):
        controller.handle(_body([_OSC, *rows], mode="tick"))

    assert fit_spy.calls - after_first == 0
    assert after_first > 0


def test_the_number_of_ladder_rows_does_not_change_the_fit_count(
    fit_spy: FitSpy,
) -> None:
    """オーダーの表明（2 点固定）: 背景を塗る行が増えても当てはめは増えない。"""
    counts = {}
    for row_count in (3, 9):
        spy, rows = _material(row_count)
        fit_spy.calls = 0

        _controller(spy, SheetState()).handle(_body([_OSC, *rows]))
        counts[row_count] = fit_spy.calls

    assert counts[3] == counts[9]
