"""serve_replay の /intraday secs gate（後方互換）: secs=1 でのみ tick_secs を並行付与する。

MP tick-live が (sec, mid) を要求するため /intraday に ``secs=1`` gate を足す。secs 無の従来
リクエストは payload 完全不変（tick_secs キーを持たない＝forming MA/OHLC アニメ回帰ゼロ）。

★この時点で /intraday の secs gate（want_secs 透過・tick_secs 付与）は未実装（Red）。
"""
from __future__ import annotations

import json
import threading
import urllib.request

import pytest

from simulator.replay_ui.framework.serve_replay import ReplayApp, make_server


class _FakeCandlePort:
    def load_candles(self, ref, timeframe, limit):
        return []


class _FakeComputePort:
    def load_source(self, ref, timeframe):
        return []

    def compute(self, indicator, variant, mode, bars, params):
        return []


class _FakeWindowPort:
    def load_m1_rows(self, ref, start, end):
        return [[1.0, 2.0, 0.5, 1.5]]

    def load_ticks(self, start, end):
        return [(10, 100.0), (20, 101.0)]


@pytest.fixture
def ctx():
    app = ReplayApp(
        candle_port=_FakeCandlePort(), compute_port=_FakeComputePort(),
        window_port=_FakeWindowPort(), is_known_ref=lambda r: True,
    )
    server = make_server(app, "127.0.0.1", None)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return r.status, json.loads(r.read())


def test_intraday_without_secs_payload_unchanged_no_tick_secs(ctx):
    # Arrange / Act: 従来リクエスト（secs 無）。
    status, body = _get(ctx, "/intraday?datasetRef=jp225_tick&start=0&end=60&mode=real_ticks")
    # Assert: 従来 payload 完全不変（ticks は mid のみ・tick_secs キー無し＝後方互換）。
    assert status == 200
    assert body["ticks"] == [100.0, 101.0]
    assert "tick_secs" not in body


def test_intraday_with_secs1_adds_parallel_tick_secs(ctx):
    # Arrange / Act: MP tick-live が secs=1 を付与。
    status, body = _get(ctx, "/intraday?datasetRef=jp225_tick&start=0&end=60&mode=real_ticks&secs=1")
    # Assert: ticks=[mid...] 契約不変＋tick_secs=[sec...] 並行付与（同順・同長）。
    assert status == 200
    assert body["ticks"] == [100.0, 101.0]
    assert body["tick_secs"] == [10, 20]
