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
    MIN_STAT_OBS,
    QuantileReading,
    QuantileScale,
    excess_event_fold,
    excess_event_history,
    fit_tail,
    in_band_rank_latest,
    in_band_ranks,
    p_at,
    trailing_ranks,
    trailing_readings,
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


def test_the_minimum_observation_count_has_no_second_definition() -> None:
    """§5.3 の下限は因果窓の規約と同じ量（`MIN_GPD_EVENTS` の再公開と同形・🟡-5）。"""
    from common import marod_bands

    assert MIN_STAT_OBS == marod_bands.MIN_STAT_OBS


class TestInBandRankLatest:
    """末尾 1 点入口（レビュー 🔴-1）。系列版と**同一の定義**であることを固定する。"""

    def test_it_equals_the_last_element_of_the_series_version(self) -> None:
        """定義の同一性（第 2 定義を作っていないことの機械的保証）。

        決定的データ（乱数を使わない）: 周期の異なる 2 つの波の和＋NaN 混在。
        """
        index = np.arange(300, dtype=np.float64)
        values = np.sin(index * 0.7) + 0.5 * np.cos(index * 0.13)
        values[::17] = np.nan
        cases = [(window_n, length)
                 for window_n in (2, 10, 500) for length in (1, 2, 3, 40, 300)]

        expected = [in_band_ranks(values[:length], window_n=window_n)[-1]
                    for window_n, length in cases]
        actual = [in_band_rank_latest(values[:length], window_n=window_n)
                  for window_n, length in cases]

        np.testing.assert_allclose(actual, expected, equal_nan=True)

    def test_a_window_shorter_than_the_minimum_is_nan(self) -> None:
        """境界値: 有限窓 1 本（< MIN_STAT_OBS）は NaN。"""
        assert np.isnan(in_band_rank_latest(np.array([1.0, 2.0]), window_n=10))

    def test_a_non_finite_current_value_is_nan(self) -> None:
        assert np.isnan(in_band_rank_latest(np.array([1.0, 2.0, 3.0, np.nan]),
                                            window_n=10))

    def test_an_empty_series_is_nan(self) -> None:
        assert np.isnan(in_band_rank_latest(np.array([]), window_n=10))


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


class TestExcessEventFold:
    """§5.2 背景ストリップの因果窓: `counts[i]` はバー i 直前までに確定した件数。"""

    def test_the_counts_are_the_prefix_lengths_of_the_same_fold(self) -> None:
        """`events[:counts[i]]` ＝「バー 0..i-1 だけを畳んだ観測列」（畳み込みは前方逐次）。

        この一致が崩れると、直近区間の当てはめ窓が当該バーより後に確定した観測を含む
        （因果境界の破れ）か、確定済みの観測を取りこぼす。
        """
        values = np.array([1.0, 12.0, 15.0, 1.0, 11.0, 0.5, 13.0, 1.0])
        bands = np.array([10.0] * 8)

        events, counts = excess_event_fold(values, bands)

        assert len(counts) == values.size
        for index in range(values.size):
            assert events[: counts[index]] == excess_event_history(
                values[:index], bands[:index]
            )

    def test_the_events_equal_the_history_version(self) -> None:
        """`excess_event_history` は本畳み込みへの委譲（第 2 定義を作らない）。"""
        values = np.array([1.0, 12.0, 15.0, 13.0, 1.0, 11.0, 0.5])
        bands = np.array([10.0] * 7)

        events, _counts = excess_event_fold(values, bands)

        assert events == excess_event_history(values, bands)

    def test_skipped_bars_still_carry_a_count(self) -> None:
        """帯の無いバーも `counts` の位置は持つ（系列と同一長＝添字で引ける）。"""
        values = np.array([1.0, 12.0, 1.0])
        bands = np.array([10.0, float("nan"), 10.0])

        _events, counts = excess_event_fold(values, bands)

        assert len(counts) == 3


