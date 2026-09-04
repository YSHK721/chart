"""serve_replay の HTTP エンドポイントテスト（proto do_GET/do_POST 忠実・fake port 注入）。

実データ非依存: fake port で untilTime 経路・mode 分岐・例外翻訳・no-store・静的配信を検証する。
"""
from __future__ import annotations

import json
import threading
import urllib.request
from urllib.error import HTTPError

import pytest

from simulator.replay_ui.framework.serve_replay import ReplayApp, make_server


class _FakeCandlePort:
    def load_candles(self, ref, timeframe, limit):
        if ref == "boom":
            raise RuntimeError("candle failure")
        return [{"time": 0, "open": 1, "high": 2, "low": 0, "close": 1.5}]


class _FakeComputePort:
    def __init__(self):
        self.last = None

    def load_source(self, ref, timeframe):
        if ref == "unknown":
            raise ValueError(f"unknown datasetRef {ref!r}")
        if ref == "oom":
            raise MemoryError()
        return [
            {"time": 0, "close": 1.0},
            {"time": 60, "close": 2.0},
            {"time": 120, "close": 3.0},
        ]

    def compute(self, indicator, variant, mode, bars, params):
        self.last = {"mode": mode, "bars": bars, "variant": variant, "params": params}
        return [{"name": "S", "kind": "line", "data": [b["time"] for b in bars]}]


class _FakeWindowPort:
    def load_m1_rows(self, ref, start, end):
        return [[1.0, 2.0, 0.5, 1.5]]

    def load_raw_ticks(self, start, end):
        # ISSUE-031: Port は生ティック (sec, bid, ask) を運ぶ。mid=(bid+ask)/2 は usecase が計算する。
        return [(10, 99.5, 100.5), (20, 100.5, 101.5)]


@pytest.fixture
def server_ctx():
    compute_port = _FakeComputePort()
    app = ReplayApp(
        candle_port=_FakeCandlePort(),
        compute_port=compute_port,
        window_port=_FakeWindowPort(),
        is_known_ref=lambda r: r in ("jp225_m1", "known"),
    )
    server = make_server(app, "127.0.0.1", None)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}", compute_port
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return r.status, json.loads(r.read()), dict(r.headers)


def _post(base, path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read()), dict(r.headers)


def test_candles_ok_and_no_store(server_ctx):
    base, _ = server_ctx
    status, body, headers = _get(base, "/candles?datasetRef=jp225_tick&timeframe=1D&limit=10")
    assert status == 200
    assert body["ok"] is True and len(body["candles"]) == 1
    assert headers.get("Cache-Control") == "no-store"


def test_candles_internal_error_translated(server_ctx):
    # ISSUE-091 A2: internal は正典 ERROR_STATUS（api_shared.http_contract）どおり 500・nested 形（ok/generation 付き）。
    base, _ = server_ctx
    with pytest.raises(HTTPError) as ei:
        _get(base, "/candles?datasetRef=boom")
    assert ei.value.code == 500
    err = json.loads(ei.value.read())
    assert err["error"]["type"] == "internal"
    assert err["ok"] is False and "violations" in err["error"]


def test_compute_until_time_and_full_mode(server_ctx):
    base, compute_port = server_ctx
    status, body, _ = _post(base, "/compute", {
        "indicatorId": "moving_averages", "datasetRef": "jp225_tick",
        "timeframe": "1D", "untilTime": 60, "generation": 7,
    })
    assert status == 200 and body["generation"] == 7
    # untilTime=60 で truncate → full 計算。
    assert compute_port.last["mode"] == "full"
    assert [b["time"] for b in compute_port.last["bars"]] == [0, 60]


def test_compute_latest_mode_applies_forming(server_ctx):
    base, compute_port = server_ctx
    _post(base, "/compute", {
        "indicatorId": "moving_averages", "datasetRef": "jp225_tick",
        "timeframe": "1D", "mode": "latest",
        "forming": {"time": 120, "open": 3.0, "high": 9.0, "low": 1.0, "close": 8.0},
    })
    assert compute_port.last["mode"] == "latest"
    assert compute_port.last["bars"][-1]["close"] == 8.0


def test_compute_unknown_ref_is_validation_error(server_ctx):
    base, _ = server_ctx
    with pytest.raises(HTTPError) as ei:
        _post(base, "/compute", {"indicatorId": "x", "datasetRef": "unknown"})
    assert ei.value.code == 400
    err = json.loads(ei.value.read())
    assert err["error"]["type"] == "validation"


def test_compute_memory_error_is_internal(server_ctx):
    # ISSUE-091 A2: internal は正典 ERROR_STATUS（api_shared.http_contract）どおり 500・nested 形（ok/generation 付き）。
    base, _ = server_ctx
    with pytest.raises(HTTPError) as ei:
        _post(base, "/compute", {"indicatorId": "x", "datasetRef": "oom"})
    assert ei.value.code == 500
    err = json.loads(ei.value.read())
    assert err["error"]["type"] == "internal"
    assert err["error"]["message"] == "memory limit"
    assert err["ok"] is False and "generation" in err


def test_intraday_real_ticks_returns_mids(server_ctx):
    base, _ = server_ctx
    status, body, _ = _get(base, "/intraday?datasetRef=jp225_tick&start=0&end=60&mode=real_ticks")
    assert status == 200
    assert body["m1"] == [[1.0, 2.0, 0.5, 1.5]]
    assert body["ticks"] == [100.0, 101.0]


def test_intraday_non_real_ticks_skips_ticks(server_ctx):
    base, _ = server_ctx
    _, body, _ = _get(base, "/intraday?datasetRef=jp225_tick&start=0&end=60&mode=ohlc_1min")
    assert body["ticks"] == []


def test_intraday_missing_start_end_validation(server_ctx):
    base, _ = server_ctx
    with pytest.raises(HTTPError) as ei:
        _get(base, "/intraday?datasetRef=jp225_tick")
    assert ei.value.code == 400
    assert json.loads(ei.value.read())["error"]["type"] == "validation"


def test_intraday_unknown_non_tick_ref_validation(server_ctx):
    base, _ = server_ctx
    with pytest.raises(HTTPError) as ei:
        _get(base, "/intraday?datasetRef=nope&start=0&end=60")
    assert ei.value.code == 400
    assert json.loads(ei.value.read())["error"]["type"] == "validation"


def test_post_non_compute_path_404(server_ctx):
    base, _ = server_ctx
    with pytest.raises(HTTPError) as ei:
        _post(base, "/other", {})
    assert ei.value.code == 404
