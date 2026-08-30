"""UC-01（段 1）: 束を畳んで既存系列を読み、第 1 表・第 2 表を組み立てる。

固定する核心:
  - **同一キーの full 系列は 1 回しか発行しない**（§7・T-1）。発行した系列は必ず出力に使う。
  - 水準／非水準は**実値の桁**で決まる（§3.1。名前で決めない）。判定表は adapter が持つ。
  - 更新粒度の差（`cvfe` は増分器が無い）を**隠さない**（§7）。
  - 水準が存在しないセルは**隠さない**（§5.2。空欄にせず「水準なし」と明示する）。
"""
from __future__ import annotations

import numpy as np
import pytest

from dashboard_ui.domain.bar import Bar
from dashboard_ui.domain.elapsed_fraction_pool import ElapsedFractionPool
from dashboard_ui.usecase.build_reach_sheet import build_reach_sheet
from dashboard_ui.usecase.sheet_models import (
    ElapsedComparison,
    OscillatorSpec,
    ReachSheetRequest,
    SeriesRole,
    SheetInstance,
    UpdateGranularity,
)

_NOW = 1_700_000_000


def _bars(closes: list[float], *, step: int = 60) -> "tuple[Bar, ...]":
    return tuple(
        Bar(time=_NOW + index * step, open=close, high=close + 1.0,
            low=close - 1.0, close=close)
        for index, close in enumerate(closes)
    )


def _points(values: list[float], *, step: int = 60) -> "tuple[tuple[int, float], ...]":
    return tuple((_NOW + index * step, value) for index, value in enumerate(values))


class FakeSeriesPort:
    """P-1 の Test Spy。発行されたキーを記録する。"""

    def __init__(self, series_by_key: dict) -> None:
        self._series_by_key = series_by_key
        self.issued: list[tuple] = []

    def full_series(self, *, indicator_id, variant, params, dataset_ref, timeframe):
        import json

        key = (indicator_id, variant,
               json.dumps(dict(params), sort_keys=True, ensure_ascii=False, default=str),
               timeframe)
        self.issued.append(key)
        return self._series_by_key.get(key, {})


class FakeBarPort:
    def __init__(self, bars_by_timeframe: dict) -> None:
        self._bars_by_timeframe = bars_by_timeframe
        self.requested: list[str] = []

    def bars(self, *, dataset_ref, timeframe):
        self.requested.append(timeframe)
        return self._bars_by_timeframe.get(timeframe, ())

    def forming_bar(self, *, dataset_ref, timeframe, now_unix):
        return None


class FakeRoles:
    """役割宣言（adapter 相当）。水準判定は**実値の桁**（現在値の 0.3〜3 倍）で行う。"""

    def __init__(self, specs: "dict | None" = None) -> None:
        self._specs = specs or {}

    def role_of(self, *, instance, series_name, values, reference_price):
        finite = [v for v in values if np.isfinite(v)]
        if not finite:
            return SeriesRole.NOT_LEVEL
        median = float(np.median(finite))
        inside = 0.3 * reference_price <= median <= 3.0 * reference_price
        return SeriesRole.PRICE_LEVEL if inside else SeriesRole.NOT_LEVEL

    def row_label(self, *, instance, series_name):
        return f"{instance.indicator_id} {series_name} {instance.params_key}"

    def row_naming(self, *, instance, series_name):
        return {"name": series_name, "period": None, "source": None, "extra": ""}

    def oscillator_spec(self, *, instance, series_names):
        return self._specs.get(instance.indicator_id)


def _request(*instances: SheetInstance, chart: str = "1m") -> ReachSheetRequest:
    return ReachSheetRequest(dataset_ref="jp225_tick", instances=instances,
                             chart_timeframe=chart)


