"""period_hl 出力アダプタ（add_period_hl / add_ytd_hl）の状態検証。

lightweight_charts は import せず duck typing（共有テストダブル testing.lwc_fakes）。
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
lwc = importlib.import_module("_period_hl_src.lwc_chart")


def _daily_df(start: str, days: int, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base = 100.0 + np.cumsum(rng.normal(0.0, 1.0, size=days))
    return pd.DataFrame(
        {
            "time": pd.date_range(start, periods=days, freq="D"),
            "open": base,
            "high": base + rng.uniform(0.1, 2.0, size=days),
            "low": base - rng.uniform(0.1, 2.0, size=days),
            "close": base,
        }
    )


class TestAddPeriodHl:
    def test_emits_the_high_and_low_columns_verbatim(self):
        # Arrange
        chart = FakeChart()
        df = _daily_df("2026-01-05", 20)

        # Act
        lines = lwc.add_period_hl(chart, df)

        # Assert — 系列名は固定 2 本・値は high / low と bit 同一（派生計算を持たない）。
        assert set(lines) == {"period_hl_hi", "period_hl_lo"}
        assert [line.name for line in chart.lines] == ["period_hl_hi", "period_hl_lo"]
        hi, lo = chart.lines[0].data, chart.lines[1].data
        assert hi["period_hl_hi"].to_numpy().tobytes() == df["high"].to_numpy().tobytes()
        assert lo["period_hl_lo"].to_numpy().tobytes() == df["low"].to_numpy().tobytes()
        assert list(hi["time"]) == list(df["time"])

    def test_missing_high_or_low_raises_value_error(self):
        df = _daily_df("2026-01-05", 5).drop(columns=["low"])
        with pytest.raises(ValueError):
            lwc.add_period_hl(FakeChart(), df)

    def test_missing_time_axis_raises_key_error(self):
        df = _daily_df("2026-01-05", 5).drop(columns=["time"]).reset_index(drop=True)
        with pytest.raises(KeyError):
            lwc.add_period_hl(FakeChart(), df)


class TestAddYtdHl:
    def test_second_year_resets_and_first_year_rows_are_dropped(self):
        # Arrange — 2025-12-29 起点の 10 日＝2025 年 3 本 ＋ 2026 年 7 本。
        chart = FakeChart()
        df = _daily_df("2025-12-29", 10)

        # Act
        lines = lwc.add_ytd_hl(chart, df)

        # Assert — 最初の年（NaN）は描画から除外され、2026 年ぶんだけが載る。
        assert set(lines) == {"ytd_hl_hi", "ytd_hl_lo"}
        hi, lo = chart.lines[0].data, chart.lines[1].data
        assert len(hi) == 7 and len(lo) == 7
        year_high = df["high"].to_numpy()[3:]
        year_low = df["low"].to_numpy()[3:]
        assert hi["ytd_hl_hi"].to_numpy().tobytes() == (
            np.maximum.accumulate(year_high).tobytes()
        )
        assert lo["ytd_hl_lo"].to_numpy().tobytes() == (
            np.minimum.accumulate(year_low).tobytes()
        )

    def test_the_latest_point_is_the_year_to_date_extreme(self):
        # Arrange
        chart = FakeChart()
        df = _daily_df("2025-12-01", 60)

        # Act
        lwc.add_ytd_hl(chart, df)

        # Assert
        in_2026 = df["time"].dt.year.to_numpy() == 2026
        assert chart.lines[0].data["ytd_hl_hi"].iloc[-1] == df["high"].to_numpy()[in_2026].max()
        assert chart.lines[1].data["ytd_hl_lo"].iloc[-1] == df["low"].to_numpy()[in_2026].min()

    def test_a_window_inside_one_year_emits_empty_series(self):
        # Arrange — 年境界を覆わない窓では「年初来」を主張しない（系列は空・名前は出る）。
        chart = FakeChart()
        df = _daily_df("2026-03-02", 15)

        # Act
        lines = lwc.add_ytd_hl(chart, df)

        # Assert
        assert set(lines) == {"ytd_hl_hi", "ytd_hl_lo"}
        assert len(chart.lines[0].data) == 0
        assert len(chart.lines[1].data) == 0
