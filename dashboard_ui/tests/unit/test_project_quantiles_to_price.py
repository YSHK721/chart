"""UC-03（§5.5.5）: ラダー各行の背景を**地平 3 段**で 3 分割する。

依頼者裁定（2026-08-29）と実測:
  - 1 色（全 instance のうち最も 0.5 から離れたもの）は **82/82 行が両端に潰れる**ため採らない。
    短期の instance が常に最も 0.5 から離れており、`max` がそれを全行へ伝播させる。
  - 3 分割では 3 地平の `p` が 0.1 超ずれる行が 35/82（43%）ある。1 色にするとこの情報が消える。
  - 各地平のセルには、その地平に属する instance のうち **`p` が 0.5 から最も離れたもの**を採る。
  - `p` が定義できない instance はその地平の候補から外す。候補が 1 つも残らないときは**空**に
    し、色を置かない（**無言で 0.5 を埋めない**）。
"""
from __future__ import annotations

import numpy as np
import pytest

from dashboard_ui.domain.continuous_quantile import QuantileScale
from dashboard_ui.domain.horizon import Horizon
from dashboard_ui.domain.price_ladder import LevelInput, build_ladder
from dashboard_ui.domain.price_value_map import PriceValueMap
from dashboard_ui.usecase.project_quantiles_to_price import (
    InstanceProjection,
    project_quantiles_to_price,
)


class _ForwardSpy:
    def __init__(self, function) -> None:
        self._function = function
        self.calls = 0

    def __call__(self, price: float) -> float:
        self.calls += 1
        return self._function(price)


def _identity_map(spy: "_ForwardSpy | None" = None) -> PriceValueMap:
    """`v(C) = C` を返すメビウス（a=1, b=0, d=0）。価格をそのまま指標値として扱える。"""
    forward = spy if spy is not None else (lambda price: price)
    return PriceValueMap.fit(forward, [90.0, 110.0], span=20.0)


def _scale(window: list[float], *, band_high: float = 1e9) -> QuantileScale:
    return QuantileScale(window_values=np.asarray(window, dtype=np.float64),
                         band_high=band_high, q_high=0.9, tail=None)


def _ladder():
    return build_ladder(
        [LevelInput(price=105.0, timeframe="1m", label="up"),
         LevelInput(price=95.0, timeframe="1m", label="down")],
        current_price=100.0,
    )


class TestProjection:
    def test_every_row_gets_one_value_per_horizon(self) -> None:
        projections = [
            InstanceProjection(timeframe="1m", value_map=_identity_map(),
                               scale=_scale([90.0, 100.0, 110.0])),
            InstanceProjection(timeframe="1D", value_map=_identity_map(),
                               scale=_scale([90.0, 100.0, 110.0])),
        ]

        background = project_quantiles_to_price(_ladder().rows, projections=projections)

        assert len(background) == 2
        assert set(background[0]) == {Horizon.SHORT, Horizon.MEDIUM, Horizon.LONG}

    def test_the_candidate_furthest_from_a_half_is_taken(self) -> None:
        """`max(p)` ではなく `max |p - 0.5|`（沈静側の極端も拾う）。"""
        hot = InstanceProjection(timeframe="1D", value_map=_identity_map(),
                                 scale=_scale([1.0, 2.0, 3.0, 4.0]))       # p = 1.0
        cold = InstanceProjection(timeframe="1D", value_map=_identity_map(),
                                  scale=_scale([200.0, 300.0, 400.0]))     # p = 0.0
        middling = InstanceProjection(timeframe="1D", value_map=_identity_map(),
                                      scale=_scale([100.0, 200.0]))        # p = 0.5

        with_cold = project_quantiles_to_price(
            _ladder().rows, projections=[middling, cold])

        assert with_cold[0][Horizon.LONG] == pytest.approx(0.0)
        assert project_quantiles_to_price(
            _ladder().rows, projections=[middling, hot])[0][Horizon.LONG] == 1.0

    def test_a_short_only_instance_does_not_reach_the_longer_horizons(self) -> None:
        """§5.5.5 の 3 分割の意味: 短期の極端を長期のセルへ伝播させない。"""
        projections = [
            InstanceProjection(timeframe="1m", value_map=_identity_map(),
                               scale=_scale([1.0, 2.0, 3.0])),
        ]

        background = project_quantiles_to_price(_ladder().rows, projections=projections)

        assert background[0][Horizon.SHORT] == pytest.approx(1.0)
        assert background[0][Horizon.MEDIUM] is None
        assert background[0][Horizon.LONG] is None

    def test_the_three_horizons_can_disagree_on_the_same_row(self) -> None:
        projections = [
            InstanceProjection(timeframe="1m", value_map=_identity_map(),
                               scale=_scale([1.0, 2.0, 3.0])),          # p = 1.0
            InstanceProjection(timeframe="1D", value_map=_identity_map(),
                               scale=_scale([200.0, 300.0])),           # p = 0.0
        ]

        background = project_quantiles_to_price(_ladder().rows, projections=projections)

        assert background[0][Horizon.SHORT] == pytest.approx(1.0)
        assert background[0][Horizon.LONG] == pytest.approx(0.0)

    def test_rows_are_projected_at_their_own_price(self) -> None:
        projections = [
            InstanceProjection(timeframe="1m", value_map=_identity_map(),
                               scale=_scale([96.0, 100.0, 104.0])),
        ]

        background = project_quantiles_to_price(_ladder().rows, projections=projections)

        assert background[0][Horizon.SHORT] == pytest.approx(1.0)   # 価格 105 は窓の全上
        assert background[1][Horizon.SHORT] == pytest.approx(0.0)   # 価格 95 は窓の全下


class TestUnavailable:
    def test_an_instance_without_a_defined_p_is_dropped_from_the_horizon(self) -> None:
        """§5.5.5: `p` が定義できないセルはその地平の候補から外す。"""
        unscaled = InstanceProjection(
            timeframe="1D", value_map=_identity_map(),
            scale=QuantileScale(window_values=np.array([1.0, 2.0, 3.0]),
                                band_high=10.0, q_high=0.9, tail=None))
        good = InstanceProjection(timeframe="1D", value_map=_identity_map(),
                                  scale=_scale([100.0, 200.0]))

        background = project_quantiles_to_price(
            _ladder().rows, projections=[unscaled, good])

        assert background[0][Horizon.LONG] == pytest.approx(0.5)

    def test_a_horizon_with_no_candidate_is_left_empty(self) -> None:
        """無言で 0.5 を埋めない。"""
        background = project_quantiles_to_price(_ladder().rows, projections=[])

        assert background[0] == {Horizon.SHORT: None, Horizon.MEDIUM: None,
                                 Horizon.LONG: None}

    def test_an_empty_ladder_yields_no_background(self) -> None:
        empty = build_ladder([], current_price=100.0)

        assert project_quantiles_to_price(empty.rows, projections=[]) == ()


class TestComplexityContract:
    def test_projecting_rows_issues_no_forward_evaluation(self) -> None:
        """§5.5.4: 係数決定の後は閉形式のみ。行を評価しても前進評価は 0 回。"""
        spy = _ForwardSpy(lambda price: price)
        projections = [InstanceProjection(timeframe="1m", value_map=_identity_map(spy),
                                          scale=_scale([90.0, 100.0, 110.0]))]
        after_fit = spy.calls

        project_quantiles_to_price(_ladder().rows, projections=projections)

        assert spy.calls == after_fit
