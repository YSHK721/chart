"""§6/§5.5.4 の値オブジェクト（Bar / SeriesPoint / RunningExtreme）を固定する。

`RunningExtreme.extended_by` は参照実装 `tools/measure/issue449/probe_heatmap.py:136-139`
（`hh[-1] = max(H0, C)` / `ll[-1] = min(L0, C)`）と同一規約であることを固定する。
ここが狂うと §5.5 の前進評価がすべて狂う。
"""
from __future__ import annotations

import math

import pytest

from dashboard_ui.domain.bar import Bar, RunningExtreme, SeriesPoint


def _bar(**over) -> Bar:
    base = dict(time=1_700_000_000, open=100.0, high=110.0, low=90.0, close=105.0)
    base.update(over)
    return Bar(**base)


class TestBar:
    def test_bar_exposes_ohlc_and_defaults_volume_to_zero(self) -> None:
        bar = _bar()

        assert (bar.time, bar.open, bar.high, bar.low, bar.close) == (
            1_700_000_000, 100.0, 110.0, 90.0, 105.0)
        assert bar.volume == 0.0

    def test_bar_is_immutable(self) -> None:
        bar = _bar()

        with pytest.raises((AttributeError, TypeError)):
            bar.close = 1.0  # type: ignore[misc]

    def test_bar_rejects_high_below_low(self) -> None:
        with pytest.raises(ValueError):
            _bar(high=90.0, low=110.0)

    def test_bar_accepts_high_equal_to_low(self) -> None:
        """境界値: 値動きの無い足は正当（拒否してはならない）。"""
        bar = _bar(high=100.0, low=100.0, open=100.0, close=100.0)

        assert bar.high == bar.low

    def test_bar_rejects_non_finite_prices(self) -> None:
        with pytest.raises(ValueError):
            _bar(close=float("nan"))

    def test_bar_rejects_negative_volume(self) -> None:
        with pytest.raises(ValueError):
            _bar(volume=-1.0)


class TestSeriesPoint:
    def test_series_point_holds_time_and_value(self) -> None:
        point = SeriesPoint(time=1_700_000_060, value=1.5)

        assert (point.time, point.value) == (1_700_000_060, 1.5)

    def test_series_point_allows_a_non_finite_value(self) -> None:
        """warm-up の NaN は「水準なし」を表す正当な値（無言で落とさない・§5.2）。"""
        point = SeriesPoint(time=1, value=float("nan"))

        assert math.isnan(point.value)


class TestRunningExtreme:
    def test_of_bar_takes_the_bar_high_and_low(self) -> None:
        extreme = RunningExtreme.of(_bar())

        assert (extreme.high, extreme.low) == (110.0, 90.0)

    def test_extended_by_a_higher_close_raises_only_the_high(self) -> None:
        extreme = RunningExtreme(high=110.0, low=90.0)

        extended = extreme.extended_by(120.0)

        assert (extended.high, extended.low) == (120.0, 90.0)

    def test_extended_by_a_lower_close_lowers_only_the_low(self) -> None:
        extreme = RunningExtreme(high=110.0, low=90.0)

        extended = extreme.extended_by(80.0)

        assert (extended.high, extended.low) == (110.0, 80.0)

    def test_extended_by_an_inside_close_changes_nothing(self) -> None:
        extreme = RunningExtreme(high=110.0, low=90.0)

        assert extreme.extended_by(100.0) == extreme

    def test_extended_by_the_boundary_close_changes_nothing(self) -> None:
        """境界値: C == H0 / C == L0 は更新なし（max/min の同値点）。"""
        extreme = RunningExtreme(high=110.0, low=90.0)

        assert extreme.extended_by(110.0) == extreme
        assert extreme.extended_by(90.0) == extreme

    def test_equal_extremes_compare_equal(self) -> None:
        """UC-02 の epoch=(bar_time, run_hi, run_lo) 不変判定の土台。"""
        assert RunningExtreme(high=110.0, low=90.0) == RunningExtreme(high=110.0, low=90.0)
        assert RunningExtreme(high=110.0, low=90.0) != RunningExtreme(high=110.1, low=90.0)

    def test_rejects_high_below_low(self) -> None:
        with pytest.raises(ValueError):
            RunningExtreme(high=90.0, low=110.0)

    def test_extended_by_a_non_finite_close_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            RunningExtreme(high=110.0, low=90.0).extended_by(float("nan"))
