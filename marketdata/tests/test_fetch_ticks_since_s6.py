"""ISSUE-049 — ``fetch_ticks_since`` の検証（TDD: Red→Green・ベンダ隔離の増分カーソル取得）。

設計正典: prototype_260707-01/server.py:60-66（``_fetch`` 増分カーソル API の呼び方＝実測済み）。

確定仕様:
  1. ``marketdata.dukascopy_source.fetch_ticks_since(cursor_ms, *, instrument=JP225,
     limit=30_000) -> list[(unix_ms, bid, ask)]``。
  2. 実体は ``dukascopy_python._fetch``（private・freeserv 増分カーソル）の薄いラッパ。
     引数は instrument / interval=INTERVAL_TICK / offer_side=OFFER_SIDE_BID /
     last_update=cursor_ms / limit（prototype と同一）。
  3. 戻りは ``cursor_ms`` より**厳密に後**の行のみ（境界重複を排する）・昇順・(ms,bid,ask) 変換。
  4. ``marketdata`` パッケージ直下から遅延 import で公開される。

ネットワーク非依存（memory dukascopy-data-source）: 実 ``_fetch`` は叩かず monkeypatch した
fake raw rows を返す。
"""

from __future__ import annotations

import pytest


@pytest.fixture
def patched_fetch(monkeypatch):
    """``dukascopy_python._fetch`` を fake へ差し替え、呼出引数を記録する。"""
    import dukascopy_python

    calls = []
    # (ms, bid, ask, bidVol, askVol) 相当の raw 行（_fetch は列位置 0=ms,1=bid,2=ask を返す）。
    rows = [
        [1000, 39000.0, 39005.0, 1.0, 3.0],
        [1500, 39001.0, 39006.0, 2.0, 4.0],
        [2000, 39002.0, 39007.0, 1.0, 1.0],
    ]

    def _fake(*, instrument, interval, offer_side, last_update, limit):
        calls.append(
            {
                "instrument": instrument,
                "interval": interval,
                "offer_side": offer_side,
                "last_update": last_update,
                "limit": limit,
            }
        )
        return rows

    monkeypatch.setattr(dukascopy_python, "_fetch", _fake, raising=False)
    return calls


def test_fetch_ticks_since_returns_ms_bid_ask_tuples(patched_fetch):
    from marketdata.dukascopy_source import fetch_ticks_since

    out = fetch_ticks_since(0)

    assert out == [
        (1000, 39000.0, 39005.0),
        (1500, 39001.0, 39006.0),
        (2000, 39002.0, 39007.0),
    ]


def test_fetch_ticks_since_excludes_rows_at_or_before_cursor(patched_fetch):
    # cursor=1000 → ms>1000 のみ（1000 自身は境界重複として除外）。
    from marketdata.dukascopy_source import fetch_ticks_since

    out = fetch_ticks_since(1000)

    assert [r[0] for r in out] == [1500, 2000]


def test_fetch_ticks_since_passes_cursor_as_last_update_and_tick_interval(patched_fetch):
    import dukascopy_python
    from marketdata.dukascopy_source import fetch_ticks_since

    fetch_ticks_since(1234, limit=500)

    call = patched_fetch[-1]
    assert call["last_update"] == 1234
    assert call["limit"] == 500
    assert call["interval"] == dukascopy_python.INTERVAL_TICK
    assert call["offer_side"] == dukascopy_python.OFFER_SIDE_BID


def test_fetch_ticks_since_exported_lazily_from_marketdata_package(patched_fetch):
    # パッケージ直下から遅延 import で解決（LiveTickBuffer の既定 fetch_fn）。
    import marketdata

    assert callable(marketdata.fetch_ticks_since)
    assert marketdata.fetch_ticks_since(1500) == [(2000, 39002.0, 39007.0)]
