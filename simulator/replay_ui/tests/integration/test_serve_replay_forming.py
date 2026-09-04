"""serve_replay の GET /market_profile_forming エンドポイント（fake forming_port 注入）。

薄殻ルート: クエリ取り出し → app.market_profile_forming → usecase → Port（fake）→ (status, body)。
now は必ずリビール T を透過する（因果＝T 以前のみ・未来リーク防止）。forming_port 未注入時は
ルート自体を持たず静的配信へフォールバック（既存 replay へ非干渉＝回帰ゼロ）。

★この時点で serve_replay に /market_profile_forming ルートと forming_port 注入は未実装（Red）。
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


class _FakeFormingPort:
    """now=T 因果を実証するため、now 以前のみを返す fake（未来リーク検出用）。"""

    def __init__(self):
        self.calls = []

    def forming(self, ref, timeframe, now, base, since, bins, va, barw):
        self.calls.append({"ref": ref, "timeframe": timeframe, "now": now, "base": base,
                           "since": since, "bins": bins, "va": va, "barw": barw})
        if ref != "jp225_tick":
            return 400, {"error": {"type": "validation", "message": f"bad {ref}"}}
        # ticks は now 以前のみ（因果）。formingStart<=now を返す。
        return 200, {
            "ok": True, "formingStart": now - 3600, "ticks": [[now - 100, 1005.0]],
            "baseFine": [0, 0, 0], "baseKmin": 100, "priceMin": 1000.0, "priceMax": 1100.0,
            "nBins": 3, "gridW": 10, "now": now,
        }


def _make_app(forming_port):
    return ReplayApp(
        candle_port=_FakeCandlePort(),
        compute_port=_FakeComputePort(),
        window_port=_FakeWindowPort(),
        is_known_ref=lambda r: r in ("jp225_tick", "jp225_m1"),
        forming_port=forming_port,
    )


def _serve(app):
    server = make_server(app, "127.0.0.1", None)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, t, f"http://127.0.0.1:{port}"


@pytest.fixture
def forming_ctx():
    fp = _FakeFormingPort()
    app = _make_app(fp)
    server, t, base = _serve(app)
    try:
        yield base, fp
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return r.status, json.loads(r.read()), dict(r.headers)


def test_forming_route_returns_base_and_forming_and_no_store(forming_ctx):
    base, fp = forming_ctx
    status, body, headers = _get(
        base, "/market_profile_forming?datasetRef=jp225_tick&timeframe=1h&base=1&now=1704074400")
    assert status == 200
    assert body["ok"] is True
    assert body["formingStart"] == 1704074400 - 3600
    assert body["baseFine"] == [0, 0, 0] and body["nBins"] == 3
    assert headers.get("Cache-Control") == "no-store"


def test_forming_route_threads_now_T_causally_to_port(forming_ctx):
    base, fp = forming_ctx
    _get(base, "/market_profile_forming?datasetRef=jp225_tick&timeframe=1h&base=1&now=1704074400")
    # now=T（リビール時刻）が Port へ透過し、応答 tick/formingStart が now 以前に収まる（因果）。
    assert fp.calls[-1]["now"] == 1704074400
    _, body, _ = _get(
        base, "/market_profile_forming?datasetRef=jp225_tick&timeframe=1h&base=1&now=1704074400")
    assert body["ticks"][-1][0] <= 1704074400
    assert body["formingStart"] <= 1704074400


def test_forming_route_forwards_since_base0_for_incremental_tail(forming_ctx):
    base, fp = forming_ctx
    _get(base, "/market_profile_forming?datasetRef=jp225_tick&timeframe=1h&base=0&since=1704074000&now=1704074400")
    call = fp.calls[-1]
    assert call["base"] == "0"
    assert call["since"] == "1704074000"


def test_forming_route_passes_through_validation_status(forming_ctx):
    base, _ = forming_ctx
    with pytest.raises(HTTPError) as ei:
        _get(base, "/market_profile_forming?datasetRef=notick&timeframe=1h&now=1704074400")
    assert ei.value.code == 400


class _FakeFormingPortFrm:
    """frm（セッション窓下限）を記録する fake（クエリ ?from= の透過検証用）。"""

    def __init__(self):
        self.calls = []

    def forming(self, ref, timeframe, now, base, since, bins, va, barw, frm=None):
        self.calls.append({"now": now, "frm": frm})
        return 200, {"ok": True, "formingStart": now - 3600, "ticks": [], "now": now}


def test_forming_route_threads_from_session_window_query_to_port():
    # Arrange
    fp = _FakeFormingPortFrm()
    app = _make_app(fp)
    server, t, base = _serve(app)
    try:
        # Act: セッション窓 base 下限 ?from=当日始まり をクエリで送る。
        _get(base, "/market_profile_forming?datasetRef=jp225_tick&timeframe=1h"
                   "&base=1&now=1704074400&from=1704067200")
        # Assert: from が Port へ透過する（クエリ由来 str・controller が int 化）。
        assert fp.calls[-1]["frm"] == "1704067200"
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)


def test_forming_route_from_omitted_threads_none_backward_compat():
    # Arrange
    fp = _FakeFormingPortFrm()
    app = _make_app(fp)
    server, t, base = _serve(app)
    try:
        # Act: from を送らない（present-mode 相当）。
        _get(base, "/market_profile_forming?datasetRef=jp225_tick&timeframe=1h&base=1&now=1704074400")
        # Assert: frm=None が透過（従来全期間 base＝後方互換）。
        assert fp.calls[-1]["frm"] is None
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)


def test_forming_route_absent_when_port_not_injected_falls_through_to_static():
    # forming_port 未注入（既存 replay 構成）ではルートを持たず静的配信 404（非干渉・回帰ゼロ）。
    app = ReplayApp(
        candle_port=_FakeCandlePort(), compute_port=_FakeComputePort(),
        window_port=_FakeWindowPort(), is_known_ref=lambda r: True,
    )
    server, t, base = _serve(app)
    try:
        with pytest.raises(HTTPError) as ei:
            _get(base, "/market_profile_forming?datasetRef=jp225_tick&timeframe=1h&now=1")
        assert ei.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)
