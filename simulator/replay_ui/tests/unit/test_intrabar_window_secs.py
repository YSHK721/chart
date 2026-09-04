"""UC intrabar_window の sec 保持拡張（gated want_secs）: ticks 契約不変＋tick_secs 追加のみ。

MP tick-live は DwellAccumulator が (sec, mid) を要求する。intrabar_window は既存 ``ticks=[mid...]``
契約を **一切変えず**、``want_secs=True`` のときだけ並行配列 ``tick_secs=[sec...]`` を追加する
（後方互換＝forming MA/OHLC アニメ回帰ゼロ）。``want_secs=False``（既定）は従来 payload と完全一致。

★この時点で IntrabarWindowRequest.want_secs / IntrabarWindowResult.tick_secs は未実装（Red）。
"""
from __future__ import annotations

from simulator.replay_ui.usecase.intrabar_window import (
    IntrabarWindowRequest,
    IntrabarWindowResult,
    intrabar_window,
)


class _FakeWindowPort:
    def __init__(self, m1=None, ticks=None):
        self._m1 = m1 or []
        self._ticks = ticks or []

    def load_m1_rows(self, ref, start, end):
        return self._m1

    def load_raw_ticks(self, start, end):   # ISSUE-031: 生ティック (sec, bid, ask)
        return self._ticks


def _port():
    return _FakeWindowPort(m1=[[1.0, 2.0, 0.5, 1.5]], ticks=[(10, 99.5, 100.5), (20, 100.5, 101.5)])


def test_want_secs_default_false_keeps_ticks_unchanged_and_no_tick_secs():
    # Arrange: want_secs 未指定（既定 False）。
    req = IntrabarWindowRequest(ref="jp225_tick", start=0, end=60, mode="real_ticks")
    # Act
    res = intrabar_window(request=req, window_port=_port())
    # Assert: ticks は従来どおり mid のみ。tick_secs は空（従来 payload 不変＝後方互換）。
    assert res.ticks == [100.0, 101.0]
    assert res.tick_secs == []


def test_want_secs_true_adds_parallel_tick_secs_without_changing_ticks():
    # Arrange
    req = IntrabarWindowRequest(ref="jp225_tick", start=0, end=60, mode="real_ticks", want_secs=True)
    # Act
    res = intrabar_window(request=req, window_port=_port())
    # Assert: ticks=[mid...] 契約不変。tick_secs=[sec...] を並行配列で追加（同順・同長）。
    assert res.ticks == [100.0, 101.0]
    assert res.tick_secs == [10, 20]
    assert len(res.tick_secs) == len(res.ticks)


def test_want_secs_true_non_real_ticks_mode_skips_tick_load_and_secs_empty():
    # Arrange: real_ticks 以外は tick 読込スキップ（軽量維持）＝tick_secs も空。
    req = IntrabarWindowRequest(ref="jp225_tick", start=0, end=60, mode="ohlc_1min", want_secs=True)
    # Act
    res = intrabar_window(request=req, window_port=_port())
    # Assert
    assert res.ticks == []
    assert res.tick_secs == []


def test_result_default_tick_secs_is_empty_list():
    # Arrange / Act
    res = IntrabarWindowResult()
    # Assert: 既定 tick_secs は空 list（従来 payload に load しないと現れない）。
    assert res.tick_secs == []
