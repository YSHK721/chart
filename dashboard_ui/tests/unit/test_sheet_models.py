"""Input / Output Model と Output Boundary（P-1〜P-4）の契約を固定する。

畳み込みキー `(indicator_id, variant, params_key, timeframe)` は §7 の計算量表明の土台である
（同一キーの full 系列発行は 1 回以下・発行した系列は必ず出力に使われる）。キーが不安定だと
同じ計算を 2 回発行しても検査が通ってしまうため、キーの決定性をここで固定する。
"""
from __future__ import annotations

import pytest

from dashboard_ui.usecase.sheet_models import (
    Degradation,
    OscillatorSpec,
    ReachSheetRequest,
    SeriesRole,
    SheetInstance,
    UpdateGranularity,
)
from dashboard_ui.usecase.sheet_ports import (
    BarSupplyPort,
    BreakpointRegistryPort,
    BreakpointSourcePort,
    ForwardEvaluationPort,
    IndicatorSeriesPort,
    SeriesRolePort,
)


class TestSheetInstance:
    def test_the_key_folds_identical_instances_together(self) -> None:
        first = SheetInstance("ma_marod", "default", {"length": 24, "source": "hlc3"}, "5m")
        second = SheetInstance("ma_marod", "default", {"source": "hlc3", "length": 24}, "5m")

        assert first.key == second.key

    def test_the_key_separates_different_parameters(self) -> None:
        first = SheetInstance("ma_marod", "default", {"length": 24}, "5m")
        second = SheetInstance("ma_marod", "default", {"length": 60}, "5m")

        assert first.key != second.key

    def test_the_key_separates_different_timeframes(self) -> None:
        first = SheetInstance("ma_marod", "default", {"length": 24}, "5m")
        second = SheetInstance("ma_marod", "default", {"length": 24}, "1h")

        assert first.key != second.key

    def test_the_params_key_is_stable_across_processes(self) -> None:
        """キーは辞書の順序や `hash()` の乱数化に依存してはならない。"""
        instance = SheetInstance("x", "default", {"b": 1, "a": 2}, "1m")

        assert instance.params_key == '{"a": 2, "b": 1}'

    def test_a_chart_following_instance_resolves_to_the_chart_timeframe(self) -> None:
        """§2 chart 追従水準: params の timeframe が "chart"（未指定含む）。"""
        instance = SheetInstance.of(
            "moving_averages", "default", {"length": 24}, chart_timeframe="5m")

        assert instance.timeframe == "5m"
        assert "timeframe" not in instance.params

    def test_an_mtf_fixed_instance_keeps_its_own_timeframe(self) -> None:
        """§2 MTF 固定水準: 表示時間足に依らず値が同一なので、軸は自分の timeframe。"""
        instance = SheetInstance.of(
            "moving_averages", "default", {"length": 24, "timeframe": "1D"},
            chart_timeframe="5m")

        assert instance.timeframe == "1D"
        assert "timeframe" not in instance.params

    def test_an_explicit_chart_timeframe_parameter_follows_the_chart(self) -> None:
        instance = SheetInstance.of(
            "moving_averages", "default", {"timeframe": "chart"}, chart_timeframe="15m")

        assert instance.timeframe == "15m"

    def test_the_same_mtf_instance_on_two_charts_folds_to_one_key(self) -> None:
        """§7 の核心: 1D 固定の水準は表示足が違っても 1 回しか発行してはならない。"""
        from_five = SheetInstance.of("x", "default", {"timeframe": "1D"}, chart_timeframe="5m")
        from_hour = SheetInstance.of("x", "default", {"timeframe": "1D"}, chart_timeframe="1h")

        assert from_five.key == from_hour.key

    def test_intrabar_capability_defaults_to_declared_absent(self) -> None:
        """更新粒度は宣言（増分器の登録有無）で決まる。既定で「できる」と嘘をつかない。"""
        assert SheetInstance("cvfe", "default", {}, "5m").intrabar_capable is False


class TestReachSheetRequest:
    def test_unique_keys_drop_duplicated_instances(self) -> None:
        instances = [
            SheetInstance("a", "default", {"n": 1}, "1m"),
            SheetInstance("a", "default", {"n": 1}, "1m"),
            SheetInstance("a", "default", {"n": 2}, "1m"),
        ]

        request = ReachSheetRequest(dataset_ref="jp225_tick", instances=tuple(instances))

        assert len(request.unique_instances()) == 2

    def test_the_unique_order_is_the_order_of_first_appearance(self) -> None:
        instances = [
            SheetInstance("b", "default", {}, "1m"),
            SheetInstance("a", "default", {}, "1m"),
            SheetInstance("b", "default", {}, "1m"),
        ]

        request = ReachSheetRequest(dataset_ref="x", instances=tuple(instances))

        assert [i.indicator_id for i in request.unique_instances()] == ["b", "a"]

    def test_an_empty_request_is_allowed(self) -> None:
        assert ReachSheetRequest(dataset_ref="x", instances=()).unique_instances() == ()


class TestOutputModels:
    def test_a_degradation_states_the_update_granularity_and_the_reason(self) -> None:
        """§7: cvfe は増分器が無く段 1 でしか更新されない。これを隠さず表示へ渡す。"""
        degradation = Degradation(
            instance_key=("cvfe", "default", "{}", "5m"),
            granularity=UpdateGranularity.BAR_CLOSE,
            reason="増分器が未登録のため足内更新できない",
        )

        assert degradation.granularity is UpdateGranularity.BAR_CLOSE

    def test_the_series_roles_are_exactly_level_and_not_level(self) -> None:
        assert [role.name for role in SeriesRole] == ["PRICE_LEVEL", "NOT_LEVEL"]

    def test_an_oscillator_spec_carries_what_the_p_scale_needs(self) -> None:
        spec = OscillatorSpec(
            value_series="rsi", band_high_series="rsi_q90",
            q_high=0.9, window_n=500, k_events=50)

        assert spec.q_high == 0.9
        assert spec.excess(95.0, 90.0) == pytest.approx(5.0)


class TestPorts:
    @pytest.mark.parametrize(
        "port",
        [IndicatorSeriesPort, BarSupplyPort, ForwardEvaluationPort,
         BreakpointSourcePort, BreakpointRegistryPort, SeriesRolePort],
    )
    def test_every_port_is_a_runtime_checkable_protocol(self, port: type) -> None:
        assert getattr(port, "_is_protocol", False) is True
        assert getattr(port, "_is_runtime_protocol", False) is True

    def test_a_conforming_fake_satisfies_the_series_port(self) -> None:
        class Fake:
            def full_series(self, *, indicator_id, variant, params, dataset_ref, timeframe):
                return {}

        assert isinstance(Fake(), IndicatorSeriesPort)

    def test_a_non_conforming_fake_does_not_satisfy_the_series_port(self) -> None:
        class Fake:
            def something_else(self):
                return None

        assert not isinstance(Fake(), IndicatorSeriesPort)

    def test_a_breakpoint_registry_reports_the_invertible_indicators(self) -> None:
        """§5.5.1: tickvol の除外は列挙で書かず `breakpoints()` を提供できない形で現れる。"""

        class Fake:
            def resolve(self, indicator_id):
                return None

            def invertible_ids(self):
                return frozenset({"ma_marod"})

        assert isinstance(Fake(), BreakpointRegistryPort)
