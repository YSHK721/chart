"""common_view.lwc_adapter — lwc 出力アダプタ共有プリミティブの契約を固定する回帰テスト。

ISSUE-179 項目 2 で 21 パッケージの ``lwc_chart.py`` へ複製されていた
``_resolve_times`` / ``_emit_line`` / 系列 Protocol を共有実装へ 1 本化した。本テストは
移設元（``profit_arctan`` / ``btlm_trail_marod`` 系 12 パッケージ）の挙動を **そのまま**
固定する（PORTING_GUIDE §5 の時刻解決順序：明示指定 > time 列 > date 列 > DatetimeIndex）。

固定する契約:
    1. 解決順序 4 経路と、解決不能時の KeyError。
    2. 戻り値は ``pd.Series``（``list`` ではない）・index は 0..n-1 へ reset 済み。
    3. 列名は大小不問、かつ **非 str 列名でも AttributeError にならない**（``str(c).lower()``）。
    4. ``emit_line`` は create_line → NaN 行 dropna → set の順で 1 系列を生成する。
    5. 系列 Protocol ``SeriesLike`` は ``set`` のみを要求する構造的部分型である。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Time": ["2024-01-01 00:00", "2024-01-01 00:01", "2024-01-01 00:02"],
            "close": [1.0, 2.0, 3.0],
        }
    )


# ---------------- resolve_times: 解決順序 ----------------


def test_resolve_times_prefers_explicit_time_column_case_insensitively() -> None:
    # Arrange
    from common_view.lwc_adapter import resolve_times

    df = _frame().rename(columns={"Time": "stamp"})

    # Act
    times = resolve_times(df, "STAMP")

    # Assert
    assert isinstance(times, pd.Series)
    assert times.iloc[0] == pd.Timestamp("2024-01-01 00:00")


def test_resolve_times_falls_back_to_time_column_when_unspecified() -> None:
    # Arrange
    from common_view.lwc_adapter import resolve_times

    # Act
    times = resolve_times(_frame(), None)

    # Assert
    assert list(times) == list(pd.to_datetime(_frame()["Time"]))


def test_resolve_times_falls_back_to_date_column_when_no_time_column() -> None:
    # Arrange
    from common_view.lwc_adapter import resolve_times

    df = _frame().rename(columns={"Time": "DATE"})

    # Act
    times = resolve_times(df, None)

    # Assert
    assert times.iloc[2] == pd.Timestamp("2024-01-01 00:02")


def test_resolve_times_falls_back_to_datetime_index_named_time() -> None:
    # Arrange
    from common_view.lwc_adapter import resolve_times

    df = _frame().set_index(pd.to_datetime(_frame()["Time"])).drop(columns=["Time"])

    # Act
    times = resolve_times(df, None)

    # Assert: DatetimeIndex 経路の系列名は "time" に固定される。
    assert times.name == "time"
    assert list(times.index) == [0, 1, 2]


# ---------------- resolve_times: 異常系・境界 ----------------


def test_resolve_times_raises_key_error_for_missing_explicit_column() -> None:
    # Arrange
    from common_view.lwc_adapter import resolve_times

    # Act / Assert
    with pytest.raises(KeyError, match="指定された時刻列が存在しません"):
        resolve_times(_frame(), "nope")


def test_resolve_times_raises_key_error_when_unresolvable() -> None:
    # Arrange
    from common_view.lwc_adapter import resolve_times

    df = pd.DataFrame({"close": [1.0, 2.0]})

    # Act / Assert
    with pytest.raises(KeyError, match="時刻を解決できません"):
        resolve_times(df, None)


def test_resolve_times_returns_series_with_reset_index() -> None:
    # Arrange
    from common_view.lwc_adapter import resolve_times

    df = _frame()
    df.index = [10, 11, 12]

    # Act
    times = resolve_times(df, None)

    # Assert: 戻り値は Series（list ではない）で index は 0..n-1。
    assert isinstance(times, pd.Series)
    assert not isinstance(times, list)
    assert list(times.index) == [0, 1, 2]


def test_resolve_times_tolerates_non_string_column_labels() -> None:
    # Arrange: header=None 相当（列ラベルが int）。str(c).lower() 規約の境界。
    from common_view.lwc_adapter import resolve_times

    df = pd.DataFrame({0: ["2024-01-01"], 1: [1.0]})

    # Act / Assert: AttributeError ではなく KeyError（解決不能）で落ちる。
    with pytest.raises(KeyError, match="時刻を解決できません"):
        resolve_times(df, None)


# ---------------- emit_line ----------------


class _FakeLine:
    def __init__(self, kwargs: dict) -> None:
        self.kwargs = kwargs
        self.data: pd.DataFrame | None = None

    def set(self, data: pd.DataFrame) -> None:
        self.data = data


class _FakeChart:
    def __init__(self) -> None:
        self.lines: list[_FakeLine] = []

    def create_line(self, **kwargs) -> _FakeLine:
        line = _FakeLine(kwargs)
        self.lines.append(line)
        return line


def test_emit_line_creates_line_and_sets_dropna_frame() -> None:
    # Arrange
    from common_view.lwc_adapter import emit_line

    chart = _FakeChart()
    times = pd.Series(pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]))
    values = [1.0, np.nan, 3.0]

    # Act
    line = emit_line(chart, "mid", times, values, "#fff", "solid")

    # Assert: NaN 行は除外され、値列名は系列名と一致する。
    assert line is chart.lines[0]
    assert list(line.data.columns) == ["time", "mid"]
    assert len(line.data) == 2
    assert line.kwargs == {
        "name": "mid",
        "color": "#fff",
        "style": "solid",
        "width": 1,
        "price_line": False,
        "price_label": False,
    }


def test_emit_line_casts_values_to_float() -> None:
    # Arrange
    from common_view.lwc_adapter import emit_line

    chart = _FakeChart()
    times = pd.Series(pd.to_datetime(["2024-01-01", "2024-01-02"]))

    # Act
    line = emit_line(chart, "v", times, [1, 2], "#000", "dotted")

    # Assert
    assert line.data["v"].dtype == np.float64


# ---------------- SeriesLike Protocol ----------------


def test_series_like_protocol_requires_only_set() -> None:
    # Arrange
    from common_view.lwc_adapter import SeriesLike

    # Act / Assert: set を持てば構造的部分型として成立する。
    assert isinstance(_FakeLine({}), SeriesLike)
    assert not isinstance(object(), SeriesLike)