class TestFolding:
    def test_a_duplicated_instance_is_issued_only_once(self) -> None:
        instance = SheetInstance("moving_averages", "default", {"length": 24}, "1m")
        series = FakeSeriesPort({instance.key: {"MA": _points([99.0, 100.0, 101.0])}})
        bars = FakeBarPort({"1m": _bars([100.0, 100.0, 100.0])})

        build_reach_sheet(_request(instance, instance), series_port=series,
                          bar_port=bars, roles=FakeRoles())

        assert series.issued.count(instance.key) == 1

    def test_the_same_mtf_instance_seen_from_two_charts_is_issued_once(self) -> None:
        """§7 の核心（ISSUE-450 と同型の無駄を作らない）。"""
        first = SheetInstance.of("moving_averages", "default", {"timeframe": "1D"},
                                 chart_timeframe="1m")
        second = SheetInstance.of("moving_averages", "default", {"timeframe": "1D"},
                                  chart_timeframe="5m")
        series = FakeSeriesPort({first.key: {"MA": _points([100.0, 101.0])}})
        bars = FakeBarPort({"1m": _bars([100.0, 100.0]), "1D": _bars([100.0, 100.0])})

        build_reach_sheet(_request(first, second), series_port=series,
                          bar_port=bars, roles=FakeRoles())

        assert len(series.issued) == 1

    def test_bars_are_requested_once_per_timeframe(self) -> None:
        one = SheetInstance("moving_averages", "default", {"length": 5}, "1m")
        two = SheetInstance("moving_averages", "default", {"length": 9}, "1m")
        series = FakeSeriesPort({
            one.key: {"MA": _points([100.0, 101.0])},
            two.key: {"MA": _points([100.0, 102.0])},
        })
        bars = FakeBarPort({"1m": _bars([100.0, 100.0])})

        build_reach_sheet(_request(one, two), series_port=series,
                          bar_port=bars, roles=FakeRoles())

        assert bars.requested.count("1m") == 1


class TestLadder:
    def test_price_scale_series_become_ladder_rows_in_descending_order(self) -> None:
        upper = SheetInstance("moving_averages", "default", {"length": 5}, "1m")
        lower = SheetInstance("moving_averages", "default", {"length": 9}, "1m")
        series = FakeSeriesPort({
            upper.key: {"MA": _points([104.0, 105.0])},
            lower.key: {"MA": _points([96.0, 95.0])},
        })
        bars = FakeBarPort({"1m": _bars([100.0, 100.0])})

        sheet = build_reach_sheet(_request(upper, lower), series_port=series,
                                  bar_port=bars, roles=FakeRoles())

        assert [row.price for row in sheet.rows] == [105.0, 95.0]
        assert sheet.current_price == 100.0
        assert sheet.current_index == 1

    def test_a_series_off_the_price_scale_is_not_a_ladder_row(self) -> None:
        """§3.1: `btlm_trail_beta` 等は水準ではない。除外は**実値の桁**で判定する。"""
        instance = SheetInstance("btlm_trail", "default", {}, "1m")
        series = FakeSeriesPort({instance.key: {
            "btlm_trail_mean": _points([100.0, 101.0]),
            "btlm_trail_beta": _points([2.0, 3.0]),
        }})
        bars = FakeBarPort({"1m": _bars([100.0, 100.0])})

        sheet = build_reach_sheet(_request(instance), series_port=series,
                                  bar_port=bars, roles=FakeRoles())

        assert [row.label.split()[1] for row in sheet.rows] == ["btlm_trail_mean"]

    def test_every_row_carries_its_reach_state(self) -> None:
        """§6.2 定義 A を第 1 表に適用する（クライアント側で積み上げない）。"""
        instance = SheetInstance("moving_averages", "default", {"length": 5}, "1m")
        series = FakeSeriesPort({instance.key: {"MA": _points([99.0, 99.0, 99.0])}})
        bars = FakeBarPort({"1m": _bars([98.0, 100.0, 101.0])})

        sheet = build_reach_sheet(_request(instance), series_port=series,
                                  bar_port=bars, roles=FakeRoles())

        assert sheet.rows[0].reach.reached is True
        assert sheet.rows[0].reach.since_time == _NOW + 60

    def test_a_row_whose_latest_level_is_missing_is_not_placed_on_the_ladder(self) -> None:
        """NaN の水準はラダーへ入れない（並びを壊さない・無言で最下段に沈めない）。"""
        instance = SheetInstance("cvfe", "default", {}, "1m")
        series = FakeSeriesPort({instance.key: {
            "cvfe_u1": _points([101.0, float("nan")])}})
        bars = FakeBarPort({"1m": _bars([100.0, 100.0])})

        sheet = build_reach_sheet(_request(instance), series_port=series,
                                  bar_port=bars, roles=FakeRoles())

        assert sheet.rows == ()

    def test_an_empty_request_yields_an_empty_sheet(self) -> None:
        bars = FakeBarPort({"1m": _bars([100.0])})

        sheet = build_reach_sheet(_request(), series_port=FakeSeriesPort({}),
                                  bar_port=bars, roles=FakeRoles())

        assert sheet.rows == () and sheet.cells == ()

    def test_a_chart_without_bars_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            build_reach_sheet(_request(), series_port=FakeSeriesPort({}),
                              bar_port=FakeBarPort({}), roles=FakeRoles())


