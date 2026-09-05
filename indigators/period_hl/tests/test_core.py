"""period_hl core（year_runs / ytd_running_extremes）の状態検証。

指標パッケージは top-level パッケージ名の同名衝突を避けるため、production と同じ機構
（``common.module_loader.load_package``）で一意名 _period_hl_src として読む。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import numpy as np
import pytest

from common.module_loader import load_package

load_package("_period_hl_src", Path(__file__).resolve().parents[1] / "src")
core = importlib.import_module("_period_hl_src.core")


class TestYearRuns:
    def test_splits_consecutive_years_into_half_open_runs(self):
        # Arrange
        years = np.array([2024, 2024, 2025, 2025, 2025, 2026])

        # Act
        runs = core.year_runs(years)

        # Assert
        assert runs == [(0, 2), (2, 5), (5, 6)]

    def test_single_year_is_one_run(self):
        assert core.year_runs(np.array([2026, 2026, 2026])) == [(0, 3)]

    def test_empty_input_yields_no_runs(self):
        assert core.year_runs(np.array([], dtype=int)) == []


class TestYtdRunningExtremes:
    def test_first_year_is_nan_and_later_years_fold_running_extremes(self):
        # Arrange — 2025 年 2 本（年初被覆を保証できない）＋ 2026 年 4 本。
        years = np.array([2025, 2025, 2026, 2026, 2026, 2026])
        high = np.array([110.0, 120.0, 100.0, 105.0, 103.0, 108.0])
        low = np.array([90.0, 95.0, 98.0, 97.0, 99.0, 96.0])

        # Act
        hi, lo = core.ytd_running_extremes(years, high, low)

        # Assert — 最初の年は NaN・次の年は年境界でリセットされた走行 max / min。
        assert np.isnan(hi[:2]).all() and np.isnan(lo[:2]).all()
        assert hi[2:].tolist() == [100.0, 105.0, 105.0, 108.0]
        assert lo[2:].tolist() == [98.0, 97.0, 97.0, 96.0]

    def test_the_latest_value_is_the_year_to_date_extreme(self):
        # Arrange
        years = np.array([2025, 2026, 2026, 2026])
        high = np.array([999.0, 100.0, 130.0, 120.0])
        low = np.array([1.0, 90.0, 80.0, 85.0])

        # Act
        hi, lo = core.ytd_running_extremes(years, high, low)

        # Assert — 前年の極値（999 / 1）は持ち越さない。
        assert hi[-1] == 130.0
        assert lo[-1] == 80.0

    def test_a_window_inside_one_year_yields_all_nan(self):
        # Arrange — 年境界を覆わない窓では「年初来」を主張しない。
        years = np.array([2026, 2026, 2026])
        high = np.array([1.0, 2.0, 3.0])
        low = np.array([1.0, 2.0, 3.0])

        # Act
        hi, lo = core.ytd_running_extremes(years, high, low)

        # Assert
        assert np.isnan(hi).all() and np.isnan(lo).all()

    def test_empty_input_yields_empty_output(self):
        hi, lo = core.ytd_running_extremes(
            np.array([], dtype=int), np.array([]), np.array([])
        )
        assert hi.size == 0 and lo.size == 0

    def test_mismatched_lengths_raise(self):
        with pytest.raises(ValueError):
            core.ytd_running_extremes(
                np.array([2026, 2026]), np.array([1.0, 2.0]), np.array([1.0])
            )
