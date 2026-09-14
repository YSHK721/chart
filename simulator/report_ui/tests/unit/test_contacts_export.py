"""contacts_export（tools 層）単体テスト — 接点イベント→agg.contacts 形状変換と
scan_contacts usecase 経由の接点算出（preview フォールバック）を AAA で固定する。

方針:
  - events_to_contacts: contact.v1 event（bar_time/price/direction）を
    agg.contacts 形状（{time, price, dir}）へ純変換する。
  - compute_segment_contacts: bars + ma_values を ScanContactsRequest へ束ね、
    scan_contacts usecase（挙動の正解＝プロト bit 一致）経由で接点を算出する。
    preview（full_scan=False）は確定足 close クロスのみ・tick 非読込。
"""
from __future__ import annotations

from dataclasses import dataclass

from simulator.report_ui.tools.contacts_export import (
    compute_segment_contacts,
    events_to_contacts,
)


@dataclass
class _Bar:
    time: int
    high: float
    low: float
    close: float
    open: float = 0.0


# --- events_to_contacts: contact.v1 → {time, price, dir} ---------------------

class TestEventsToContacts:
    def test_maps_bar_time_price_direction_to_contact_keys(self):
        # Arrange: contact.v1 event（他キー混在）
        events = [
            {"schema": "contact.v1", "bar_time": 1060, "price": 101.0,
             "direction": "up", "bar_index": 1, "level": 100.0},
            {"schema": "contact.v1", "bar_time": 1120, "price": 98.0,
             "direction": "down", "bar_index": 2, "level": 100.0},
        ]
        # Act
        contacts = events_to_contacts(events)
        # Assert: time=bar_time, dir=direction のみに射影
        assert contacts == [
            {"time": 1060, "price": 101.0, "dir": "up"},
            {"time": 1120, "price": 98.0, "dir": "down"},
        ]

    def test_empty_events_yield_empty_list(self):
        assert events_to_contacts([]) == []

    def test_contact_keys_are_exactly_time_price_dir(self):
        events = [{"bar_time": 5, "price": 1.0, "direction": "up"}]
        assert set(events_to_contacts(events)[0].keys()) == {"time", "price", "dir"}


# --- compute_segment_contacts: preview 経由の接点算出（usecase bit 一致） --------

class TestComputeSegmentContactsPreview:
    def _bars(self):
        # ma_prev=100 一定。bar1 で close 99→101（up）、bar2 で close 101→98（down）。
        return [
            _Bar(time=1000, high=101.0, low=99.0, close=99.0),
            _Bar(time=1060, high=102.0, low=98.0, close=101.0),
            _Bar(time=1120, high=102.0, low=97.0, close=98.0),
        ]

    def _ma(self):
        return {0: 100.0, 1: 100.0, 2: 100.0}

    def test_preview_returns_up_then_down_contacts(self):
        # Arrange
        bars, ma = self._bars(), self._ma()
        # Act: preview（tick 不要）
        contacts = compute_segment_contacts(
            bars=bars, ma_values=ma, ref="JP225", timeframe="M1",
            indicator="ema", variant="", params={"period": 60}, full_scan=False,
        )
        # Assert: bar1=up@t1060/101, bar2=down@t1120/98
        assert contacts == [
            {"time": 1060, "price": 101.0, "dir": "up"},
            {"time": 1120, "price": 98.0, "dir": "down"},
        ]

    def test_preview_does_not_call_ticks_fn(self):
        # preview は tick を一切読まない（ticks_fn 未呼出の実証）
        calls = []

        def ticks_fn(start, end):
            calls.append((start, end))
            return []

        compute_segment_contacts(
            bars=self._bars(), ma_values=self._ma(), ref="JP225", timeframe="M1",
            indicator="ema", variant="", params={}, ticks_fn=ticks_fn, full_scan=False,
        )
        assert calls == []

    def test_contact_shape_keys(self):
        contacts = compute_segment_contacts(
            bars=self._bars(), ma_values=self._ma(), ref="JP225", timeframe="M1",
            indicator="ema", variant="", params={}, full_scan=False,
        )
        assert contacts
        for c in contacts:
            assert set(c.keys()) == {"time", "price", "dir"}
            assert isinstance(c["time"], int)
            assert isinstance(c["price"], float)
            assert c["dir"] in ("up", "down")

    def test_no_crossing_yields_no_contacts(self):
        # close が終始 level(100) 上側 → クロスなし
        bars = [
            _Bar(time=1000, high=105.0, low=99.0, close=101.0),
            _Bar(time=1060, high=105.0, low=98.0, close=102.0),
        ]
        contacts = compute_segment_contacts(
            bars=bars, ma_values={0: 100.0, 1: 100.0}, ref="JP225",
            timeframe="M1", indicator="ema", variant="", params={}, full_scan=False,
        )
        assert contacts == []