class TestTrailingReadings:
    """§5.2 背景ストリップ: 直近区間の読みは当該バーのセルと同じ式・同じ因果境界。"""

    def test_each_reading_equals_the_single_point_definition(self) -> None:
        """バー i の読み ＝「系列を i で打ち切ったときの末尾 1 点の読み」。

        この同値が崩れると、ストリップの色とセルの色が**別々の定義**で決まる
        （出力はどちらもそれらしい色のままなので状態検証では落ちない）。
        """
        rng = np.random.default_rng(20260904)
        values = rng.uniform(0.0, 100.0, size=120)
        bands = np.full(120, 70.0)
        events, counts = excess_event_fold(values, bands)

        readings = trailing_readings(
            values, bands, window_n=30, q_high=0.9,
            events=events, event_counts=counts, k_events=50, n_bars=9,
        )

        assert len(readings) == 9
        for offset, reading in enumerate(readings):
            index = 120 - 9 + offset
            expected = p_at(
                value=float(values[index]),
                band_high=float(bands[index]),
                q_high=0.9,
                in_band_rank=in_band_rank_latest(values[: index + 1], 30),
                tail=fit_tail(
                    excess_event_history(values[:index], bands[:index]), k_events=50
                ),
            )
            assert reading == expected

    def test_a_short_series_yields_fewer_readings_not_an_error(self) -> None:
        values = np.array([10.0, 20.0, 30.0])
        bands = np.array([90.0] * 3)
        events, counts = excess_event_fold(values, bands)

        readings = trailing_readings(
            values, bands, window_n=30, q_high=0.9,
            events=events, event_counts=counts, k_events=50, n_bars=9,
        )

        assert len(readings) == 3

    def test_an_in_band_bar_issues_no_tail_fit(self) -> None:
        """帯内のバーへ当てはめを発行しない（作ってから捨てない・絶対命令 §4.1）。"""
        values = np.array([10.0, 20.0, 30.0, 25.0])
        bands = np.array([90.0] * 4)
        events, counts = excess_event_fold(values, bands)
        issued: "list[int]" = []

        trailing_readings(
            values, bands, window_n=30, q_high=0.9,
            events=events, event_counts=counts, k_events=50, n_bars=9,
            fit=lambda observed: issued.append(len(observed)),
        )

        assert issued == []

    def test_an_out_of_band_bar_without_events_is_tail_unscaled(self) -> None:
        """§5.3.2: 目盛りの無い帯外区間は `p` を発明しない。"""
        values = np.array([10.0, 20.0, 95.0, 25.0])
        bands = np.array([90.0] * 4)
        events, counts = excess_event_fold(values, bands)

        readings = trailing_readings(
            values, bands, window_n=30, q_high=0.9,
            events=events, event_counts=counts, k_events=50, n_bars=9,
        )

        assert readings[2].p is None
        assert readings[2].tail_unscaled is True

    def test_mismatched_counts_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            trailing_readings(
                np.array([1.0, 2.0]), np.array([10.0, 10.0]),
                window_n=30, q_high=0.9, events=[], event_counts=(0,),
                k_events=50, n_bars=9,
            )


class TestTrailingRanks:
    """§5.3.3 の積み上がる量: 確定バーの全量は経過 100% の部分和＝因果窓の経験順位でよい。"""

    def test_each_rank_uses_only_the_preceding_bars(self) -> None:
        values = np.array([100.0, 200.0, 300.0, 150.0])

        readings = trailing_ranks(values, window_n=30, n_bars=9)

        assert [reading.p for reading in readings] == [
            None,                       # 窓が空
            None,                       # 有限観測 1 本 < MIN_STAT_OBS
            pytest.approx(1.0),         # [100, 200] < 300
            pytest.approx(1 / 3),       # [100, 200, 300] のうち 150 未満は 1 本
        ]
        assert all(reading.tail_unscaled is False for reading in readings)

    def test_the_result_is_limited_to_the_requested_bars(self) -> None:
        readings = trailing_ranks(np.arange(50, dtype=np.float64), window_n=10, n_bars=9)

        assert len(readings) == 9


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
