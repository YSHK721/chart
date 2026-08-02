"""UC-R7 forming_tickvol — 形成中バーの実 tick 数（ISSUE-238）の検証。

固定する仕様:
  - 数える対象は ``/intraday`` と同じ mid 列（domain E-4 ``mid_series``＝窓フィルタ・mid 算出・
    中央値外れ値除去）。tick 集合の定義を 2 つ持たない。
  - 各 ``to`` 時点までに到来した数（``[win_start, to]``）を返す。単調非減少。
  - 窓終端では窓内の全 tick 数＝確定足の tickvol に一致する（段差なく収束する）。
  - 不明（窓不正・ティック取得失敗・0 件・``to`` 欠落）は ``None``＝呼び出し側は volume を
    載せない＝従来挙動（勝手に値を作らない）。
  - ``with_tick_volume`` は非破壊（入力 dict を書き換えない）。
"""

from __future__ import annotations

import pytest

from simulator.replay_ui.usecase.forming_tickvol import (
    forming_tick_counts,
    with_tick_volume,
)

_START, _END = 1000, 1100


class _FakePort:
    """``load_raw_ticks`` のみを持つ IntrabarWindowPort スタブ。"""

    def __init__(self, rows=None, raises=False):
        self._rows = rows if rows is not None else []
        self._raises = raises
        self.calls = []

    def load_m1_rows(self, ref, start, end):  # pragma: no cover — 本 usecase は使わない
        raise AssertionError("load_m1_rows は呼ばれない")

    def load_raw_ticks(self, start, end):
        self.calls.append((start, end))
        if self._raises:
            raise RuntimeError("tick 取得失敗")
        return list(self._rows)


def _ticks(secs, price=100.0):
    """``(sec, bid, ask)`` 列（mid=price 一定＝外れ値除去に掛からない）。"""
    return [(s, price - 0.5, price + 0.5) for s in secs]


def test_counts_are_cumulative_and_converge_to_the_window_total():
    port = _FakePort(_ticks([1000, 1010, 1020, 1050, 1099]))
    got = forming_tick_counts(
        window_port=port, win_start=_START, win_end=_END,
        tos=[1000, 1010, 1049, 1050, 1099],
    )
    assert got == [1, 2, 3, 4, 5]
    # 窓終端では窓内の全 tick 数（＝確定足の tickvol と同じ集合）へ一致する。
    assert got[-1] == 5


def test_counts_are_monotone_non_decreasing():
    port = _FakePort(_ticks(range(1000, 1100, 3)))
    got = forming_tick_counts(
        window_port=port, win_start=_START, win_end=_END,
        tos=list(range(1000, 1100, 7)),
    )
    assert all(b >= a for a, b in zip(got, got[1:]))


def test_ticks_are_loaded_once_for_the_whole_window():
    # 時点ごとに読み直さない（足内の各時点で IO を繰り返さないための構造）。
    port = _FakePort(_ticks([1000, 1010, 1020]))
    forming_tick_counts(window_port=port, win_start=_START, win_end=_END,
                        tos=[1000, 1005, 1010, 1020])
    assert port.calls == [(_START, _END)]


def test_ticks_outside_the_window_are_not_counted():
    port = _FakePort(_ticks([990, 999, 1000, 1099, 1100, 1200]))
    got = forming_tick_counts(window_port=port, win_start=_START, win_end=_END, tos=[1099])
    assert got == [2]          # 1000 と 1099 のみ（窓は [1000,1100)）


def test_outlier_ticks_are_excluded_like_intraday():
    # domain E-4 の中央値外れ値除去（|mid/m - 1| > 閾値）を通した集合を数える。
    rows = _ticks([1000, 1010, 1020, 1030]) + [(1040, 9000.0, 9000.0)]
    got = forming_tick_counts(window_port=_FakePort(rows), win_start=_START, win_end=_END,
                              tos=[1099])
    assert got == [4]          # 外れ値 1 件は数えない


@pytest.mark.parametrize("kwargs", [
    {"win_start": None, "win_end": _END},
    {"win_start": _START, "win_end": None},
    {"win_start": _END, "win_end": _START},      # 逆転
    {"win_start": "x", "win_end": _END},         # 非数
])
def test_invalid_window_yields_unknown(kwargs):
    got = forming_tick_counts(window_port=_FakePort(_ticks([1000])), tos=[1000, 1050], **kwargs)
    assert got == [None, None]


def test_tick_load_failure_yields_unknown():
    got = forming_tick_counts(window_port=_FakePort(raises=True), win_start=_START,
                              win_end=_END, tos=[1000])
    assert got == [None]


def test_empty_tick_window_yields_unknown():
    got = forming_tick_counts(window_port=_FakePort([]), win_start=_START, win_end=_END,
                              tos=[1000])
    assert got == [None]


def test_missing_to_yields_unknown_for_that_point_only():
    port = _FakePort(_ticks([1000, 1010]))
    got = forming_tick_counts(window_port=port, win_start=_START, win_end=_END,
                              tos=[1000, None, 1010])
    assert got == [1, None, 2]


def test_empty_tos_returns_empty():
    assert forming_tick_counts(window_port=_FakePort(), win_start=_START, win_end=_END,
                               tos=[]) == []


# ---- with_tick_volume ------------------------------------------------------


def test_with_tick_volume_sets_float_volume_without_mutating_input():
    src = {"time": 1000, "open": 1.0, "close": 2.0}
    out = with_tick_volume(src, 7)
    assert out["volume"] == 7.0 and isinstance(out["volume"], float)
    assert "volume" not in src            # 非破壊
    assert out["time"] == 1000 and out["close"] == 2.0


def test_with_tick_volume_is_a_noop_when_count_is_unknown():
    src = {"time": 1000, "close": 2.0}
    assert with_tick_volume(src, None) is src     # 値を作らない＝従来挙動


def test_with_tick_volume_ignores_non_dict():
    assert with_tick_volume(None, 5) is None
