"""計算量 3（§7）: オーダーの表明 — ラダー行数・水準数を増やしても発行が増えない。

§7 の表明そのもの:
    ラダー行数 82 → 164 で発行回数が**変わらない**（2 点固定）。
    1 instance の水準 6 本 → 12 本で発行回数が**変わらない**。

「行として足す案」（§10）はこの表明を 1 つも満たせない（行数・水準数の両方に比例するため）。
"""
from __future__ import annotations

import numpy as np

from dashboard_ui.domain.bar import Bar
from dashboard_ui.domain.continuous_quantile import QuantileScale
from dashboard_ui.domain.price_ladder import LevelInput, build_ladder
from dashboard_ui.tests.complexity.conftest import ForwardSpy, Registry
from dashboard_ui.usecase.project_quantiles_to_price import (
    InstanceProjection,
    project_quantiles_to_price,
)
from dashboard_ui.usecase.sheet_models import LadderRow, SheetInstance
from dashboard_ui.usecase.update_reach_sheet import refresh_projection

_INSTANCE = SheetInstance("ma_marod", "default", {"length": 24}, "1m",
                          intrabar_capable=True)


def _ladder_rows(count: int) -> "tuple[LadderRow, ...]":
    ladder = build_ladder(
        [LevelInput(price=100.0 + index * 0.5, timeframe="1m", label=f"L{index}")
         for index in range(count)],
        current_price=100.0 + count * 0.25,
    )
    return tuple(
        LadderRow(price=row.price, timeframe=row.timeframe, label=row.label,
                  distance=row.distance, gap_to_previous=row.gap_to_previous,
                  horizon_marks=row.horizon_marks, reach=None)
        for row in ladder.rows
    )


def _projection(forward: ForwardSpy) -> InstanceProjection:
    cache = refresh_projection(
        None,
        forming_bar=Bar(time=1, open=100.0, high=110.0, low=90.0, close=100.0),
        instances=(_INSTANCE,), dataset_ref="x", forward_port=forward,
        registry=Registry({"ma_marod"}))
    return InstanceProjection(
        timeframe="1m",
        value_map=cache.maps[_INSTANCE.key],
        scale=QuantileScale(window_values=np.array([1.0, 1.2, 1.4]),
                            band_high=1e9, q_high=0.9, tail=None),
    )


def test_doubling_the_ladder_rows_does_not_issue_more_forward_evaluations() -> None:
    issued = {}
    for row_count in (82, 164):
        forward = ForwardSpy()
        projection = _projection(forward)
        spent_on_fit = len(forward.calls)

        project_quantiles_to_price(_ladder_rows(row_count), projections=[projection])

        issued[row_count] = len(forward.calls) - spent_on_fit

    assert issued[82] == issued[164] == 0


def test_doubling_the_levels_of_one_instance_does_not_issue_more() -> None:
    """1 instance の水準 6 本 → 12 本（水準は行になるだけで、係数決定には効かない）。"""
    issued = {}
    for level_count in (6, 12):
        forward = ForwardSpy()
        projection = _projection(forward)
        spent_on_fit = len(forward.calls)

        project_quantiles_to_price(_ladder_rows(level_count), projections=[projection])

        issued[level_count] = len(forward.calls) - spent_on_fit

    assert issued[6] == issued[12] == 0


def test_the_fit_cost_tracks_the_pieces_not_the_rows() -> None:
    """当てはめ費用は区分数だけで決まる（行数・水準数のどちらにも依存しない）。"""
    forward = ForwardSpy()
    cache = refresh_projection(
        None, forming_bar=Bar(time=1, open=100.0, high=110.0, low=90.0, close=100.0),
        instances=(_INSTANCE,), dataset_ref="x", forward_port=forward,
        registry=Registry({"ma_marod"}))

    assert len(forward.calls) == 3 * len(cache.maps[_INSTANCE.key].pieces)
