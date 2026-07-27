"""serve_replay のカレンダー系エンドポイント（リプレイバーの再生開始日選択）。

対象:
  - GET /candles?...&from=&pre=  → WindowedCandlePort へ委譲（未指定時は従来の tail 経路のまま）
  - GET /available_days?...      → AvailableDaysPort へ委譲（days_port 未注入なら経路を持たない）
"""
from __future__ import annotations

import json
import threading
import urllib.request
from urllib.error import HTTPError

import pytest

from simulator.replay_ui.framework.serve_replay import ReplayApp, make_server


class _FakeCandlePort:
    """tail 経路と start 起点経路を呼び分けたことが分かる fake。"""

    def __init__(self):
        self.calls = []

    def load_candles(self, ref, timeframe, limit):
        self.calls.append(("tail", ref, timeframe, limit))
        return [{"time": 0, "open": 1, "high": 2, "low": 0, "close": 1.5}]

    def load_candles_from(self, ref, timeframe, start, pre, limit):
        self.calls.append(("from", ref, timeframe, start, pre, limit))
        return [{"time": start, "open": 1, "high": 2, "low": 0, "close": 1.5}]


class _FakeDaysPort:
    def load_days(self, ref, timeframe):
        if ref == "boom":
            raise RuntimeError("days failure")
        return ["2020-01-03", "2020-01-06"]


def _serve(app):
    server = make_server(app, "127.0.0.1", None)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, t, f"http://127.0.0.1:{port}"


@pytest.fixture
def ctx():
    candle_port = _FakeCandlePort()
    app = ReplayApp(
        candle_port=candle_port,
        compute_port=None,
        window_port=None,
        days_port=_FakeDaysPort(),
    )
    server, t, base = _serve(app)
    try:
        yield base, candle_port
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return r.status, json.loads(r.read())


def test_candles_without_from_uses_the_tail_path(ctx):
    base, candle_port = ctx
    status, body = _get(base, "/candles?datasetRef=jp225_tick&timeframe=1D&limit=10")
    assert status == 200 and body["ok"] is True
    assert candle_port.calls == [("tail", "jp225_tick", "1D", 10)]  # 従来経路＝挙動不変


def test_candles_with_from_and_pre_uses_the_windowed_path(ctx):
    base, candle_port = ctx
    status, body = _get(
        base, "/candles?datasetRef=jp225_tick&timeframe=1D&limit=1500&from=1578268800&pre=300"
    )
    assert status == 200 and body["candles"][0]["time"] == 1578268800
    assert candle_port.calls == [("from", "jp225_tick", "1D", 1578268800, 300, 1500)]


def test_candles_with_non_int_from_is_a_validation_error(ctx):
    base, _ = ctx
    with pytest.raises(HTTPError) as ei:
        _get(base, "/candles?datasetRef=jp225_tick&timeframe=1D&from=abc")
    assert ei.value.code == 400
    assert json.loads(ei.value.read())["error"]["type"] == "validation"


def test_available_days_returns_ascending_days(ctx):
    base, _ = ctx
    status, body = _get(base, "/available_days?datasetRef=jp225_tick&timeframe=1D")
    assert status == 200
    assert body == {"ok": True, "days": ["2020-01-03", "2020-01-06"]}


def test_available_days_internal_error_translated(ctx):
    base, _ = ctx
    with pytest.raises(HTTPError) as ei:
        _get(base, "/available_days?datasetRef=boom&timeframe=1D")
    assert ei.value.code == 500
    assert json.loads(ei.value.read())["error"]["type"] == "internal"


def test_available_days_absent_when_no_days_port_is_wired():
    """days_port 未注入なら /available_days は API 経路を持たない（静的配信へフォールバック）。"""
    app = ReplayApp(
        candle_port=_FakeCandlePort(), compute_port=None, window_port=None
    )
    server, t, base = _serve(app)
    try:
        with pytest.raises(HTTPError) as ei:
            _get(base, "/available_days?datasetRef=jp225_tick&timeframe=1D")
        assert ei.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)
