"""§5.3 連続量 `p`（帯内＝経験順位／帯外＝GPD）の唯一定義を固定する。

    帯内 : p = 経験順位（当該バー除外の因果窓）                       ∈ [0, q_high]
    帯外 : p = q_high + (1 - q_high) * F_GPD(v - u ; xi, beta)        ∈ (q_high, 1]

参照実装: `tools/measure/issue449/probe_tailscale.py`（common の GPD 実装を無改変で使用・
エピソード極値へ畳んだ超過分の直近 k_events 件へ当てはめる）。
観測が `MIN_GPD_EVENTS` 未満のセルは帯外を解像できない＝**目盛りが無い**ことを
`tail_unscaled` で明示する（§5.3.2。濃淡でごまかさない）。
"""
from __future__ import annotations

import numpy as np
import pytest

from dashboard_ui.domain.continuous_quantile import (
    MIN_GPD_EVENTS,
    QuantileReading,
    QuantileScale,
    excess_event_history,
    fit_tail,
    in_band_ranks,
    p_at,
)


def _exponential_events(count: int, *, scale: float = 1.0) -> list[float]:
    """決定的な超過分の観測列（GPD の当てはめが成立する形）。"""
    rng = np.random.default_rng(4490829)
    return [float(x) for x in rng.exponential(scale, size=count)]


class TestInBandRanks:
    def test_the_rank_is_the_share_of_window_values_below_the_current_one(self) -> None:
        values = np.array([1.0, 2.0, 3.0, 4.0, 2.5])

        ranks = in_band_ranks(values, window_n=10)

        assert ranks[4] == pytest.approx(2 / 4)

    def test_the_window_excludes_the_current_bar(self) -> None:
        """§5.3 の窓は `values[max(0, t-window_n): t]`（common.marod_bands と同一規約）。"""
        values = np.array([1.0, 2.0, 3.0])

        ranks = in_band_ranks(values, window_n=10)

        assert ranks[2] == pytest.approx(1.0)   # 窓 [1,2] は両方 3 未満

    def test_bars_without_enough_window_are_nan(self) -> None:
        ranks = in_band_ranks(np.array([1.0, 2.0, 3.0]), window_n=10)

        assert np.isnan(ranks[0]) and np.isnan(ranks[1])

    def test_an_empty_series_yields_an_empty_result(self) -> None:
        assert in_band_ranks(np.array([]), window_n=10).shape == (0,)


class TestFitTail:
    def test_fewer_events_than_the_minimum_yields_no_fit(self) -> None:
        """§5.3.2 境界値: MIN_GPD_EVENTS(30) 未満では当てはめない。"""
        assert fit_tail(_exponential_events(MIN_GPD_EVENTS - 1), k_events=50) is None

    def test_exactly_the_minimum_number_of_events_yields_a_fit(self) -> None:
        fit = fit_tail(_exponential_events(MIN_GPD_EVENTS), k_events=50)

        assert fit is not None
        assert np.isfinite(fit.xi) and fit.beta > 0.0

    def test_only_the_most_recent_k_events_are_used(self) -> None:
        events = _exponential_events(80)

        # di-ok(C3): 同一関数の 2 入力の関係（直近 40 件だけが効く）を見る変形テスト
        assert fit_tail(events, k_events=40) == fit_tail(events[-40:], k_events=40)

    def test_non_finite_events_are_dropped_before_counting(self) -> None:
        events = _exponential_events(MIN_GPD_EVENTS - 1) + [float("nan")] * 5

        assert fit_tail(events, k_events=50) is None

    def test_an_empty_event_history_yields_no_fit(self) -> None:
        assert fit_tail([], k_events=50) is None


