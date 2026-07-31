"""serve_replay の GET /market_profile エンドポイント（fake market_profile_port 注入）。

薄殻ルート: クエリ取り出し → app.market_profile → usecase → Port（fake）→ (status, body)。
to は必ずリビール T を透過する（因果＝as-seen-at-t＝time<=to のみ・未来リーク防止）。
market_profile_port 未注入時はルート自体を持たず静的配信へフォールバック（既存 replay へ非干渉＝回帰ゼロ）。

★この時点で serve_replay に /market_profile ルートと market_profile_port 注入は未実装（Red）。
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

    def load_raw_ticks(self, start, end):   # ISSUE-031: 生ティック (sec, bid, ask)
        return []


class _FakeProfilePort:
    """to=T 因果を実証するため、to 以前のみを集計する fake（未来リーク検出用）。"""

    def __init__(self):
        self.calls = []

    def profile(self, ref, timeframe, limit, bins, va, src, barw, to,
                frm=None, today=None, sessions=None):
        self.calls.append({"ref": ref, "timeframe": timeframe, "limit": limit,
                           "bins": bins, "va": va, "src": src, "barw": barw, "to": to,
                           "frm": frm, "today": today, "sessions": sessions})
        if ref != "jp225_tick":
            return 400, {"ok": False, "error": {"type": "validation", "message": f"bad {ref}"}}
        # ticklive×{1W,1M} は forming 非対応だが、本 as-of-cursor 経路（candle resample）は全 TF 成立。
        return 200, {
            "ok": True,
            "profile": {"bins": [{"price": 1000.0, "tpo": 3, "norm": 1.0}],
                        "poc": 1000.0, "va_low": 995.0, "va_high": 1005.0,
                        "price_min": 990.0, "price_max": 1010.0, "tpo_units": 3, "n_bins": 3},
            "src": src or "candle", "atom": "足レンジ", "bar_width": 6.67,
        }


def _make_app(profile_port, **kw):
    return ReplayApp(
        candle_port=_FakeCandlePort(),
        compute_port=_FakeComputePort(),
        window_port=_FakeWindowPort(),
        is_known_ref=lambda r: r in ("jp225_tick", "jp225_m1"),
        market_profile_port=profile_port,
        **kw,
    )


def _serve(app):
    server = make_server(app, "127.0.0.1", None)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, t, f"http://127.0.0.1:{port}"


@pytest.fixture
def mp_ctx():
    fp = _FakeProfilePort()
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


def test_market_profile_route_returns_profile_and_no_store(mp_ctx):
    base, fp = mp_ctx
    status, body, headers = _get(
        base, "/market_profile?datasetRef=jp225_tick&timeframe=1h&bins=60&to=1704074400")
    assert status == 200
    assert body["ok"] is True
    assert body["profile"]["poc"] == 1000.0
    assert headers.get("Cache-Control") == "no-store"


def test_market_profile_route_threads_to_T_as_seen_at_t(mp_ctx):
    base, fp = mp_ctx
    _get(base, "/market_profile?datasetRef=jp225_tick&timeframe=1h&to=1704074400")
    # to=T（as-seen-at-t）が Port へ透過している（因果・未来リーク防止）。
    assert fp.calls[-1]["to"] == "1704074400"


def test_market_profile_route_forwards_bins_va_src_barw(mp_ctx):
    base, fp = mp_ctx
    _get(base, "/market_profile?datasetRef=jp225_tick&timeframe=1D&bins=100&va=0.8&src=dwell&barw=5")
    call = fp.calls[-1]
    assert call["bins"] == "100"
    assert call["va"] == "0.8"
    assert call["src"] == "dwell"
    assert call["barw"] == "5"


def test_market_profile_route_forwards_from_today_sessions(mp_ctx):
    base, fp = mp_ctx
    _get(base, "/market_profile?datasetRef=jp225_tick&timeframe=1h&from=1704067200&today=1&sessions=1")
    call = fp.calls[-1]
    assert call["frm"] == "1704067200"
    assert call["today"] == "1"
    assert call["sessions"] == "1"


def test_market_profile_route_all_tf_works_including_1W_1M(mp_ctx):
    # normal/sessions/replay = as-of-cursor（candle resample）は全 TF 成立（ticklive×{1W,1M} 非対応の代替）。
    base, fp = mp_ctx
    for tf in ("1m", "5m", "1h", "1D", "1W", "1M"):
        status, body, _ = _get(
            base, f"/market_profile?datasetRef=jp225_tick&timeframe={tf}&to=1704074400")
        assert status == 200, tf
        assert body["ok"] is True, tf


def test_market_profile_route_passes_through_validation_status(mp_ctx):
    base, _ = mp_ctx
    with pytest.raises(HTTPError) as ei:
        _get(base, "/market_profile?datasetRef=notick&timeframe=1h")
    assert ei.value.code == 400


def test_market_profile_route_absent_when_port_not_injected_falls_through_to_static():
    # market_profile_port 未注入（既存 replay 構成）ではルートを持たず静的配信 404（非干渉・回帰ゼロ）。
    app = ReplayApp(
        candle_port=_FakeCandlePort(), compute_port=_FakeComputePort(),
        window_port=_FakeWindowPort(), is_known_ref=lambda r: True,
    )
    server, t, base = _serve(app)
    try:
        with pytest.raises(HTTPError) as ei:
            _get(base, "/market_profile?datasetRef=jp225_tick&timeframe=1h")
        assert ei.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)
