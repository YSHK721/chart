"""UC-R3 intrabar_window: fake IntrabarWindowPort 注入の AAA（do_intraday 忠実）。

m1 は常に返す。ticks は mode=='real_ticks' のときのみ（他モードは tick 読込スキップ）。
例外は m1_error / ticks_error へ翻訳し、計算全体は落とさない。
"""
from __future__ import annotations

from simulator.replay_ui.usecase.intrabar_window import (
    IntrabarWindowRequest,
    intrabar_window,
)


class _FakeWindowPort:
    def __init__(self, m1=None, ticks=None, m1_exc=None, ticks_exc=None):
        self._m1 = m1 or []
        self._ticks = ticks or []
        self._m1_exc = m1_exc
        self._ticks_exc = ticks_exc
        self.tick_called = False

    def load_m1_rows(self, ref, start, end):
        if self._m1_exc:
            raise self._m1_exc
        return self._m1

    def load_raw_ticks(self, start, end):   # ISSUE-031: 生ティック (sec, bid, ask)
        self.tick_called = True
        if self._ticks_exc:
            raise self._ticks_exc
        return self._ticks


def _req(mode="real_ticks"):
    return IntrabarWindowRequest(ref="jp225_tick", start=0, end=60, mode=mode)


def test_real_ticks_returns_m1_and_tick_mids():
    port = _FakeWindowPort(
        m1=[[1.0, 2.0, 0.5, 1.5]], ticks=[(10, 99.5, 100.5), (20, 100.5, 101.5)]
    )
    res = intrabar_window(request=_req("real_ticks"), window_port=port)
    assert res.ok is True
    assert res.m1 == [[1.0, 2.0, 0.5, 1.5]]
    assert res.ticks == [100.0, 101.0]  # mid のみ取り出す
    assert res.m1_error is None and res.ticks_error is None


def test_non_real_ticks_skips_tick_loading():
    port = _FakeWindowPort(m1=[[1.0, 2.0, 0.5, 1.5]], ticks=[(10, 99.5, 100.5)])
    res = intrabar_window(request=_req("ohlc_1min"), window_port=port)
    assert res.m1 == [[1.0, 2.0, 0.5, 1.5]]
    assert res.ticks == []
    assert port.tick_called is False  # tick 読込スキップ（軽量維持）


def test_m1_error_translated_and_does_not_block():
    port = _FakeWindowPort(m1_exc=RuntimeError("boom-m1"), ticks=[(10, 99.5, 100.5)])
    res = intrabar_window(request=_req("real_ticks"), window_port=port)
    assert res.m1 == []
    assert res.m1_error is not None and "boom-m1" in res.m1_error
    # m1 失敗でも tick 経路は継続する。
    assert res.ticks == [100.0]


def test_ticks_error_translated():
    port = _FakeWindowPort(m1=[[1.0, 1.0, 1.0, 1.0]], ticks_exc=RuntimeError("boom-t"))
    res = intrabar_window(request=_req("real_ticks"), window_port=port)
    assert res.m1 == [[1.0, 1.0, 1.0, 1.0]]
    assert res.ticks == []
    assert res.ticks_error is not None and "boom-t" in res.ticks_error


def test_error_message_truncated_to_120_chars():
    port = _FakeWindowPort(m1_exc=RuntimeError("x" * 500))
    res = intrabar_window(request=_req("real_ticks"), window_port=port)
    assert len(res.m1_error) == 120