class TestPAt:
    def test_a_value_inside_the_band_uses_the_empirical_rank(self) -> None:
        reading = p_at(value=5.0, band_high=10.0, q_high=0.95,
                       in_band_rank=0.42, tail=None)

        assert reading == QuantileReading(p=pytest.approx(0.42), tail_unscaled=False)

    def test_a_value_exactly_at_the_band_edge_is_inside_the_band(self) -> None:
        """境界値 v = u: 帯外は `v > u`（参照実装 probe_tailscale.py:116 と同一）。"""
        reading = p_at(value=10.0, band_high=10.0, q_high=0.95,
                       in_band_rank=0.94, tail=None)

        assert reading.p == pytest.approx(0.94)
        assert reading.tail_unscaled is False

    def test_a_value_outside_the_band_is_resolved_by_the_tail_fit(self) -> None:
        fit = fit_tail(_exponential_events(60), k_events=50)

        reading = p_at(value=12.0, band_high=10.0, q_high=0.95,
                       in_band_rank=1.0, tail=fit)

        assert 0.95 < reading.p <= 1.0
        assert reading.tail_unscaled is False

    def test_the_scale_joins_continuously_at_the_band_edge(self) -> None:
        """§5.3.1 実測「接合の跳び 0.0000〜0.0031」＝連続。F_GPD(0)=0 なので p→q_high。"""
        fit = fit_tail(_exponential_events(60), k_events=50)

        just_outside = p_at(value=10.0 + 1e-9, band_high=10.0, q_high=0.95,
                            in_band_rank=1.0, tail=fit)

        assert just_outside.p == pytest.approx(0.95, abs=1e-6)

    def test_the_scale_is_monotonic_in_the_value_across_the_join(self) -> None:
        """§5.3.1 単調性: 帯内は経験順位（非減少）・帯外は F_GPD（増加）。"""
        fit = fit_tail(_exponential_events(60), k_events=50)
        outside = [
            p_at(value=10.0 + step, band_high=10.0, q_high=0.95,
                 in_band_rank=1.0, tail=fit).p
            for step in (0.1, 0.5, 1.0, 3.0, 8.0)
        ]

        assert outside == sorted(outside)
        assert all(0.95 < value <= 1.0 for value in outside)

    def test_without_a_tail_fit_the_outside_of_the_band_has_no_scale(self) -> None:
        """§5.3.2 の 7 セル。p を無言で 1.0 や 0.5 で埋めない。"""
        reading = p_at(value=12.0, band_high=10.0, q_high=0.95,
                       in_band_rank=1.0, tail=None)

        assert reading.p is None
        assert reading.tail_unscaled is True

    def test_a_value_beyond_a_finite_upper_endpoint_saturates_at_one(self) -> None:
        """xi<0 の GPD は有限終端を持つ。終端以上では分布関数の定義どおり 1（NaN にしない）。"""
        fit = fit_tail(_exponential_events(60), k_events=50)
        negative_xi = type(fit)(xi=-0.5, beta=1.0, n_events=fit.n_events)

        reading = p_at(value=10.0 + 2.0, band_high=10.0, q_high=0.9,
                       in_band_rank=1.0, tail=negative_xi)

        assert reading.p == pytest.approx(1.0)

    def test_an_unavailable_in_band_rank_yields_no_p(self) -> None:
        """窓不足のバーは帯内でも p を出せない（§5.5.5 でその地平の候補から外れる）。"""
        reading = p_at(value=5.0, band_high=10.0, q_high=0.95,
                       in_band_rank=float("nan"), tail=None)

        assert reading.p is None
        assert reading.tail_unscaled is False

    def test_a_missing_band_keeps_the_reading_in_band(self) -> None:
        """帯が NaN（warm-up）なら帯外と判定できない。順位だけを返す。"""
        reading = p_at(value=5.0, band_high=float("nan"), q_high=0.95,
                       in_band_rank=0.3, tail=None)

        assert reading.p == pytest.approx(0.3)

    def test_a_custom_excess_definition_is_honoured(self) -> None:
        """RSI の超過分は `(v-u)/(100-u)`（levels.py ③）。指標名で分岐しない（OCP）。"""
        fit = fit_tail(_exponential_events(60), k_events=50)

        default = p_at(value=95.0, band_high=90.0, q_high=0.9, in_band_rank=1.0, tail=fit)
        scaled = p_at(value=95.0, band_high=90.0, q_high=0.9, in_band_rank=1.0, tail=fit,
                      excess=lambda value, edge: (value - edge) / (100.0 - edge))

        assert scaled.p != pytest.approx(default.p)
        assert 0.9 < scaled.p <= 1.0

    @pytest.mark.parametrize("q_high", [0.0, 1.0, -0.1, 1.5])
    def test_an_invalid_q_high_is_rejected(self, q_high: float) -> None:
        with pytest.raises(ValueError):
            p_at(value=5.0, band_high=10.0, q_high=q_high, in_band_rank=0.4, tail=None)


