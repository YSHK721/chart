"""足の時刻の一括変換（usecase/bar_times・ISSUE-553 項目 3）の検定。

表明:
    1. 状態検証: 値は暦から独立に書いた epoch 秒（秒未満は切り捨て）と一致する（datetime64 の
       各単位・単位の混在・秒未満・1970 年以前・整数・混在）。未対応の表現は ConfigError。
    2. 計算量: 変換 1 回で列全体を扱う（呼び出し 1 回・一括）。列の長さ 10 / 100 で呼び出しは増えない。
"""
from __future__ import annotations

import numpy as np
import pytest

from simulator.domain.exceptions import ConfigError
from simulator.usecase import bar_times

#: 2024-01-01T00:00:00Z の epoch 秒（1704067200 = 19723 日 × 86400）。
_T0 = 1_704_067_200

#: (時刻の列, 期待する epoch 秒の列)。期待値は秒未満を切り捨てた（floor）暦の値として独立に書く。
_CASES = {
    "datetime64[s]": (
        [np.datetime64("2024-01-01T00:00:00", "s") + np.timedelta64(i * 61, "s") for i in range(3)],
        [_T0, _T0 + 61, _T0 + 122],
    ),
    "datetime64[ns]_subsecond": (
        [np.datetime64("2024-01-01T00:00:00.999999999", "ns") + np.timedelta64(i, "m") for i in range(3)],
        [_T0, _T0 + 60, _T0 + 120],
    ),
    "datetime64[ms]_before_1970": (
        [np.datetime64("1969-12-31T23:59:59.500", "ms") + np.timedelta64(i, "s") for i in range(3)],
        [-1, 0, 1],
    ),
    "mixed_units": (
        [np.datetime64("2024-01-01T00:00:00", "s"), np.datetime64("2024-01-01T00:01:00.250", "ms"),
         np.datetime64("2024-01-01T00:02:00.000000001", "ns")],
        [_T0, _T0 + 60, _T0 + 120],
    ),
    "int": ([_T0, _T0 + 60], [_T0, _T0 + 60]),
    "numpy_int64": ([np.int64(_T0), np.int64(_T0 + 60)], [_T0, _T0 + 60]),
    "mixed_representations": (
        [np.datetime64("2024-01-01T00:00:00", "s"), _T0 + 60, np.int64(_T0 + 120)],
        [_T0, _T0 + 60, _T0 + 120],
    ),
    "empty": ([], []),
}


@pytest.fixture
def calls():
    seen: "list[tuple[int, bool]]" = []
    bar_times.set_observer(lambda count, batched: seen.append((count, batched)))
    yield seen
    bar_times.set_observer(None)


@pytest.mark.parametrize("name", sorted(_CASES))
def test_values_are_the_floored_epoch_seconds(name) -> None:
    times, expected = _CASES[name]
    assert bar_times.epoch_seconds_of(times) == expected


def test_values_are_python_ints() -> None:
    out = bar_times.epoch_seconds_of(_CASES["datetime64[ns]_subsecond"][0])
    assert all(type(v) is int for v in out)


def test_unsupported_representation_raises_the_domain_error() -> None:
    with pytest.raises(ConfigError):
        bar_times.epoch_seconds_of([np.datetime64("2024-01-01", "s"), "2024-01-01"])


@pytest.mark.parametrize("length", [10, 100])
def test_one_batched_call_covers_the_whole_sequence(calls, length) -> None:
    times = [np.datetime64("2024-01-01T00:00:00", "s") + np.timedelta64(i, "m") for i in range(length)]
    bar_times.epoch_seconds_of(times)
    # 発行 − 1 = 0（列の長さに依らない）・一括で列全体を扱う
    assert len(calls) - 1 == 0
    assert calls == [(length, True)]


@pytest.mark.parametrize("length", [0, 10, 100])
def test_the_result_observer_sees_each_returned_column_itself(length) -> None:
    # Arrange: 返した列の実体を観測する口（2 引数の「`set_observer`」とは別の口）。
    times = [np.datetime64("2024-01-01T00:00:00", "s") + np.timedelta64(i, "m") for i in range(length)]
    seen: "list[list[int]]" = []
    bar_times.set_result_observer(seen.append)
    try:
        # Act
        returned = [bar_times.epoch_seconds_of(times), bar_times.bar_epoch_seconds([])]
    finally:
        bar_times.set_result_observer(None)

    # Assert: 変換 1 回ごとに、返した列そのもの（同一の実体）を知らせる。
    assert len(seen) == len(returned)
    assert all(s is r for s, r in zip(seen, returned))


def test_the_result_observer_is_off_by_default_and_after_removal() -> None:
    bar_times.set_result_observer(lambda column: pytest.fail("外した観測口が呼ばれた"))
    bar_times.set_result_observer(None)
    assert bar_times.epoch_seconds_of([_T0]) == [_T0]
