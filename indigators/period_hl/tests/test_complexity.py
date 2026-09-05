"""period_hl の計算量テスト（絶対命令 2026-08-28・CLAUDE.md §計算量テストの規約）。

測るのは時間ではなく**回数**。畳み込みプリミティブ（core._ACC_MAX / _ACC_MIN）へ Spy を
差し、「畳み込みへ供した要素数 − 出力に使った要素数 = 0」（作ってから捨てる計算の不在）を
表明する。回数そのものを期待値に焼き込まず、入力を変えた 2 点以上でオーダーを固定する。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from common.module_loader import load_package
from testing.lwc_fakes import FakeChart

load_package("_period_hl_src", Path(__file__).resolve().parents[1] / "src")
core = importlib.import_module("_period_hl_src.core")
lwc = importlib.import_module("_period_hl_src.lwc_chart")


class _FoldSpy:
    """畳み込みへ供した要素数を数える Test Spy（値は本物の畳み込みへ委譲する）。"""

    def __init__(self, real):
        self._real = real
        self.elements = 0

    def __call__(self, values):
        self.elements += int(np.asarray(values).size)
        return self._real(values)


@pytest.fixture()
def fold_spies(monkeypatch) -> "tuple[_FoldSpy, _FoldSpy]":
    spy_max = _FoldSpy(np.maximum.accumulate)
    spy_min = _FoldSpy(np.minimum.accumulate)
    monkeypatch.setattr(core, "_ACC_MAX", spy_max)
    monkeypatch.setattr(core, "_ACC_MIN", spy_min)
    return spy_max, spy_min


def _years(prev_year_bars: int, current_year_bars: int) -> np.ndarray:
    return np.concatenate(
        (np.full(prev_year_bars, 2025), np.full(current_year_bars, 2026))
    )


class TestYtdFoldBudget:
    def test_issued_folds_equal_used_outputs(self, fold_spies):
        """発行した畳み込み要素 − 出力に使った要素 = 0（作ってから捨てる計算が無い）。"""
        # Arrange
        spy_max, spy_min = fold_spies
        years = _years(prev_year_bars=50, current_year_bars=200)
        high = np.arange(years.size, dtype=np.float64)
        low = -high

        # Act
        hi, lo = core.ytd_running_extremes(years, high, low)

        # Assert — 出力の有限要素（＝ラダー / チャートが使う点）と発行数が一致する。
        assert spy_max.elements == int(np.isfinite(hi).sum())
        assert spy_min.elements == int(np.isfinite(lo).sum())

    @pytest.mark.parametrize("current", [100, 400])
    def test_no_element_is_folded_twice(self, fold_spies, current):
        """オーダーの表明: 供給要素数は当年バー数に 1:1（2 点で固定・n^2 の再畳み込みが無い）。"""
        # Arrange
        spy_max, spy_min = fold_spies
        years = _years(prev_year_bars=30, current_year_bars=current)
        high = np.arange(years.size, dtype=np.float64)

        # Act
        core.ytd_running_extremes(years, high, -high)

        # Assert
        assert spy_max.elements == current
        assert spy_min.elements == current

    @pytest.mark.parametrize("prev", [10, 500])
    def test_extending_the_window_into_the_first_year_adds_no_folds(
        self, fold_spies, prev
    ):
        """窓を過去（最初の年の内側）へ伸ばしても畳み込みは増えない（NaN 区間は計算しない）。"""
        # Arrange
        spy_max, spy_min = fold_spies
        years = _years(prev_year_bars=prev, current_year_bars=150)
        high = np.arange(years.size, dtype=np.float64)

        # Act
        core.ytd_running_extremes(years, high, -high)

        # Assert — 供給要素数は最初の年のバー数（prev）に依らない。
        assert spy_max.elements + spy_min.elements == 300

    def test_a_window_inside_one_year_folds_nothing(self, fold_spies):
        """年境界を覆わない窓は畳み込みを 1 要素も発行しない（NaN を「計算して捨て」ない）。"""
        # Arrange
        spy_max, spy_min = fold_spies
        years = np.full(250, 2026)
        high = np.arange(250, dtype=np.float64)

        # Act
        core.ytd_running_extremes(years, high, -high)

        # Assert
        assert spy_max.elements == 0
        assert spy_min.elements == 0


def _flat_df(days: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": pd.date_range("2025-12-01", periods=days, freq="D"),
            "open": np.full(days, 100.0),
            "high": np.full(days, 101.0),
            "low": np.full(days, 99.0),
            "close": np.full(days, 100.0),
        }
    )


class TestSeriesIssueCount:
    @pytest.mark.parametrize("days", [20, 80])
    def test_the_number_of_issued_series_does_not_grow_with_the_input(self, days):
        """系列の発行数は入力長に依らず指標あたり固定 2（2 点で固定する）。"""
        # Arrange
        chart = FakeChart()

        # Act
        lwc.add_period_hl(chart, _flat_df(days))
        lwc.add_ytd_hl(chart, _flat_df(days))

        # Assert
        assert len(chart.lines) == 4

    @pytest.mark.parametrize("days", [30, 120])
    def test_every_emitted_point_comes_from_exactly_one_input_bar(self, days):
        """発行した点 − 入力バー = 0（点の複製・再構築が無い）。2 点で固定する。"""
        # Arrange
        chart = FakeChart()

        # Act
        lwc.add_period_hl(chart, _flat_df(days))

        # Assert
        assert len(chart.lines[0].data) == days
        assert len(chart.lines[1].data) == days
