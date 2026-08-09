"""serve_replay の /catalog ルート検証（fake port 注入・実データ非依存）。

ISSUE-278 #8/#4: standalone replay は ``GET /catalog`` を持たず、front も ``catalog.load()`` を
呼んでいなかった。そのため front は variant が受理しない param まで送っており、ライブ側 back が
無言で捨てることで無症状になっていた。無言破棄の撤去（#8）で、この欠落は ``validation``
エラーとして表面化する。ルートの在席と、応答がライブ controller の戻りそのままであることを固定する。

Port 未注入時はルートを持たず静的配信へフォールバックする（既存 replay へ非干渉）ことも併せて固定する。
"""
from __future__ import annotations

import json
import threading
import urllib.request
from urllib.error import HTTPError

import pytest

from simulator.replay_ui.framework.serve_replay import ReplayApp, make_server
from simulator.replay_ui.tests.integration._fake_ports import (  # noqa: E402
    FakeCandlePort as _FakeCandlePort,
    FakeComputePort as _FakeComputePort,
    FakeWindowPort as _FakeWindowPort,
)


class _FakeCatalogPort:
    """ライブ controller の戻り（status, body）を模す。呼出回数を記録する。"""

    def __init__(self, ret=None, boom=False):
        self.calls = 0
        self.boom = boom
        self.ret = ret or (200, {
            "ok": True,
            "catalog": {"profit_band": {"probabilities": [0.95], "timeframe": "chart"}},
            "paramScopes": {"profit_band": {
                "global": ["probabilities", "require_full", "timeframe"],
                "robust": ["probabilities", "normalize", "timeframe"],
            }},
        })

    def catalog(self):
        self.calls += 1
        if self.boom:
            raise RuntimeError("catalog failure")
        return self.ret


def _serve(catalog_port):
    app = ReplayApp(
        candle_port=_FakeCandlePort(),
        compute_port=_FakeComputePort(),
        window_port=_FakeWindowPort(),
        catalog_port=catalog_port,
    )
    server = make_server(app, "127.0.0.1", None)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return f"http://127.0.0.1:{server.server_address[1]}", server, t


@pytest.fixture
def server_ctx():
    port = _FakeCatalogPort()
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


def test_serves_param_defaults_and_variant_scopes(server_ctx):
    base, port = server_ctx
    status, body, headers = _get(base, "/catalog")
    assert status == 200
    assert body["ok"] is True
    assert body["catalog"]["profit_band"]["timeframe"] == "chart"
    # variant ごとの受理 param が届く＝front が表示・送信を絞れる（#8 の前提）。
    assert body["paramScopes"]["profit_band"]["global"] == [
        "probabilities", "require_full", "timeframe"
    ]
    assert headers.get("Cache-Control") == "no-store"
    assert port.calls == 1


def test_unexpected_error_is_translated_to_internal_500():
    base, server, t = _serve(_FakeCatalogPort(boom=True))
    try:
        with pytest.raises(HTTPError) as ei:
            _get(base, "/catalog")
        assert ei.value.code == 500
        assert json.loads(ei.value.read())["error"]["type"] == "internal"
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)


def test_route_is_absent_when_port_is_not_injected():
    """Port 未注入 = ルート無し（静的配信へフォールバック）＝既存 replay へ非干渉。"""
    base, server, t = _serve(None)
    try:
        with pytest.raises(HTTPError) as ei:
            _get(base, "/catalog")
        assert ei.value.code == 404  # web_dir 未設定＝静的配信も無いので 404
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)


def test_composition_root_injects_a_catalog_port():
    """本番結線（build_replay_app）が /catalog を有効にする（結線漏れの検出）。

    ISSUE-277 と同型の「実配信ページだけ取り残す」再発を防ぐため、Port の在席を本番結線で固定する。
    """
    from simulator.replay_ui.main.composition_root import build_replay_app

    app = build_replay_app()
    assert app.catalog_enabled is True