class TestDegradation:
    def test_an_instance_without_intrabar_support_is_reported(self) -> None:
        """§7: `cvfe` は増分器が無く段 1 でしか更新されない。これを隠さない。"""
        instance = SheetInstance("cvfe", "default", {}, "1m", intrabar_capable=False)
        series = FakeSeriesPort({instance.key: {"cvfe_u1": _points([101.0, 102.0])}})
        bars = FakeBarPort({"1m": _bars([100.0, 100.0])})

        sheet = build_reach_sheet(_request(instance), series_port=series,
                                  bar_port=bars, roles=FakeRoles())

        assert [d.instance_key for d in sheet.degradations] == [instance.key]
        assert sheet.degradations[0].granularity is UpdateGranularity.BAR_CLOSE

    def test_an_instance_with_intrabar_support_is_not_reported(self) -> None:
        instance = SheetInstance("moving_averages", "default", {}, "1m",
                                 intrabar_capable=True)
        series = FakeSeriesPort({instance.key: {"MA": _points([101.0, 102.0])}})
        bars = FakeBarPort({"1m": _bars([100.0, 100.0])})

        sheet = build_reach_sheet(_request(instance), series_port=series,
                                  bar_port=bars, roles=FakeRoles())

        assert sheet.degradations == ()


class TestOscillatorCells:
    @staticmethod
    def _rsi_setup(values: list[float], bands: list[float]):
        instance = SheetInstance("profit_rsi", "default", {}, "1m", intrabar_capable=True)
        series = FakeSeriesPort({instance.key: {
            "rsi": _points(values), "rsi_q90": _points(bands)}})
        bars = FakeBarPort({"1m": _bars([100.0] * len(values))})
        roles = FakeRoles({"profit_rsi": OscillatorSpec(
            value_series="rsi", band_high_series="rsi_q90",
            q_high=0.9, window_n=500, k_events=50)})
        return instance, series, bars, roles

    def test_a_cell_reports_the_current_value_and_its_p(self) -> None:
        """§5.2: 色から絶対量は読めないため、現在値の数字を必ず併記する。"""
        instance, series, bars, roles = self._rsi_setup(
            [10.0, 20.0, 30.0, 25.0], [90.0] * 4)

        sheet = build_reach_sheet(_request(instance), series_port=series,
                                  bar_port=bars, roles=roles)

        assert len(sheet.cells) == 1
        assert sheet.cells[0].value == pytest.approx(25.0)
        assert sheet.cells[0].p == pytest.approx(2 / 3)
        assert sheet.cells[0].tail_unscaled is False

    def test_a_cell_outside_the_band_without_enough_events_has_no_scale(self) -> None:
        """§5.3.2 の 7 セル。帯外は単一色にし、濃淡でごまかさない。"""
        instance, series, bars, roles = self._rsi_setup(
            [10.0, 20.0, 30.0, 95.0], [90.0] * 4)

        sheet = build_reach_sheet(_request(instance), series_port=series,
                                  bar_port=bars, roles=roles)

        assert sheet.cells[0].p is None
        assert sheet.cells[0].tail_unscaled is True

    def test_a_cell_without_its_value_series_is_shown_as_unavailable(self) -> None:
        """§5.2: 水準が存在しないセルは隠さない（空欄にせず「水準なし」と明示する）。"""
        instance = SheetInstance("tickvol", "default", {}, "1M", intrabar_capable=True)
        series = FakeSeriesPort({instance.key: {}})
        bars = FakeBarPort({"1m": _bars([100.0]), "1M": _bars([100.0])})
        roles = FakeRoles({"tickvol": OscillatorSpec(
            value_series="tickvol", band_high_series="tickvol_q90",
            q_high=0.9, window_n=500, k_events=50)})

        sheet = build_reach_sheet(_request(instance), series_port=series,
                                  bar_port=bars, roles=roles)

        assert len(sheet.cells) == 1
        assert sheet.cells[0].value is None
        assert sheet.cells[0].p is None
        assert sheet.cells[0].unavailable_reason is not None

    def test_the_reach_time_of_a_cell_uses_definition_a(self) -> None:
        instance, series, bars, roles = self._rsi_setup(
            [95.0, 10.0, 95.0, 96.0], [90.0] * 4)

        sheet = build_reach_sheet(_request(instance), series_port=series,
                                  bar_port=bars, roles=roles)

        assert sheet.cells[0].reach.reached is True
        assert sheet.cells[0].reach.since_time == _NOW + 120


