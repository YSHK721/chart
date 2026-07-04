"""usecase/scan_contacts: ticks_fn / ma_values を fake 注入した AAA（DI 結線）。

ma_values は bar_index → MA 値。scan_contacts が bar_times で ma_by_time へ写像し、
純エンジンへ委譲することを固定する（events / summary の一貫性）。
"""
from __future__ import annotations

from simulator.usecase.scan_contacts import (
    ScanContactsRequest,
    ScanContactsResult,
    scan_contacts,
)


def _request(full_scan=True):
    # engine テストと同一の 3 足シナリオ（bar1 のみ候補）。
    return ScanContactsRequest(
        ref="synthetic",
        timeframe="1m",
        indicator="moving_averages",
        variant="default",
        params={"ma_type": "ema", "length": 9},
        full_scan=full_scan,
        bar_times=[0, 60, 120],
        highs=[110.0, 110.0, 120.0],
        lows=[90.0, 90.0, 100.0],
        closes=[105.0, 108.0, 115.0],
    )


_MA_VALUES = {0: 100.0, 1: 200.0, 2: 123.0}   # bar_index → MA


def test_full_scan_events_and_summary():
    # Arrange
    req = _request(full_scan=True)
    ticks = [(61, 99.0), (62, 101.0), (63, 98.0)]

    def ticks_fn(start, end):
        return list(ticks)

    # Act
    res = scan_contacts(request=req, ticks_fn=ticks_fn, ma_values=_MA_VALUES)

    # Assert
    assert isinstance(res, ScanContactsResult)
    assert len(res.events) == 2
    assert [e["direction"] for e in res.events] == ["up", "down"]
    assert res.summary["schema"] == "contact.summary.v1"
    assert res.summary["contacts"] == 2
    assert res.summary["candidate_bars"] == 1
    assert res.summary["ticks_scanned"] == 3
    assert res.summary["range"] == {"from": 0, "to": 120, "n_bars": 3}
    # event の level は前足 MA（ma_values[bar_index=0]=100.0）
    assert res.events[0]["level"] == 100.0
    assert res.events[0]["bar_time"] == 60


def test_preview_mode_does_not_read_ticks():
    req = _request(full_scan=False)
    called = {"n": 0}

    def ticks_fn(start, end):
        called["n"] += 1
        return [(61, 99.0), (62, 101.0)]

    res = scan_contacts(request=req, ticks_fn=ticks_fn, ma_values=_MA_VALUES)
    assert called["n"] == 0
    assert res.summary["ticks_scanned"] == 0
    assert res.summary["scanned_bars"] == 0
    assert res.summary["candidate_bars"] == 1
    assert res.summary["full_scan"] is False


def test_event_json_shape_keys():
    # フロント agg.contacts 用の contact.v1 契約（tick_time:int / price:float / direction:str）。
    req = _request(full_scan=True)
    res = scan_contacts(
        request=req,
        ticks_fn=lambda s, e: [(61, 99.0), (62, 101.0)],
        ma_values=_MA_VALUES,
    )
    ev = res.events[0]
    assert isinstance(ev["tick_time"], int)
    assert isinstance(ev["price"], float)
    assert ev["direction"] in ("up", "down")
    assert ev["schema"] == "contact.v1"