class TestExcessEventHistory:
    def test_consecutive_over_band_bars_fold_into_one_episode_extreme(self) -> None:
        """既存規約（common.event_quantiles.step_events の episode 集計）と同一。"""
        values = np.array([1.0, 12.0, 15.0, 13.0, 1.0, 11.0, 0.5])
        bands = np.array([10.0] * 7)

        events = excess_event_history(values, bands)

        assert events == [pytest.approx(5.0), pytest.approx(1.0)]

    def test_an_unclosed_episode_is_not_an_event_yet(self) -> None:
        """エピソードが閉じるまで観測にしない（＝当てはめを増やさない・§7）。"""
        values = np.array([1.0, 12.0, 15.0])
        bands = np.array([10.0] * 3)

        assert excess_event_history(values, bands) == []

    def test_bars_without_a_band_are_skipped(self) -> None:
        values = np.array([1.0, 12.0, 1.0])
        bands = np.array([10.0, float("nan"), 10.0])

        assert excess_event_history(values, bands) == []

    def test_mismatched_lengths_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            excess_event_history(np.array([1.0, 2.0]), np.array([1.0]))


class TestQuantileScale:
    """§5.5 の「この価格で引けたら `p` はどこになるか」を答える目盛り。

    `p_at` は「当該バーの `p`」を求めるのに対し、`QuantileScale` は**仮定の指標値**に対して
    同じ目盛りを当てる。式は同じものを使う（第 2 定義を作らない）。
    """

    @staticmethod
    def _scale(**over) -> QuantileScale:
        base = dict(
            window_values=np.array([10.0, 20.0, 30.0, 40.0]),
            band_high=90.0,
            q_high=0.9,
            tail=None,
        )
        base.update(over)
        return QuantileScale(**base)

    def test_a_hypothetical_value_is_ranked_in_the_same_window(self) -> None:
        assert self._scale().p_of(25.0).p == pytest.approx(2 / 4)

    def test_the_scale_is_the_same_formula_as_the_current_bar_reading(self) -> None:
        values = np.array([10.0, 20.0, 30.0, 40.0, 25.0])
        expected = in_band_ranks(values, window_n=10)[4]

        assert self._scale().p_of(25.0).p == pytest.approx(expected)

    def test_a_hypothetical_value_outside_the_band_uses_the_tail(self) -> None:
        scale = self._scale(tail=fit_tail(_exponential_events(60), k_events=50))

        reading = scale.p_of(95.0)

        assert 0.9 < reading.p <= 1.0

    def test_a_hypothetical_value_outside_the_band_without_a_fit_has_no_p(self) -> None:
        reading = self._scale().p_of(95.0)

        assert reading.p is None
        assert reading.tail_unscaled is True

    def test_a_window_shorter_than_the_minimum_yields_no_p(self) -> None:
        reading = self._scale(window_values=np.array([10.0])).p_of(25.0)

        assert reading.p is None

    def test_non_finite_window_values_are_dropped(self) -> None:
        scale = self._scale(window_values=np.array([10.0, np.nan, 30.0, 40.0]))

        assert scale.p_of(25.0).p == pytest.approx(1 / 3)

    def test_a_non_finite_hypothetical_value_yields_no_p(self) -> None:
        assert self._scale().p_of(float("nan")).p is None
