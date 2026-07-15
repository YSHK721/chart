"""marketdata TickSource ⇄ simulator ingest の契約テスト（利用者側で検証）。

ISSUE-091 #8: 旧所在 marketdata/tests/test_ticksource_s2.py（S2 enabler②）から移設。
最下層ライブラリ marketdata のテストが上位 simulator を import する逆依存を排し、
契約（RAW_COLUMNS 適合・to_canonical_ticks 消費可能・fetch_range 後方互換）は
契約の消費者である simulator 側スイートで検証する。検証内容・fake は移設前と同一。
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest


# Dukascopy raw fetch が返す形（timestamp を index に持ち bidPrice/askPrice/bidVolume/
# askVolume を列に持つ）を再現する fake。実 fetch はネットワーク依存のため monkeypatch する。
def _fake_fetch_frame(ts_list, bids, asks, bvols, avols):
    idx = pd.to_datetime(ts_list, utc=True)
    idx.name = "timestamp"
    return pd.DataFrame(
        {
            "bidPrice": bids,
            "askPrice": asks,
            "bidVolume": bvols,
            "askVolume": avols,
        },
        index=idx,
    )


@pytest.fixture
def patched_fetch(monkeypatch):
    """dukascopy_python.fetch を 1 日分の fake raw を返す関数へ差し替え、呼出を記録する。"""
    import dukascopy_python

    calls = []

    def _fake(instrument, interval, offer_side, start, end):
        calls.append(
            {
                "instrument": instrument,
                "interval": interval,
                "offer_side": offer_side,
                "start": start,
                "end": end,
            }
        )
        # 各呼出（日次チャンク）で 2 tick を返す（start 時刻基準）。
        base = pd.Timestamp(start)
        return _fake_fetch_frame(
            [base, base + pd.Timedelta(seconds=1)],
            bids=[39000.0, 39001.0],
            asks=[39005.0, 39006.0],
            bvols=[1.0, 2.0],
            avols=[3.0, 4.0],
        )

    monkeypatch.setattr(dukascopy_python, "fetch", _fake)
    return calls


def test_dukascopy_tick_source_returns_raw_columns_contract(patched_fetch):
    # H-2: 出力列が ingest の RAW_COLUMNS 契約へ直接適合する。
    from simulator.tools.ingest_ticks import RAW_COLUMNS
    from marketdata.dukascopy_source import DukascopyTickSource

    src = DukascopyTickSource()

    out = src.fetch_ticks(datetime(2025, 1, 2), datetime(2025, 1, 3))

    # Assert: RAW_COLUMNS が全て出力列に含まれる（to_canonical_ticks が直接消費可能）。
    for col in RAW_COLUMNS:
        assert col in out.columns, f"H-2: RAW_COLUMNS 契約列 {col!r} が欠落"


def test_to_canonical_ticks_unchanged_after_delegation(patched_fetch):
    # DukascopyTickSource 出力 → to_canonical_ticks が canonical を生成できる（契約適合）。
    # last=mid・volume=bid+ask の規約が委譲経路でも成立する。
    from simulator.adapter.repository._tick_frame import TICK_COLUMNS
    from simulator.tools.ingest_ticks import to_canonical_ticks
    from marketdata.dukascopy_source import DukascopyTickSource

    raw = DukascopyTickSource().fetch_ticks(datetime(2025, 1, 2), datetime(2025, 1, 3))

    canonical = to_canonical_ticks(raw)

    # Assert: canonical 列・last=mid・volume=合計（fake: bid=39000,ask=39005 → last=39002.5）。
    assert list(canonical.columns) == list(TICK_COLUMNS)
    assert canonical["last"].iloc[0] == (39000.0 + 39005.0) / 2.0
    assert canonical["volume"].iloc[0] == 1.0 + 3.0


def test_fetch_ticks_dukascopy_delegates_to_tick_source(patched_fetch):
    # fetch_ticks_dukascopy.fetch_range（後方互換 API）が DukascopyTickSource 経由で
    # timestamp 列化済の同形 DataFrame を返す（CLI 出力不変の核）。
    from simulator.tools.fetch_ticks_dukascopy import fetch_range
    import dukascopy_python

    out = fetch_range(
        datetime(2025, 1, 2), datetime(2025, 1, 3), dukascopy_python.OFFER_SIDE_BID
    )

    # Assert: timestamp が列（H-2）・bid/ask 両列（H-3）を委譲後も保つ。
    assert "timestamp" in out.columns
    assert "bidPrice" in out.columns and "askPrice" in out.columns
    assert len(out) == 2