class TestCumulativeCells:
    """§5.3.3: 積み上がる量は**同じ経過割合の分布**へ当てる（部分和を完全和へ当てない）。"""

    @staticmethod
    def _setup():
        instance = SheetInstance("tickvol", "default", {}, "1h", intrabar_capable=True)
        series = FakeSeriesPort({instance.key: {
            "tickvol": _points([100.0, 200.0, 300.0]),
            "tickvol_q90": _points([1000.0] * 3)}})
        bars = FakeBarPort({"1m": _bars([100.0] * 3), "1h": _bars([100.0] * 3)})
        roles = FakeRoles({"tickvol": OscillatorSpec(
            value_series="tickvol", band_high_series="tickvol_q90",
            q_high=0.9, window_n=500, k_events=50, cumulative=True)})
        return instance, series, bars, roles

    def test_a_forming_cumulative_cell_is_ranked_against_the_same_elapsed_partials(
        self,
    ) -> None:
        instance, series, bars, roles = self._setup()
        pool = ElapsedFractionPool.from_units(
            [0, 0, 0, 1, 1, 1, 2, 2, 2], [1.0, 1.0, 1.0, 5.0, 5.0, 5.0, 9.0, 9.0, 9.0])
        comparison = ElapsedComparison(pool=pool, completed_units=2, forming_sum=11.0)

        sheet = build_reach_sheet(
            _request(instance), series_port=series, bar_port=bars, roles=roles,
            elapsed_comparisons={instance.key: comparison})

        # 経過 2 単位の過去の部分和は [2, 10, 18]。11 未満は 2 本。
        assert sheet.cells[0].p == pytest.approx(2 / 3)
        assert sheet.cells[0].value == pytest.approx(11.0)

    def test_a_cumulative_cell_without_a_comparison_set_is_shown_as_unavailable(
        self,
    ) -> None:
        """比較集合が無いのに確定足の分布へ当てない（それが §5.3.3 のバイアスそのもの）。"""
        instance, series, bars, roles = self._setup()

        sheet = build_reach_sheet(_request(instance), series_port=series,
                                  bar_port=bars, roles=roles)

        assert sheet.cells[0].p is None
        assert sheet.cells[0].unavailable_reason is not None


