"""serve_replay の /tickvol_profile ルート検証（fake port 注入・実データ非依存）。

取引密度ハイライト（時刻帯の背景色）の帯定義を配信する。Port 未注入時はルートを持たず静的配信へ
フォールバックする（既存 replay へ非干渉＝回帰ゼロ）ことも併せて固定する。
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
        return []


class _FakeComputePort:
    def load_source(self, ref, timeframe):
        return []

    def compute(self, indicator, variant, mode, bars, params):
        return []


class _FakeWindowPort:
    def load_m1_rows(self, ref, start, end):
        return []

    def load_raw_ticks(self, start, end):
        return []


class _FakeTickvolPort:
    """(ref, sessions, pct, until) を記録し、固定の (status, body) を返す。"""

    def __init__(self, ret=None):
        self.calls = []
        self.ret = ret or (200, {"ok": True, "binSec": 900, "sessions": 20,
                                 "bands": [{"startOff": 10800, "endOff": 19800}],
                                 "bins": [], "threshold": 1.0})

    def profile(self, ref, sessions=None, pct=None, until=None):
        self.calls.append({"ref": ref, "sessions": sessions, "pct": pct, "until": until})
        if ref == "boom":
            raise RuntimeError("profile failure")
        if ref == "unknown":
            raise ValueError("unknown datasetRef 'unknown'")
        return self.ret


def _serve(tickvol_port):
    app = ReplayApp(
        candle_port=_FakeCandlePort(),
        compute_port=_FakeComputePort(),
        window_port=_FakeWindowPort(),
        tickvol_profile_port=tickvol_port,
    )
    server = make_server(app, "127.0.0.1", None)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return f"http://127.0.0.1:{server.server_address[1]}", server, t


@pytest.fixture
def server_ctx():
    port = _FakeTickvolPort()
    base, server, t = _serve(port)
    try:
        yield base, port
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return r.status, json.loads(r.read()), dict(r.headers)


def test_returns_bands_and_threads_query_params_to_the_port(server_ctx):
    base, port = server_ctx
    status, body, headers = _get(
        base, "/tickvol_profile?datasetRef=jp225_tick&sessions=20&pct=75&until=1785542400"
    )
    assert status == 200
    assert body["ok"] is True
    assert body["bands"] == [{"startOff": 10800, "endOff": 19800}]
    assert headers.get("Cache-Control") == "no-store"
    # until はリビール T（単一時計）＝文字列のまま Port へ透過し、controller が数値解釈する。
    assert port.calls == [
        {"ref": "jp225_tick", "sessions": "20", "pct": "75", "until": "1785542400"}
    ]


def test_optional_params_absent_are_passed_as_none(server_ctx):
    base, port = server_ctx
    _get(base, "/tickvol_profile?datasetRef=jp225_tick")
    assert port.calls[0] == {"ref": "jp225_tick", "sessions": None, "pct": None, "until": None}


def test_value_error_is_translated_to_validation_400(server_ctx):
    base, _ = server_ctx
    with pytest.raises(HTTPError) as ei:
        _get(base, "/tickvol_profile?datasetRef=unknown")
    assert ei.value.code == 400
    assert json.loads(ei.value.read())["error"]["type"] == "validation"


def test_unexpected_error_is_translated_to_internal_500(server_ctx):
    base, _ = server_ctx
    with pytest.raises(HTTPError) as ei:
        _get(base, "/tickvol_profile?datasetRef=boom")
    assert ei.value.code == 500
    assert json.loads(ei.value.read())["error"]["type"] == "internal"


def test_route_is_absent_when_port_is_not_injected():
    """Port 未注入 = ルート無し（静的配信へフォールバック）＝既存 replay へ非干渉。"""
    base, server, t = _serve(None)
    try:
        with pytest.raises(HTTPError) as ei:
            _get(base, "/tickvol_profile?datasetRef=jp225_tick")
        assert ei.value.code == 404  # web_dir 未設定＝静的配信も無いので 404
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)
