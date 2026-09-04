"""ISSUE-380: 捨てられる計算の除去（段階 1: 切断検知投棄・段階 2: 実行中計算への合流）。

背景（実測・ISSUE-380）: クライアントが 30 秒タイムアウト・リロードで要求を破棄しても、
サーバはキューに残った計算を無期限に消化し続ける（CLOSE_WAIT 88 本・軽量指標でも 60 秒無応答）。
流入 > 処理が恒常化すると新規 /compute が事実上永久に応答しない。

本テストが固定する不変条件:
  段階 1（_compute_unless_abandoned / _make_client_gone_probe）
    - probe は接続生存中 False・切断（EOF/CLOSE_WAIT・fd 破棄）で True。一切ブロックしない。
    - 全依頼元が切断済みなら計算関数は **呼ばれずに** _ABANDONED が返る。
    - 1 本でも生存していれば計算する（誤投棄しない側へ倒す）。
  段階 2（_run_coalesced）
    - 同一 key の並行要求は owner 1 回の計算に合流し、全員が同じ結果を受け取る。
    - 完了と同時に登録が外れる（キャッシュではない＝次の同一要求は再計算）。
    - スナップショット後に合流した生存依頼元が投棄判定へ巻き込まれた場合、新エントリで
      再実行され応答を失わない。
  HTTP 実経路
    - 切断済みクライアントの /compute はワーカー実行時点で投棄される（handle_compute 不呼出）。
    - 同一ボディの並行 /compute は 1 回だけ計算され、両クライアントへ 200 が返る。

様式は ``test_server_smoke.py``（エフェメラルポート・stdlib のみ）を踏襲する。
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import framework.server as server_mod
from framework.server import IndicatorUIRequestHandler


# --------------------------------------------------------------------------- #
# 共有ヘルパ
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _clean_inflight():
    """各テストの前後で合流登録が空であることを保証する（テスト間の汚染防止）。"""
    assert not server_mod._INFLIGHT, f"前テストの残骸: {list(server_mod._INFLIGHT)}"
    yield
    deadline = time.time() + 5
    while server_mod._INFLIGHT and time.time() < deadline:
        time.sleep(0.01)
    assert not server_mod._INFLIGHT, f"合流登録が完了後も残っている: {list(server_mod._INFLIGHT)}"


@pytest.fixture()
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), IndicatorUIRequestHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _post_json(base: str, body: dict, timeout: float = 30):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        base + "/compute", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _wait_until(cond, timeout: float = 5) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# --------------------------------------------------------------------------- #
# 段階 1: probe と投棄判定（単体）
# --------------------------------------------------------------------------- #
def test_probe_is_false_while_peer_is_connected_and_true_after_close():
    a, b = socket.socketpair()
    try:
        probe = server_mod._make_client_gone_probe(a)
        assert probe() is False  # 生存中（データ無し）は切断扱いにしない
        b.close()
        assert probe() is True  # EOF（CLOSE_WAIT 相当）で切断検知
    finally:
        a.close()


def test_probe_treats_destroyed_socket_as_gone():
    a, b = socket.socketpair()
    probe = server_mod._make_client_gone_probe(a)
    a.close()
    b.close()
    assert probe() is True  # fd 破棄済み（OSError/ValueError）も切断扱い


def test_abandoned_when_all_clients_gone_fn_is_never_called():
    calls = []
    result = server_mod._compute_unless_abandoned(
        [lambda: True, lambda: True], lambda: calls.append(1) or ("ok", {}),
    )
    assert result is server_mod._ABANDONED
    assert calls == []  # 計算せず投棄＝仕事の量が実際に減る


def test_one_alive_client_keeps_the_computation():
    result = server_mod._compute_unless_abandoned(
        [lambda: True, lambda: False], lambda: ("ok", {"n": 1}),
    )
    assert result == ("ok", {"n": 1})


# --------------------------------------------------------------------------- #
# 段階 2: 合流（単体）
# --------------------------------------------------------------------------- #
def test_identical_keys_coalesce_into_one_computation():
    gate = threading.Event()
    calls = []
    results = []

    def fn():
        calls.append(threading.get_ident())
        assert gate.wait(10)
        return 200, {"ok": True}

    def run():
        results.append(
            server_mod._run_coalesced("k", lambda: False, lambda f: f(), fn)
        )

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    # 3 本全員が合流（owner 1 + 合流 2）してから計算を解放する。
    assert _wait_until(
        lambda: len(server_mod._INFLIGHT.get("k", {}).get("probes", [])) == 3
    ), "3 本の要求が単一エントリへ合流していない"
    gate.set()
    for t in threads:
        t.join(timeout=10)
    assert len(calls) == 1, "同一 key の並行要求が重複計算された"
    assert results == [(200, {"ok": True})] * 3


def test_completion_unregisters_so_next_identical_request_recomputes():
    calls = []

    def fn():
        calls.append(1)
        return 200, {"n": len(calls)}

    r1 = server_mod._run_coalesced("k", lambda: False, lambda f: f(), fn)
    r2 = server_mod._run_coalesced("k", lambda: False, lambda f: f(), fn)
    assert (r1, r2) == ((200, {"n": 1}), (200, {"n": 2}))  # 合流はキャッシュではない
    assert len(calls) == 2


def test_owner_exception_propagates_to_all_joined_requests():
    gate = threading.Event()
    errors = []

    def fn():
        assert gate.wait(10)
        raise RuntimeError("boom")

    def run():
        try:
            server_mod._run_coalesced("k", lambda: False, lambda f: f(), fn)
        except RuntimeError as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    assert _wait_until(
        lambda: len(server_mod._INFLIGHT.get("k", {}).get("probes", [])) == 2
    )
    gate.set()
    for t in threads:
        t.join(timeout=10)
    assert errors == ["boom", "boom"]


def test_live_joiner_swept_into_abandonment_is_recomputed_not_lost():
    """スナップショット後合流の生存依頼元は、投棄判定に巻き込まれても応答を失わない。

    owner（切断済み）の probe 評価を合図に合流者（生存）が同一エントリへ入り、owner の
    dispatch は合流を確認してから登録を外す＝「合流したのに _ABANDONED を受け取る」状況を
    決定論的に作る。合流者は新エントリで再実行され結果を得る。owner の計算関数は呼ばれない。
    """
    snapshot_taken = threading.Event()
    owner_calls = []
    joiner_calls = []
    results = {}

    def gone_probe():
        snapshot_taken.set()  # 投棄判定（スナップショット評価）に入った合図
        return True

    def owner_dispatch(f):
        result = f()  # 全依頼元切断（owner のみ）→ _ABANDONED
        probes = f.args[0]  # _compute_unless_abandoned へ渡した合流 probe リスト
        assert _wait_until(lambda: len(probes) == 2), "合流者が旧エントリへ入る前に登録が外れた"
        return result

    def owner():
        results["owner"] = server_mod._run_coalesced(
            "k", gone_probe, owner_dispatch,
            lambda: owner_calls.append(1) or ("never", {}),
        )

    def joiner():
        assert snapshot_taken.wait(10)
        results["joiner"] = server_mod._run_coalesced(
            "k", lambda: False, lambda f: f(),
            lambda: joiner_calls.append(1) or (200, {"ok": True}),
        )

    threads = [threading.Thread(target=owner), threading.Thread(target=joiner)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert results["owner"] is server_mod._ABANDONED  # 切断済み owner へは応答不要
    assert results["joiner"] == (200, {"ok": True})  # 生存合流者は再実行で結果を得る
    assert owner_calls == [] and joiner_calls == [1]


# --------------------------------------------------------------------------- #
# HTTP 実経路（統合）
# --------------------------------------------------------------------------- #
def test_disconnected_client_compute_is_discarded_before_execution(server, monkeypatch):
    """切断済みクライアントの /compute はワーカー実行時点で投棄される（段階 1 の本体）。

    専用ワーカーを先行タスクで塞いだ状態で要求を送って即切断し、解放後にその計算が
    実行されなかった（handle_compute 不呼出）ことを、後続の生存要求の完了で確認する。
    """
    calls = []

    def stub(body):
        calls.append(body)
        return 200, {"ok": True, "series": []}

    monkeypatch.setattr(server_mod, "handle_compute", stub)
    monkeypatch.setattr(server_mod, "requires_dedicated_worker", lambda _id: True)

    occupied = threading.Event()
    gate = threading.Event()
    blocker = threading.Thread(
        target=lambda: server_mod._COMPUTE_WORKER.run(
            lambda: (occupied.set(), gate.wait(10))
        ),
        daemon=True,
    )
    blocker.start()
    assert occupied.wait(5)

    # 要求を送り切ってから即切断（ブラウザの 30 秒タイムアウト相当）。
    host, port = server.rsplit(":", 1)[0].replace("http://", ""), int(server.rsplit(":", 1)[1])
    body = json.dumps({"indicatorId": "x", "params": {}}).encode("utf-8")
    sock = socket.create_connection((host, port), timeout=5)
    sock.sendall(
        b"POST /compute HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
    )
    # 要求がワーカーキューへ到達してから切断・解放する（順序の決定論化）。
    assert _wait_until(lambda: server_mod._COMPUTE_WORKER._q.qsize() >= 1)
    sock.close()
    gate.set()
    blocker.join(timeout=5)

    # 生存クライアントの後続要求は完了する＝キューは前進し、切断分は計算されていない。
    status, payload = _post_json(server, {"indicatorId": "y", "params": {}})
    assert status == 200 and payload["ok"] is True
    assert calls == [{"indicatorId": "y", "params": {}}], (
        f"切断済み要求が計算されている（捨てられる計算が残存・ISSUE-380）: {calls}"
    )


def test_identical_concurrent_computes_are_coalesced_over_http(server, monkeypatch):
    """同一ボディの並行 /compute は 1 回だけ計算され、両クライアントへ同一の 200 が返る。"""
    gate = threading.Event()
    calls = []

    def stub(body):
        calls.append(body)
        assert gate.wait(10)
        return 200, {"ok": True, "series": []}

    monkeypatch.setattr(server_mod, "handle_compute", stub)
    monkeypatch.setattr(server_mod, "requires_dedicated_worker", lambda _id: False)

    body = {"indicatorId": "x", "params": {"window": 5}}
    key = "compute:" + json.dumps(body, sort_keys=True, ensure_ascii=False)
    results = []

    def fire():
        results.append(_post_json(server, body))

    threads = [threading.Thread(target=fire) for _ in range(2)]
    for t in threads:
        t.start()
    assert _wait_until(
        lambda: len(server_mod._INFLIGHT.get(key, {}).get("probes", [])) == 2
    ), "2 本目の同一要求が実行中計算へ合流していない"
    gate.set()
    for t in threads:
        t.join(timeout=15)

    assert len(calls) == 1, f"同一ボディが重複計算された: {len(calls)} 回"
    assert results == [(200, {"ok": True, "series": []})] * 2