class TestTailFitCache:
    """当てはめの再利用は「イベント確定のときだけ」だが、**古い値を返してはならない**。

    参照実装 `probe_tailscale.py:125` のキャッシュキーは `(窓の本数, 窓の末尾, 窓の先頭)` で
    あり、本数だけではない。本数だけを見ると、窓がずれて中身が入れ替わった場合に古い
    当てはめを返す（直近 k_events 件のローリング窓なので本数は上限で頭打ちになる）。
    """

    def test_a_window_with_the_same_length_but_different_content_is_refitted(self) -> None:
        from dashboard_ui.usecase.build_reach_sheet import TailFitCache

        cache = TailFitCache()
        key = ("x", "default", "{}", "1m")
        first = cache.tail_for(key, [1.0] * 40, 30)

        second = cache.tail_for(key, [5.0] * 40, 30)

        assert first is not None and second is not None
        assert second.beta != pytest.approx(first.beta)

    def test_an_unchanged_window_reuses_the_previous_fit(self) -> None:
        from dashboard_ui.usecase.build_reach_sheet import TailFitCache

        cache = TailFitCache()
        key = ("x", "default", "{}", "1m")
        events = [1.0 + index * 0.1 for index in range(40)]

        assert cache.tail_for(key, events, 30) is cache.tail_for(key, list(events), 30)

    def test_only_the_rolling_window_takes_part_in_the_signature(self) -> None:
        """窓の外へ出た古い観測が変わっても、当てはめ結果は同じでよい（窓が同一なら同一）。"""
        from dashboard_ui.usecase.build_reach_sheet import TailFitCache

        cache = TailFitCache()
        key = ("x", "default", "{}", "1m")
        window = [1.0 + index * 0.1 for index in range(30)]

        first = cache.tail_for(key, [99.0, *window], 30)
        second = cache.tail_for(key, [7.0, *window], 30)

        assert first is second


class TestRowInstanceLink:
    """行から instance を辿れること（§7: `cvfe` 行に更新粒度を出すために要る）。

    `degradations` は instance 単位で出るため、行が属する instance を答えられないと
    「どの行がバー確定でしか動かないか」を表に出せない（無言の縮退になる）。
    """

    def test_every_row_names_the_instance_it_came_from(self) -> None:
        moving = SheetInstance("moving_averages", "default", {"length": 5}, "1m")
        cvfe = SheetInstance("cvfe", "default", {}, "1m")
        series = FakeSeriesPort({
            moving.key: {"MA": _points([104.0, 105.0])},
            cvfe.key: {"cvfe_u1": _points([96.0, 95.0])},
        })
        bars = FakeBarPort({"1m": _bars([100.0, 100.0])})

        sheet = build_reach_sheet(_request(moving, cvfe), series_port=series,
                                  bar_port=bars, roles=FakeRoles())

        assert [row.instance_key for row in sheet.rows] == [moving.key, cvfe.key]

    def test_the_row_instance_key_matches_the_degradation_key(self) -> None:
        """行と縮退の告知が同じキーで突き合わせられること。"""
        cvfe = SheetInstance("cvfe", "default", {}, "1m", intrabar_capable=False)
        series = FakeSeriesPort({cvfe.key: {"cvfe_u1": _points([101.0, 102.0])}})
        bars = FakeBarPort({"1m": _bars([100.0, 100.0])})

        sheet = build_reach_sheet(_request(cvfe), series_port=series, bar_port=bars,
                                  roles=FakeRoles())

        assert sheet.rows[0].instance_key == sheet.degradations[0].instance_key
