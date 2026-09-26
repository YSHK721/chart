"""指標系列を Bar 列へ時刻で対応させる（ISSUE-509）— 対応・Fail-Stop・計算量。

観測の境界:
    内側 registry は `IndicatorPort` として渡す（宣言された境界）。Test Spy はその
    ``get`` の発行を数える。対応部品の内部名は差し替えない。

期待値の導き方:
    切り出しの発行は「要求された系列名の集合」から導く（回数のリテラルを焼き込まない）。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

from simulator.adapter.indicator.bar_aligned_registry import align_to_bars
from simulator.adapter.indicator.registry import PandasIndicatorRegistry
from simulator.domain.exceptions import DataError
from simulator.usecase.ports import IndicatorPort

_T0 = 1_704_067_200


@dataclass(frozen=True)
class _B:
    time: int


def _rows(n: int) -> np.ndarray:
    return np.array([_T0 + 60 * i for i in range(n)], dtype=np.int64)


def _bars(first: int, n: int) -> "list[_B]":
    return [_B(_T0 + 60 * (first + i)) for i in range(n)]


class _GetSpy(IndicatorPort):
    def __init__(self, series: "dict[str, pd.Series]") -> None:
        self.calls: "list[str]" = []
        self._series = series

    def names(self) -> "tuple[str, ...]":
        return tuple(self._series)

    def get(self, name: str):
        self.calls.append(name)
        return self._series[name]

    def update(self, bar_index: int) -> None:
        return None


class TestAlignment:
    def test_bar_index_reads_the_row_of_the_same_time(self) -> None:
        # Arrange: 行の値＝行番号にして、どの行を読んだかを値で判別する。
        rows = 30
        inner = PandasIndicatorRegistry({"x": pd.Series(np.arange(rows, dtype=float))})
        # Act
        aligned = align_to_bars(inner, _rows(rows), _bars(10, 15))
        # Assert
        assert list(aligned.get("x")) == [float(10 + i) for i in range(15)]

    def test_a_run_over_all_rows_returns_the_inner_registry_itself(self) -> None:
        inner = PandasIndicatorRegistry({"x": pd.Series(np.arange(5, dtype=float))})
        assert align_to_bars(inner, _rows(5), _bars(0, 5)) is inner

    def test_names_are_delegated(self) -> None:
        inner = PandasIndicatorRegistry({"a": pd.Series([1.0] * 5), "b": pd.Series([2.0] * 5)})
        assert align_to_bars(inner, _rows(5), _bars(1, 3)).names() == ("a", "b")


class TestFailStop:
    def test_a_bar_time_missing_from_the_rows_stops(self) -> None:
        inner = PandasIndicatorRegistry({"x": pd.Series([0.0] * 10)})
        bars = _bars(2, 3) + [_B(_T0 + 60 * 9 + 1)]
        with pytest.raises(DataError):
            align_to_bars(inner, _rows(10), bars)

    def test_bars_that_skip_a_row_stop(self) -> None:
        inner = PandasIndicatorRegistry({"x": pd.Series([0.0] * 10)})
        bars = [_B(_T0 + 60 * 2), _B(_T0 + 60 * 4)]
        with pytest.raises(DataError):
            align_to_bars(inner, _rows(10), bars)

    def test_a_series_shorter_than_the_rows_stops(self) -> None:
        inner = PandasIndicatorRegistry({"x": pd.Series([0.0] * 9)})
        aligned = align_to_bars(inner, _rows(10), _bars(2, 3))
        with pytest.raises(DataError):
            aligned.get("x")


class TestComplexity:
    """切り出しは系列名ごとに 1 回（発行 − 要求名 = 0）・長さは足数で行数に依らない。"""

    @pytest.mark.parametrize(("rows", "first", "n"), [(50, 10, 20), (5000, 1000, 20), (5000, 10, 3000)])
    def test_each_name_is_sliced_once_however_often_it_is_read(
        self, rows: int, first: int, n: int
    ) -> None:
        # Arrange
        spy = _GetSpy({"a": pd.Series(np.zeros(rows)), "b": pd.Series(np.zeros(rows))})
        aligned = align_to_bars(spy, _rows(rows), _bars(first, n))
        requested = ["a", "b", "a"]
        # Act: 足ごとに読む戦略を模して、足数ぶん繰り返し読む。
        lengths = {len(aligned.get(name)) for _ in range(n) for name in requested}
        # Assert
        assert Counter(spy.calls) == Counter(set(requested))
        assert lengths == {n}


class TestRowTimes:
    """行の時刻は、各形式の時刻の表記を UTC の epoch 秒として読んだ値（独立に計算して照合）。"""

    def test_mt5_rows(self) -> None:
        from simulator.main.ea_bindings.sources import ohlc_repository_for, row_times_for

        # Arrange: MT5 の日付 2025.01.02 と時刻を、pandas だけで独立に epoch 秒へ直す。
        path = "simulator/tests/fixtures/mt5/ma_slope_jp225_202501/input/JP225_M1_202501.csv"
        raw = pd.read_csv(path, sep="\t", usecols=["<DATE>", "<TIME>"])
        stamps = pd.to_datetime(raw["<DATE>"].str.replace(".", "-") + " " + raw["<TIME>"], utc=True)
        expected = ((stamps - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)).tolist()
        # Act / Assert
        assert row_times_for(ohlc_repository_for(path), path).tolist() == expected

    def test_marketdata_rows(self, tmp_path) -> None:
        from simulator.main.ea_bindings.sources import ohlc_repository_for, row_times_for

        path = tmp_path / "md.csv"
        pd.DataFrame(
            {
                "date": ["2024-01-01 00:00:00", "2024-01-01 00:01:00"],
                "open": [1.0, 1.0], "high": [1.0, 1.0], "low": [1.0, 1.0],
                "close": [1.0, 1.0], "volume": [1.0, 1.0],
            }
        ).to_csv(path, index=False)
        assert row_times_for(ohlc_repository_for(path), path).tolist() == [_T0, _T0 + 60]

    def test_comma_rows(self, tmp_path) -> None:
        from simulator.main.ea_bindings.sources import ohlc_repository_for, row_times_for

        path = tmp_path / "c.csv"
        pd.DataFrame(
            {
                "time": [_T0, _T0 + 60], "open": [1.0, 1.0], "high": [1.0, 1.0],
                "low": [1.0, 1.0], "close": [1.0, 1.0], "volume": [1.0, 1.0], "spread": [0, 0],
            }
        ).to_csv(path, index=False)
        assert row_times_for(ohlc_repository_for(path), path).tolist() == [_T0, _T0 + 60]
