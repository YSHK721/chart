"""ISSUE-097 🟡-4 回帰ガード — serve_replay 全ハンドラの例外→HTTP 契約統一。

各ハンドラに個別コピーされていた例外分類を中央翻訳器へ集約したことの回帰ガード。特に
/market_profile・/market_profile_forming に欠落していた ``ValueError→validation`` 分岐の是正
（旧: ValueError が internal/500 に落ちる不整合）を正典契約（validation/400・nested 形）へ固定する。

契約: どの API ハンドラでも例外は
    ValueError            → validation / 400
    MemoryError・その他    → internal   / 500
へ翻訳され、ボディは nested 形 {ok:false, generation, error:{type, message, violations:[]}} で一致する。
/intraday は usecase が source 例外を *_error フィールドへ翻訳する（app-level except へは伝播しない）
ため、到達可能な except 経路＝is_known_ref の ValueError（validation/400）のみを固定する。
"""
from __future__ import annotations

import json
import threading
import urllib.request
from urllib.error import HTTPError

import pytest

from simulator.replay_ui.framework.serve_replay import ReplayApp, make_server


class _NoopCandlePort:
    def load_candles(self, ref, timeframe, limit):
        return []


class _NoopComputePort:
    def load_source(self, ref, timeframe):
        return []

    def compute(self, indicator, variant, mode, bars, params):
        return []


class _NoopWindowPort:
    def load_m1_rows(self, ref, start, end):
        return []

    def load_ticks(self, start, end):
        return []


class _RaisingCandlePort:
    def __init__(self, exc):
        self._exc = exc

    def load_candles(self, ref, timeframe, limit):
        raise self._exc


class _RaisingComputePort:
    def __init__(self, exc):
        self._exc = exc

    def load_source(self, ref, timeframe):
        raise self._exc

    def compute(self, indicator, variant, mode, bars, params):
        raise self._exc


class _RaisingProfilePort:
    def __init__(self, exc):
        self._exc = exc

    def profile(self, ref, timeframe, limit, bins, va, src, barw, to,
                frm=None, today=None, sessions=None):
        raise self._exc


class _RaisingFormingPort:
    def __init__(self, exc):
        self._exc = exc

    def forming(self, ref, timeframe, now, base, since, bins, va, barw, frm=None):
        raise self._exc


def _serve(app):
    server = make_server(app, "127.0.0.1", None)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, t, f"http://127.0.0.1:{port}"


def _get_err(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=5) as r:
            return r.status, json.loads(r.read())
    except HTTPError as e:
        return e.code, json.loads(e.read())


def _post_err(base, path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        base + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except HTTPError as e:
        return e.code, json.loads(e.read())


def _assert_nested(body, error_type):
    assert body["ok"] is False
    assert body["error"]["type"] == error_type
    assert body["error"]["violations"] == []
    assert "message" in body["error"]
    assert "generation" in body


# 各ハンドラの (ValueError→validation/400, その他→internal/500) 対応表。
_CASES = [
    (ValueError("boom"), 400, "validation"),
    (RuntimeError("boom"), 500, "internal"),
]


@pytest.mark.parametrize("exc,code,etype", _CASES)
def test_candles_handler_error_contract(exc, code, etype):
    app = ReplayApp(
        candle_port=_RaisingCandlePort(exc), compute_port=_NoopComputePort(),
        window_port=_NoopWindowPort(), is_known_ref=lambda r: True)
    server, t, base = _serve(app)
    try:
        status, body = _get_err(base, "/candles?datasetRef=x&timeframe=1D")
        assert status == code
        _assert_nested(body, etype)
    finally:
        server.shutdown(); server.server_close(); t.join(timeout=2)


@pytest.mark.parametrize("exc,code,etype", _CASES)
def test_market_profile_handler_error_contract(exc, code, etype):
    # ISSUE-097 🟡-4 是正の中核: 旧実装は ValueError→internal/500 に落ちていた（欠落分岐）。
    app = ReplayApp(
        candle_port=_NoopCandlePort(), compute_port=_NoopComputePort(),
        window_port=_NoopWindowPort(), is_known_ref=lambda r: True,
        market_profile_port=_RaisingProfilePort(exc))
    server, t, base = _serve(app)
    try:
        status, body = _get_err(
            base, "/market_profile?datasetRef=jp225_tick&timeframe=1h")
        assert status == code
        _assert_nested(body, etype)
    finally:
        server.shutdown(); server.server_close(); t.join(timeout=2)


@pytest.mark.parametrize("exc,code,etype", _CASES)
def test_market_profile_forming_handler_error_contract(exc, code, etype):
    # ISSUE-097 🟡-4 是正の中核: 旧実装は ValueError→internal/500 に落ちていた（欠落分岐）。
    app = ReplayApp(
        candle_port=_NoopCandlePort(), compute_port=_NoopComputePort(),
        window_port=_NoopWindowPort(), is_known_ref=lambda r: True,
        forming_port=_RaisingFormingPort(exc))
    server, t, base = _serve(app)
    try:
        status, body = _get_err(
            base, "/market_profile_forming?datasetRef=jp225_tick&timeframe=1h&now=1704074400")
        assert status == code
        _assert_nested(body, etype)
    finally:
        server.shutdown(); server.server_close(); t.join(timeout=2)


@pytest.mark.parametrize("exc,code,etype", _CASES)
def test_compute_handler_error_contract(exc, code, etype):
    app = ReplayApp(
        candle_port=_NoopCandlePort(), compute_port=_RaisingComputePort(exc),
        window_port=_NoopWindowPort(), is_known_ref=lambda r: True)
    server, t, base = _serve(app)
    try:
        status, body = _post_err(
            base, "/compute", {"indicatorId": "x", "datasetRef": "x", "generation": 3})
        assert status == code
        _assert_nested(body, etype)
        assert body["generation"] == 3
    finally:
        server.shutdown(); server.server_close(); t.join(timeout=2)


def test_compute_memory_error_stays_internal_with_stable_message():
    # 中央翻訳後も compute の MemoryError 特別メッセージ "memory limit" は不変（byte 保存）。
    app = ReplayApp(
        candle_port=_NoopCandlePort(), compute_port=_RaisingComputePort(MemoryError()),
        window_port=_NoopWindowPort(), is_known_ref=lambda r: True)
    server, t, base = _serve(app)
    try:
        status, body = _post_err(
            base, "/compute", {"indicatorId": "x", "datasetRef": "x", "generation": 5})
        assert status == 500
        _assert_nested(body, "internal")
        assert body["error"]["message"] == "memory limit"
        assert body["generation"] == 5
    finally:
        server.shutdown(); server.server_close(); t.join(timeout=2)


def test_intraday_known_ref_gate_value_error_is_validation():
    # /intraday の到達可能な except 経路: is_known_ref の ValueError→validation/400。
    app = ReplayApp(
        candle_port=_NoopCandlePort(), compute_port=_NoopComputePort(),
        window_port=_NoopWindowPort(), is_known_ref=lambda r: False)
    server, t, base = _serve(app)
    try:
        status, body = _get_err(base, "/intraday?datasetRef=nope&start=0&end=60")
        assert status == 400
        _assert_nested(body, "validation")
    finally:
        server.shutdown(); server.server_close(); t.join(timeout=2)
