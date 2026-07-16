"""SessionGate 単体テスト（ISSUE-094 🔴-1 抽出物の直接検証）。

RunBacktestInteractor から抽出した closed_bars セッション判定を直接検証する。
既存の run_backtest 挙動テスト（byte-identical）とは独立に、判定オブジェクト単体の
契約（集合メンバシップ・カレンダー None/非 None の導出）を固定する。
"""
from __future__ import annotations

from simulator.usecase.session_gate import SessionGate


class TestIsClosed:
    def test_index_in_set_is_closed(self):
        gate = SessionGate({1, 3, 5})
        assert gate.is_closed(1) is True
        assert gate.is_closed(3) is True
        assert gate.is_closed(5) is True

    def test_index_not_in_set_is_open(self):
        gate = SessionGate({1, 3, 5})
        assert gate.is_closed(0) is False
        assert gate.is_closed(2) is False
        assert gate.is_closed(4) is False

    def test_empty_set_is_always_open(self):
        gate = SessionGate(set())
        assert gate.is_closed(0) is False
        assert gate.is_closed(100) is False

    def test_closed_bars_property_returns_set(self):
        s = {2, 7}
        gate = SessionGate(s)
        assert gate.closed_bars == {2, 7}


class TestFromCalendar:
    def test_none_calendar_yields_empty_always_open(self):
        gate = SessionGate.from_calendar(None, bars=[object(), object()])
        assert gate.closed_bars == set()
        assert gate.is_closed(0) is False
        assert gate.is_closed(1) is False

    def test_delegates_to_closed_bar_indices(self):
        class _FakeCalendar:
            def __init__(self):
                self.seen = None

            def closed_bar_indices(self, bars):
                self.seen = bars
                return {0, 2}

        bars = [object(), object(), object()]
        cal = _FakeCalendar()
        gate = SessionGate.from_calendar(cal, bars)
        # 導出結果がそのまま反映される
        assert gate.closed_bars == {0, 2}
        assert gate.is_closed(0) is True
        assert gate.is_closed(1) is False
        assert gate.is_closed(2) is True
        # bars がそのまま委譲される
        assert cal.seen is bars
