"""Router 契約テスト（Red フェーズ）。

対象: `unified_ui/router.py` の `create_router_server`。
方式: 実 core（8001/8281）に依存させず、http.server で立てたスタブ上流に対して
      ルータのプロキシ／静的配信／隔離契約を検証する。

テスト構造は AAA（Arrange / Act / Assert）。テスト名は
「対象_条件_期待結果」の 3 要素で記述する。

Red フェーズ: router のリクエスト処理は NotImplementedError を送出するため、
全ケースが失敗する（＝実装の不在を実証する失敗テスト）。
"""

from __future__ import annotations

import http.client
import json
import os
import threading
import time
from collections import namedtuple
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import router as router_mod

WEB_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

Recorded = namedtuple("Recorded", ["method", "path", "body", "content_type"])
# `headers` は ISSUE-348 の検定（Cache-Control の確認）で追加した。既存フィールドは
#   位置・意味とも不変で、末尾への加法のみ（既存の分解代入・添字参照を壊さない）。
Response = namedtuple("Response", ["status", "content_type", "body", "error", "headers"])

# スタブ上流が返す固定の応答（透過検証用の識別可能な値）。
_STUB_CONTENT_TYPE = "application/json; charset=utf-8"


def _make_stub_upstream(tag):
    """受信要求を記録し固定応答を返すスタブ上流サーバを構築して返す。

    tag は "live" / "replay" を識別する印。応答 body に埋め込み、
    ルータがどちらの上流へ振り分けたかを検証可能にする。
    """
    records = []

    class StubHandler(BaseHTTPRequestHandler):
        def _record_and_reply(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            records.append(
                Recorded(
                    method=self.command,
                    path=self.path,  # クエリ込みの素パス（prefix 除去後を期待）
                    body=body,
                    content_type=self.headers.get("Content-Type"),
                )
            )
            payload = json.dumps(
                {
                    "tag": tag,
                    "seen_path": self.path,
                    "seen_method": self.command,
                    "seen_body": body.decode("utf-8", "replace"),
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", _STUB_CONTENT_TYPE)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):  # noqa: N802
            self._record_and_reply()

        def do_POST(self):  # noqa: N802
            self._record_and_reply()

        def log_message(self, *a, **k):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    server.records = records
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _make_slow_upstream(delay):
    """`delay` 秒待ってから 200 を返すスタブ上流（重処理＝応答前遅延を模擬）。

    read timeout の挙動検証に使う。ルータが先に切断した場合の write は OSError を握り潰す
    （テストログのノイズ抑制）。
    """

    class SlowHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            time.sleep(delay)
            payload = b'{"ok": true}'
            try:
                self.send_response(200)
                self.send_header("Content-Type", _STUB_CONTENT_TYPE)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except OSError:
                pass  # ルータが read timeout で先に切断済みなら BrokenPipe を無視する。

        def log_message(self, *a, **k):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _base_url(server):
    host, port = server.server_address
    return f"http://{host}:{port}"


def _request(base_server, method, path, body=None, headers=None, timeout=5):
    """ルータへ HTTP 要求を送り (status, content_type, body, error) を返す。

    ルータが応答なしで接続を閉じた場合（NotImplementedError 送出時）は
    status=None・error!=None を返し、assertion が明瞭に失敗するようにする。
    timeout はクライアント（テスト）側の待ち上限。重処理の素通しを検証する場合は広げる。
    """
    host, port = base_server.server_address
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        return Response(resp.status, resp.getheader("Content-Type"), data, None, dict(resp.getheaders()))
    except (
        http.client.RemoteDisconnected,
        http.client.BadStatusLine,
        ConnectionError,
        OSError,
    ) as exc:
        return Response(None, None, b"", repr(exc), {})
    finally:
        conn.close()


@pytest.fixture()
def upstreams():
    live_srv, live_th = _make_stub_upstream("live")
    replay_srv, replay_th = _make_stub_upstream("replay")
    yield live_srv, replay_srv
    live_srv.shutdown()
    replay_srv.shutdown()


@pytest.fixture()
def router(upstreams):
    live_srv, replay_srv = upstreams
    server = router_mod.create_router_server(
        ("127.0.0.1", 0),
        # V-8 裁定: 上流は「モード名 → URL」のマッピングで受ける（per-mode 引数を足さない）。
        upstreams={"live": _base_url(live_srv), "replay": _base_url(replay_srv)},
        web_root=WEB_ROOT,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, live_srv, replay_srv
    server.shutdown()


@pytest.fixture()
def sim_upstream():
    """sim コアのスタブ上流（第 3 モード）。"""
    srv, _ = _make_stub_upstream("sim")
    yield srv
    srv.shutdown()


@pytest.fixture()
def router_with_sim(upstreams, sim_upstream):
    """live / replay / sim の 3 上流を持つルータ（Phase 1 の実構成）。"""
    live_srv, replay_srv = upstreams
    server = router_mod.create_router_server(
        ("127.0.0.1", 0),
        upstreams={
            "live": _base_url(live_srv),
            "replay": _base_url(replay_srv),
            "sim": _base_url(sim_upstream),
        },
        web_root=WEB_ROOT,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, live_srv, replay_srv, sim_upstream
    server.shutdown()


@pytest.fixture()
def dashboard_upstream():
    """dashboard コアのスタブ上流（第 4 モード・ISSUE-452）。"""
    srv, _ = _make_stub_upstream("dashboard")
    yield srv
    srv.shutdown()


@pytest.fixture()
def router_with_dashboard(upstreams, sim_upstream, dashboard_upstream):
    """live / replay / sim / dashboard の 4 上流を持つルータ（ISSUE-452 の実構成）。"""
    live_srv, replay_srv = upstreams
    server = router_mod.create_router_server(
        ("127.0.0.1", 0),
        upstreams={
            "live": _base_url(live_srv),
            "replay": _base_url(replay_srv),
            "sim": _base_url(sim_upstream),
            "dashboard": _base_url(dashboard_upstream),
        },
        web_root=WEB_ROOT,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, live_srv, replay_srv, sim_upstream, dashboard_upstream
    server.shutdown()


# ---- A1: prefix 除去 + query/method 保存 + 透過 -------------------------------

def test_live_candles_get_is_proxied_to_live_upstream_without_prefix(router):
    # Arrange
    server, live_srv, replay_srv = router
    # Act
    resp = _request(server, "GET", "/live/candles?tf=1D")
    # Assert: 透過（200・content-type）と、上流が prefix 除去済みパスを受信したこと
    assert resp.status == 200, f"live candles proxy failed: {resp.error}"
    assert resp.content_type == _STUB_CONTENT_TYPE
    assert len(live_srv.records) == 1, "live 上流が1回受信するはず"
    assert live_srv.records[0].path == "/candles?tf=1D"
    assert live_srv.records[0].method == "GET"
    assert len(replay_srv.records) == 0, "replay 上流は受信しないはず"


# ---- A2: POST body 保存転送 --------------------------------------------------

def test_live_compute_post_forwards_json_body_to_live_upstream(router):
    # Arrange
    server, live_srv, _ = router
    payload = json.dumps({"indicator": "sma", "window": 50}).encode("utf-8")
    # Act
    resp = _request(
        server, "POST", "/live/compute", body=payload,
        headers={"Content-Type": "application/json"},
    )
    # Assert
    assert resp.status == 200, f"live compute proxy failed: {resp.error}"
    assert len(live_srv.records) == 1
    assert live_srv.records[0].path == "/compute"
    assert live_srv.records[0].method == "POST"
    assert live_srv.records[0].body == payload, "POST body が保存転送されるはず"


# ---- A3: replay 系プロキシ ---------------------------------------------------

def test_replay_intraday_get_is_proxied_to_replay_upstream_without_prefix(router):
    # Arrange
    server, live_srv, replay_srv = router
    # Act
    resp = _request(server, "GET", "/replay/intraday?date=2025-08-26&tf=1m")
    # Assert
    assert resp.status == 200, f"replay intraday proxy failed: {resp.error}"
    assert len(replay_srv.records) == 1
    assert replay_srv.records[0].path == "/intraday?date=2025-08-26&tf=1m"
    assert len(live_srv.records) == 0, "live 上流は受信しないはず"


# ---- A4: prefix 除去正当性（二重 slash 無し） --------------------------------

def test_live_market_profile_forming_prefix_stripped_without_double_slash(router):
    # Arrange
    server, live_srv, _ = router
    # Act
    resp = _request(server, "GET", "/live/market_profile_forming")
    # Assert: `/live` 除去後は `/market_profile_forming`（`//` を生じない）
    assert resp.status == 200, f"proxy failed: {resp.error}"
    assert live_srv.records[0].path == "/market_profile_forming"
    assert "//" not in live_srv.records[0].path


# ---- A5: ルート静的配信 ------------------------------------------------------

def test_root_get_serves_unified_index_html_as_text_html(router):
    # Arrange
    server, _, _ = router
    # Act
    resp = _request(server, "GET", "/")
    # Assert
    assert resp.status == 200, f"index serve failed: {resp.error}"
    assert resp.content_type is not None and resp.content_type.startswith("text/html")
    assert b"<!doctype html>" in resp.body.lower() or b"<html" in resp.body.lower()


# ---- A6: unified web 静的 JS の content-type -------------------------------

@pytest.mark.parametrize(
    "path",
    ["/js/unified_root.js", "/sw.js"],
)
def test_unified_static_js_served_with_javascript_content_type(router, path):
    # Arrange
    server, _, _ = router
    # Act
    resp = _request(server, "GET", path)
    # Assert
    assert resp.status == 200, f"static js serve failed for {path}: {resp.error}"
    assert resp.content_type is not None
    assert "javascript" in resp.content_type, f"unexpected content-type: {resp.content_type}"


# ---- A7: プロセス隔離（replay クラッシュ＝接続拒否時も live 正常・replay は 502） --------

def test_replay_upstream_refused_yields_502_while_live_stays_ok(router):
    # Arrange: replay core クラッシュ＝プロセス消滅＝listen ソケット閉鎖で **接続拒否**になる
    #   現実的な状況を模擬する（ゾンビ listen ではなく connection-refused）。これにより read timeout
    #   を寛容な既定値にしても、接続確立フェーズで即 502 に落ちることを検証できる。
    server, live_srv, replay_srv = router
    replay_srv.shutdown()      # serve ループ停止
    replay_srv.server_close()  # listen ソケットを閉じる＝以後の connect は拒否される
    # Act
    replay_resp = _request(server, "GET", "/replay/candles?tf=1D")
    live_resp = _request(server, "GET", "/live/candles?tf=1D")
    # Assert: replay は 502（接続拒否）、live は無傷で 200（別プロセス隔離）
    assert replay_resp.status == 502, f"expected 502 for refused replay, got {replay_resp.status}/{replay_resp.error}"
    assert live_resp.status == 200, f"live must stay healthy: {live_resp.error}"
    assert len(live_srv.records) == 1


# ---- A7b: 既定 read timeout は重処理（>4s）を 502 化しない（footgun 回帰） ----------

def test_default_read_timeout_does_not_502_slow_upstream():
    # Arrange: read_timeout を **未指定**（＝production と同じ寛容既定）で構築する。旧・既定 4s では
    #   4s 超の重処理（リプレイ全期間ロード等）が 502 化する footgun があった。既定寛容化を behavioral
    #   に実証する（4.3s 遅延＝旧既定 4.0s 超）。クライアント timeout は判定閾値でなくフレーク回避の
    #   ため 30s に広げる（🔵: 4.3s < 30s ＝余裕十分・旧既定 4.0s なら 502 になっていた）。
    slow_srv, _ = _make_slow_upstream(4.3)
    server = router_mod.create_router_server(
        ("127.0.0.1", 0),
        upstreams={"live": _base_url(slow_srv), "replay": _base_url(slow_srv)},
        web_root=WEB_ROOT,
        # read_timeout は敢えて未指定＝既定値の寛容性を検証する（旧 4s なら 502 になっていた）。
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # Act（client timeout を 30s に広げてフレークを排除）
        resp = _request(server, "GET", "/live/candles", timeout=30)
        # Assert: 既定でも重処理は 502 化されず素通しされる
        assert resp.status == 200, (
            f"default read timeout must not 502 a >4s upstream: {resp.status}/{resp.error}"
        )
    finally:
        server.shutdown()
        slow_srv.shutdown()
        slow_srv.server_close()


# ---- A7c: 明示的な短い read_timeout はハング上流を 502 化する（機構の分離検証） ----------

def test_explicit_short_read_timeout_502s_hung_upstream():
    # Arrange: read hang（応答しない）上流に対し、明示的な短い read_timeout を渡すと 502 になる。
    #   既定挙動（寛容）とは分離した、read-timeout 機構そのものの検証。
    hung_srv, _ = _make_slow_upstream(3)  # read_timeout(0.3s) 内には応答しない
    server = router_mod.create_router_server(
        ("127.0.0.1", 0),
        upstreams={"live": _base_url(hung_srv), "replay": _base_url(hung_srv)},
        web_root=WEB_ROOT,
        read_timeout=0.3,  # 明示的な短い read timeout
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # Act
        resp = _request(server, "GET", "/live/candles")
        # Assert: 短い read timeout で応答しない上流は 502
        assert resp.status == 502, (
            f"short read timeout must 502 a hung upstream: {resp.status}/{resp.error}"
        )
    finally:
        server.shutdown()
        hung_srv.shutdown()
        hung_srv.server_close()


# ---- S1〜S5: 第 3 モード `/sim/*`（基本設計書 §4 F-2・§6.1・§8.1 Phase 1）------------
#
# 上流の集合を「モード名 → URL のマッピング」で受ける（§11.1 裁定 6 = V-8）。per-mode の
#   キーワード引数を足していく方式だと、モードを増やすたびに `create_router_server` の
#   シグネチャと CLI と serve.sh を同時に直すことになる（＝拡張点の欠如）。マッピングなら
#   モードの追加は**呼び出し側の 1 エントリ**で済み、router 本体は変わらない。


def test_sim_candles_get_is_proxied_to_sim_upstream_without_prefix(router_with_sim):
    # Arrange
    server, live_srv, replay_srv, sim_srv = router_with_sim
    # Act
    resp = _request(server, "GET", "/sim/candles?tf=1D")
    # Assert: sim 上流だけが prefix 除去済みパスを受信する（live へ誤配されない）。
    assert resp.status == 200, f"sim proxy failed: {resp.error}"
    assert len(sim_srv.records) == 1, "sim 上流が1回受信するはず"
    assert sim_srv.records[0].path == "/candles?tf=1D"
    assert len(live_srv.records) == 0, "live 上流は受信しないはず（誤配の遮断）"
    assert len(replay_srv.records) == 0, "replay 上流は受信しないはず"


def test_sim_post_forwards_body_to_sim_upstream(router_with_sim):
    # Arrange
    server, live_srv, _replay, sim_srv = router_with_sim
    payload = json.dumps({"strategy": "sma_cross"}).encode("utf-8")
    # Act
    resp = _request(
        server, "POST", "/sim/jobs", body=payload,
        headers={"Content-Type": "application/json"},
    )
    # Assert
    assert resp.status == 200, f"sim post proxy failed: {resp.error}"
    assert sim_srv.records[0].path == "/jobs"
    assert sim_srv.records[0].method == "POST"
    assert sim_srv.records[0].body == payload
    assert len(live_srv.records) == 0


def test_sim_prefix_stripped_without_double_slash(router_with_sim):
    # Arrange
    server, _live, _replay, sim_srv = router_with_sim
    # Act: prefix そのもの（末尾スラッシュ無し）
    resp = _request(server, "GET", "/sim")
    # Assert: `/sim` 除去後は `/`（`//` を生じない）
    assert resp.status == 200, f"proxy failed: {resp.error}"
    assert sim_srv.records[0].path == "/"


def test_sim_upstream_refused_yields_502_while_live_and_replay_stay_ok(router_with_sim):
    # Arrange: sim コアのクラッシュ＝プロセス消滅＝listen ソケット閉鎖で接続拒否になる状況。
    #   NFR-02（プロセス隔離）: sim を止めてもライブ・リプレイは無影響でなければならない。
    server, live_srv, replay_srv, sim_srv = router_with_sim
    sim_srv.shutdown()
    sim_srv.server_close()
    # Act
    sim_resp = _request(server, "GET", "/sim/candles?tf=1D")
    live_resp = _request(server, "GET", "/live/candles?tf=1D")
    replay_resp = _request(server, "GET", "/replay/candles?tf=1D")
    # Assert
    assert sim_resp.status == 502, f"expected 502 for refused sim, got {sim_resp.status}/{sim_resp.error}"
    assert live_resp.status == 200, f"live must stay healthy: {live_resp.error}"
    assert replay_resp.status == 200, f"replay must stay healthy: {replay_resp.error}"
    assert len(live_srv.records) == 1
    assert len(replay_srv.records) == 1


def test_sim_prefix_is_not_routed_when_sim_upstream_is_not_registered(router):
    """振り分け対象は**マッピングに載っているモードだけ**（表が唯一源であることの実証）。"""
    # Arrange: `router` fixture は live / replay のみを登録している。
    server, live_srv, replay_srv = router
    # Act
    resp = _request(server, "GET", "/sim/candles")
    # Assert: どの上流へも回さず、静的配信面にも無いので 404。
    assert resp.status == 404, f"expected 404, got {resp.status}/{resp.error}"
    assert len(live_srv.records) == 0, "未登録 prefix が live へ倒れてはならない"
    assert len(replay_srv.records) == 0


def test_router_routes_any_mode_name_present_in_the_upstreams_mapping(upstreams):
    """モード名は router にハードコードされていない（第 4 モードは 1 エントリで足りる）。"""
    # Arrange: live / replay とは無関係な名前の上流を 1 つ登録する。
    live_srv, _replay = upstreams
    future_srv, _ = _make_stub_upstream("future")
    server = router_mod.create_router_server(
        ("127.0.0.1", 0),
        upstreams={"live": _base_url(live_srv), "future": _base_url(future_srv)},
        web_root=WEB_ROOT,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # Act
        resp = _request(server, "GET", "/future/candles?x=1")
        # Assert
        assert resp.status == 200, f"proxy failed: {resp.error}"
        assert future_srv.records[0].path == "/candles?x=1"
        assert len(live_srv.records) == 0
    finally:
        server.shutdown()
        future_srv.shutdown()
        future_srv.server_close()


def test_mode_prefix_boundary_is_strict_for_every_registered_mode(router_with_sim):
    """`/simfoo` のような別語は sim 配下としない（`/sim/` 境界の厳格判定）。"""
    # Arrange
    server, _live, _replay, sim_srv = router_with_sim
    # Act
    resp = _request(server, "GET", "/simfoo")
    # Assert
    assert resp.status == 404, f"expected 404 for /simfoo, got {resp.status}"
    assert len(sim_srv.records) == 0


# ---- A9: 第 4 モード dashboard（ISSUE-452 / 設計書 §4.6・arch-spec §7）------------------
#
# 置き場所の裁定（設計書 §4.6）: 価格ラダーはチャート画面へ置かない。`/live` `/replay` `/sim` と
#   並ぶ 4 つ目のモード `/dashboard` に置く。ルータ側は「マッピングへ 1 エントリ」で足りる
#   （§11.1 裁定 6 = V-8 の拡張点）ことを、sim と同じ 4 観点（透過・prefix 除去・隔離・境界）で固定する。


def test_dashboard_get_is_proxied_to_dashboard_upstream_without_prefix(router_with_dashboard):
    # Arrange
    server, live_srv, _replay, _sim, dash_srv = router_with_dashboard
    # Act
    resp = _request(server, "GET", "/dashboard/reach_sheet?tf=1D")
    # Assert: prefix を除いた素パスが dashboard core へ届く（他 core へは 1 件も行かない）。
    assert resp.status == 200, f"proxy failed: {resp.error}"
    assert dash_srv.records[0].path == "/reach_sheet?tf=1D"
    assert dash_srv.records[0].method == "GET"
    assert len(live_srv.records) == 0


def test_dashboard_post_forwards_body_to_dashboard_upstream(router_with_dashboard):
    # Arrange: 束（instances）は Input Model の一部として POST で送る（arch-spec §0 T-2）。
    server, _live, _replay, _sim, dash_srv = router_with_dashboard
    payload = json.dumps({"instances": [{"indicator_id": "ma_marod"}]}).encode("utf-8")
    # Act
    resp = _request(
        server, "POST", "/dashboard/reach_sheet", body=payload,
        headers={"Content-Type": "application/json"},
    )
    # Assert
    assert resp.status == 200, f"proxy failed: {resp.error}"
    assert dash_srv.records[0].method == "POST"
    assert dash_srv.records[0].body == payload
    assert dash_srv.records[0].content_type == "application/json"


def test_dashboard_prefix_stripped_without_double_slash(router_with_dashboard):
    # Arrange
    server, _live, _replay, _sim, dash_srv = router_with_dashboard
    # Act: prefix そのもの（末尾スラッシュ無し）
    resp = _request(server, "GET", "/dashboard")
    # Assert: `/dashboard` 除去後は `/`（`//` を生じない）
    assert resp.status == 200, f"proxy failed: {resp.error}"
    assert dash_srv.records[0].path == "/"


def test_dashboard_upstream_refused_yields_502_while_the_other_modes_stay_ok(router_with_dashboard):
    # Arrange: dashboard core の消滅（NFR-02 プロセス隔離）。既存 3 モードは無影響でなければならない。
    server, live_srv, replay_srv, sim_srv, dash_srv = router_with_dashboard
    dash_srv.shutdown()
    dash_srv.server_close()
    # Act
    dash_resp = _request(server, "GET", "/dashboard/reach_sheet")
    live_resp = _request(server, "GET", "/live/candles?tf=1D")
    replay_resp = _request(server, "GET", "/replay/candles?tf=1D")
    sim_resp = _request(server, "GET", "/sim/")
    # Assert
    assert dash_resp.status == 502, f"expected 502, got {dash_resp.status}/{dash_resp.error}"
    assert live_resp.status == 200, f"live must stay healthy: {live_resp.error}"
    assert replay_resp.status == 200, f"replay must stay healthy: {replay_resp.error}"
    assert sim_resp.status == 200, f"sim must stay healthy: {sim_resp.error}"
    assert len(live_srv.records) == 1
    assert len(replay_srv.records) == 1
    assert len(sim_srv.records) == 1


def test_dashboard_prefix_is_not_routed_when_dashboard_upstream_is_not_registered(router_with_sim):
    """未登録なら誤配せず 404（既存 3 モード構成のルータは dashboard を知らない）。"""
    # Arrange
    server, live_srv, replay_srv, sim_srv = router_with_sim
    # Act
    resp = _request(server, "GET", "/dashboard/reach_sheet")
    # Assert
    assert resp.status == 404, f"expected 404, got {resp.status}/{resp.error}"
    assert len(live_srv.records) == 0
    assert len(replay_srv.records) == 0
    assert len(sim_srv.records) == 0


def test_dashboardfoo_is_not_treated_as_dashboard_prefix(router_with_dashboard):
    """`/dashboardfoo` のような別語は dashboard 配下としない（`/dashboard/` 境界の厳格判定）。"""
    # Arrange
    server, _live, _replay, _sim, dash_srv = router_with_dashboard
    # Act
    resp = _request(server, "GET", "/dashboardfoo")
    # Assert
    assert resp.status == 404, f"expected 404 for /dashboardfoo, got {resp.status}"
    assert len(dash_srv.records) == 0


# ---- S6: CLI の繰り返し指定 `--upstream <mode>=<url>`（§11.1 裁定 6）------------------


def test_parse_upstream_args_builds_a_mode_to_url_mapping():
    # Arrange
    argv = ["live=http://127.0.0.1:8001", "replay=http://127.0.0.1:8281", "sim=http://127.0.0.1:8381"]
    # Act
    got = router_mod.parse_upstream_args(argv)
    # Assert
    assert got == {
        "live": "http://127.0.0.1:8001",
        "replay": "http://127.0.0.1:8281",
        "sim": "http://127.0.0.1:8381",
    }


def test_parse_upstream_args_keeps_the_url_scheme_separator_intact():
    """`=` は最初の 1 個だけを区切りにする（URL 中の `=` を壊さない）。"""
    # Arrange / Act
    got = router_mod.parse_upstream_args(["sim=http://127.0.0.1:8381/?a=b"])
    # Assert
    assert got == {"sim": "http://127.0.0.1:8381/?a=b"}


@pytest.mark.parametrize("bad", ["live", "=http://x", "live="])
def test_parse_upstream_args_rejects_malformed_entries(bad):
    """形が違う指定は黙って無視せず落とす（無言で上流が欠けるのを防ぐ）。"""
    with pytest.raises(ValueError):
        router_mod.parse_upstream_args([bad])


# ---- S7: モード名の形と予約語（🟡-4）------------------------------------------------
#
# モード名はそのまま URL prefix（`/` + mode）になる。任意の文字列を許すと 2 通りの壊れ方をする:
#   1. prefix として成立しない名前（大文字・記号・スラッシュ入り）を渡すと、front が出す
#      `/<mode>/*` と一致せず**どこにも当たらない**（無音の 404）。
#   2. 静的配信面と同じ名前を渡すと、その配信面が丸ごと proxy へ吸われる。とくに `js` は
#      `_ASSET_SUBTREE_PREFIXES` の唯一の要素で、統合層 JS と Service Worker の import が
#      全滅する（ページが起動しなくなる）。
# どちらも起動時には何のエラーも出ないため、受け取る側で形を固定する。


@pytest.mark.parametrize("bad", [
    "Live",          # 大文字（front のモード名は小文字）
    "1live",         # 数字始まり
    "li-ve",         # ハイフン（prefix として front の表と一致しない）
    "li ve",         # 空白
    "li/ve",         # スラッシュ（prefix 境界を壊す）
    "_live",         # 下線始まり
    "ライブ",         # 非 ASCII
])
def test_parse_upstream_args_rejects_mode_names_that_are_not_identifiers(bad):
    with pytest.raises(ValueError):
        router_mod.parse_upstream_args([f"{bad}=http://127.0.0.1:9999"])


@pytest.mark.parametrize("reserved", ["js", "sw.js", "index.html", "__serving_root"])
def test_parse_upstream_args_rejects_names_colliding_with_the_static_surface(reserved):
    with pytest.raises(ValueError):
        router_mod.parse_upstream_args([f"{reserved}=http://127.0.0.1:9999"])


def test_reserved_mode_names_are_derived_from_the_static_surface_constants():
    """予約語は静的配信面の定義から導く（第 2 の一覧を持たない）。"""
    # Arrange / Act
    reserved = router_mod.RESERVED_MODE_NAMES
    # Assert: 資産ファイル・資産サブツリー・診断エンドポイントの全てが含まれる。
    assert set(router_mod._ASSET_FILES) <= reserved
    assert {p.rstrip("/") for p in router_mod._ASSET_SUBTREE_PREFIXES} <= reserved
    assert router_mod._SERVING_ROOT_PATH.lstrip("/") in reserved


@pytest.mark.parametrize("ok", ["live", "replay", "sim", "sim2", "my_mode"])
def test_parse_upstream_args_accepts_valid_mode_names(ok):
    """検証が過剰に効いていない（第 4 モードの名前が通る）。"""
    assert router_mod.parse_upstream_args([f"{ok}=http://x"]) == {ok: "http://x"}


def test_default_upstreams_pass_their_own_validation():
    """既定値そのものが検証を通る（既定と検定が食い違わない）。"""
    defaults = router_mod.default_upstreams()
    argv = [f"{mode}={url}" for mode, url in defaults.items()]
    assert router_mod.parse_upstream_args(argv) == defaults


def test_main_cli_defaults_include_all_four_modes():
    """`--upstream` 無指定時の既定は 4 モード（serve.sh が明示指定する値と同じ既定）。

    ISSUE-452 で第 4 モード `dashboard` を足した。既定の集合は front のモード定義表
    （`unified_ui/web/js/mode_table.js`）と 1:1 でなければならない（片方だけに在るモードは、
    押しても 404 になるだけで何のエラーも出ない＝無音の失敗になる）。
    """
    # Arrange / Act
    got = router_mod.default_upstreams()
    # Assert
    assert set(got) == {"live", "replay", "sim", "dashboard"}
    assert got["sim"].endswith(":8381")
    assert got["dashboard"].endswith(":8481")


def test_default_upstreams_bind_the_dashboard_core_to_loopback_8481():
    """dashboard core は loopback 限定の専用プロセス（arch-spec §3・serve.sh と同値）。"""
    # Arrange / Act
    got = router_mod.default_upstreams()
    # Assert
    assert got["dashboard"] == "http://127.0.0.1:8481"


def test_dashboard_upstream_can_be_overridden_by_its_environment_variable(monkeypatch):
    """`UNIFIED_DASHBOARD_UPSTREAM` で個別に上書きできる（既存 3 モードと同じ規約）。"""
    # Arrange
    monkeypatch.setenv("UNIFIED_DASHBOARD_UPSTREAM", "http://127.0.0.1:19481")
    # Act
    got = router_mod.default_upstreams()
    # Assert: 当該モードだけが差し替わり、他モードは既定のまま。
    assert got["dashboard"] == "http://127.0.0.1:19481"
    assert got["live"] == "http://127.0.0.1:8001"


@pytest.mark.parametrize("mode", sorted(router_mod._DEFAULT_UPSTREAMS))
def test_every_default_mode_is_overridable_by_its_own_environment_variable(monkeypatch, mode):
    """既定表の全モードが `UNIFIED_<MODE>_UPSTREAM` で上書きできる（環境変数の取り残し検出）。

    モードを足したのに環境変数の口を足し忘れると、そのモードだけが上書き不能になる。
    起動時には何も起きず、上書きしたはずの上流へ行かない形で現れる（無音の失敗）。
    """
    # Arrange
    monkeypatch.setenv(f"UNIFIED_{mode.upper()}_UPSTREAM", "http://127.0.0.1:19999")
    # Act
    got = router_mod.default_upstreams()
    # Assert
    assert got[mode] == "http://127.0.0.1:19999"


# ---- A8: prefix 無し API パスは 404 ------------------------------------------

def test_bare_api_path_without_prefix_returns_404(router):
    # Arrange
    server, live_srv, replay_srv = router
    # Act: prefix 無しの `/compute` 直叩き（SW リライト前の素パス）
    resp = _request(server, "GET", "/compute")
    # Assert: いずれの上流へも振り分けず 404（定義挙動）
    assert resp.status == 404, f"expected 404 for bare api path, got {resp.status}/{resp.error}"
    assert len(live_srv.records) == 0
    assert len(replay_srv.records) == 0


# ---- ISSUE-035 系: 静的配信のパストラバーサル防御 -----------------------------
# replay_ui の StaticFileServer は同型の弱点（区切り境界を見ない prefix 一致）を
#   is_relative_to へ是正済みで回帰テストも持つ（tests/unit/test_static_file_server.py）。
#   ルータ側は os.sep 付き比較で安全だが**回帰テストが無かった**ため、実際に 8000 で
#   配信される本経路にも同じ攻撃ケースを固定する。
#
# 検証設計の注意: 生の `..` は _serve_static 手前の `rel.startswith("..")` で弾かれ、
#   realpath 比較まで到達しない。**realpath ガードそのもの**を検証するには、rel に `..` を
#   含まないまま実体が root 外を指す経路＝ web_root 内の symlink が必要である
#   （当初 `..` だけで書いたところ、ガードを弱める変異を検出できず空虚と判明した）。

@pytest.fixture()
def router_with_secret_sibling(upstreams, tmp_path):
    """web_root と**接頭辞を共有する兄弟**に機密を置き、root 内から symlink を張ったルータ。"""
    web_root = tmp_path / "unified_web"
    web_root.mkdir()
    (web_root / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    secret = tmp_path / "unified_web_SECRET"      # 区切り無し prefix 一致なら通過しうる
    secret.mkdir()
    (secret / "leak.txt").write_text("TOP_SECRET", encoding="utf-8")
    # rel に `..` を含まないまま root 外へ出る唯一の経路（realpath ガードの検証点）。
    (web_root / "link").symlink_to(secret, target_is_directory=True)

    live_srv, replay_srv = upstreams
    server = router_mod.create_router_server(
        ("127.0.0.1", 0),
        upstreams={"live": _base_url(live_srv), "replay": _base_url(replay_srv)},
        web_root=str(web_root),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def test_static_symlink_escaping_web_root_is_rejected(router_with_secret_sibling):
    """web_root 内の symlink が外（接頭辞共有の兄弟）を指しても配信しない（CWE-22）。

    realpath 比較を区切り境界なしの prefix 一致へ弱めると本テストが失敗する
    （検出力を変異注入で実証済み）。
    """
    resp = _request(router_with_secret_sibling, "GET", "/link/leak.txt")
    assert resp.body is None or b"TOP_SECRET" not in resp.body, "機密が漏洩した"
    assert resp.status in (400, 404), f"逸脱要求が {resp.status} で通過している"


@pytest.mark.parametrize("path", [
    "/../unified_web_SECRET/leak.txt",             # 生 `..`（手前の正規化で弾かれる想定）
    "/subdir/../../unified_web_SECRET/leak.txt",   # 深い階層からの逸脱
])
def test_static_dotdot_traversal_is_rejected(router_with_secret_sibling, path):
    """生の `..` による逸脱も拒否する（正規化段の防御）。"""
    resp = _request(router_with_secret_sibling, "GET", path)
    assert resp.body is None or b"TOP_SECRET" not in resp.body
    assert resp.status in (400, 404)


def test_static_normal_file_is_still_served(router_with_secret_sibling):
    """正規の配信は従来どおり成功する（防御が過剰に効いていない）。"""
    resp = _request(router_with_secret_sibling, "GET", "/")
    assert resp.status == 200
    assert b"ok" in resp.body


# ---- ISSUE-198: 接続の扱い（keep-alive / backlog / ヘッダ重複） ----------------
#
# 真因の確定（2026-07-31・実 UI 実測）:
#   報告された「SW 経由の /live_ticks が network error」は **ルータ 8000 の一時停止**
#   （再起動・瞬断）で再現する。ポーリング中に 8000 を落とすと `net::ERR_FAILED @
#   /live_ticks?since=0` が連続して出る（実測 89 回中 8 回失敗）。SW はこの失敗を忠実に
#   伝えているだけで、リライト論理の欠陥ではない。ページ側は次の poll で自動復帰する。
#
# 併せて実測で見つかったルータ自身の欠陥を以下で固定する。

def _raw_headers(server, path):
    """1 接続で 1 GET を行い、生のステータス行とヘッダ（重複込み）を返す。"""
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        resp.read()
        return resp.version, resp.getheaders()
    finally:
        conn.close()


def test_proxied_response_has_no_duplicate_date_or_server_header(router):
    """上流の Date/Server を転送しない（RFC 7231 §7.1.1.2 は Date の重複を禁ずる）。

    `send_response()` がルータ自身の Date/Server を必ず出すため、上流の同名ヘッダを
    そのまま転送すると 1 応答に 2 回現れる（修正前は `curl -D -` で実測できた）。
    """
    server, _live, _replay = router
    _version, headers = _raw_headers(server, "/live/candles?datasetRef=x")
    for name in ("date", "server"):
        n = sum(1 for key, _ in headers if key.lower() == name)
        assert n == 1, f"{name} ヘッダが {n} 個ある（重複）: {headers}"


def test_router_speaks_http_1_1_and_keeps_the_connection_alive(router):
    """HTTP/1.1 で応答し、1 接続で複数リクエストを処理できる（ISSUE-198）。

    既定の HTTP/1.0 では 1 リクエスト = 1 TCP 接続になり、1 画面で多数の API を並行に
    叩く本 UI では接続生成が集中する。`_proxy` は上流本体を全読みして自前で
    `Content-Length` を付与し、`_serve_static` / `_send_simple` も明示するため、
    HTTP/1.1 の応答長確定要件を全経路で満たす。
    """
    server, _live, _replay = router
    version, _headers = _raw_headers(server, "/live/candles?datasetRef=x")
    assert version == 11, f"HTTP/1.1 で応答すること（実際: {version}）"

    # 同一接続で 3 回。keep-alive が効いていなければ 2 回目以降が失敗する。
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        statuses = []
        for _ in range(3):
            conn.request("GET", "/live/candles?datasetRef=x")
            resp = conn.getresponse()
            resp.read()
            statuses.append(resp.status)
        assert statuses == [200, 200, 200], f"1 接続で 3 回処理できること: {statuses}"
    finally:
        conn.close()


def test_router_widens_accept_backlog_without_touching_stdlib_default():
    """accept backlog を広げる。ただし stdlib のクラス属性は書き換えない（ISSUE-198）。

    `ThreadingHTTPServer.request_queue_size` を直接書き換えると同一プロセス内の他サーバへ
    波及するため、サブクラス `RouterServer` に閉じ込める。
    """
    import socketserver

    assert router_mod.RouterServer.request_queue_size > ThreadingHTTPServer.request_queue_size
    assert socketserver.TCPServer.request_queue_size == 5, "stdlib 既定を書き換えていないこと"


def test_router_handler_reaps_idle_keep_alive_connections(router):
    """idle な keep-alive 接続に上限秒を設ける（HTTP/1.1 化の副作用対策・ISSUE-198）。

    ThreadingHTTPServer は 1 接続 = 1 スレッド。timeout が無いと、到達しなかったタブや
    中断されたロードの接続がスレッドを保持し続ける。
    """
    assert isinstance(router_mod.RouterHandler.timeout, (int, float))
    assert 0 < router_mod.RouterHandler.timeout <= 300


# --- ISSUE-278 #9/#10: 配信面の最小権限とキャッシュ方針 --------------------------- #
@pytest.mark.parametrize("path", [
    "/tests/sw_rewrite.test.js",   # 内部設計（SW リライト規則）がそのまま読める
    "/package-lock.json",
    "/vitest.config.js",
    "/node_modules/vitest/package.json",
])
def test_router_does_not_serve_non_asset_files(router, path):
    """web_root 全体ではなく資産だけを配信する（実測で 200 を返していた経路の回帰）。"""
    server, _live, _replay = router
    resp = _request(server, "GET", path)
    assert resp.status == 404, f"{path} が配信面に露出している"


@pytest.mark.parametrize("path", ["/", "/index.html", "/sw.js", "/js/unified_root.js"])
def test_router_serves_entry_and_layer_js(router, path):
    """エントリ・SW・統合層 JS は従来どおり配信する（縮小しすぎていないこと）。"""
    server, _live, _replay = router
    resp = _request(server, "GET", path)
    assert resp.status == 200, f"{path} が配信できていない"


def test_router_disables_browser_cache_for_own_assets(router):
    """統合層の資産にも core と同じキャッシュ無効化を付ける。

    付けないと、修正した統合層 JS と Service Worker をブラウザが古いまま掴む（両 core が
    この理由で明示的に潰した問題を、実配信ページだけが再現していた）。
    """
    server, _live, _replay = router
    host, port = server.server_address
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/js/unified_root.js")
        resp = conn.getresponse()
        resp.read()
        assert "no-store" in (resp.getheader("Cache-Control") or "")
    finally:
        conn.close()


# ---- ISSUE-348: 配信元ツリーの申告 -------------------------------------------
# 病因: `serve.sh` の二重起動判定が「8000 が応答するか」しか見ておらず、「どのツリーが
#   応答しているか」を見ていなかった。別チェックアウトの残存スタックがポートを握っていると
#   no-op で正常終了し、開発者は自分のコードが 1 行も入っていない UI を検証してしまう。
#   実際に 2 度事故が起きている（ISSUE-355 はこの機構の帰結）。
#
# 判定の材料をプロセス外から観測可能にすることが要点で、これが無いと照合は原理的に不可能。


def test_serving_root_reports_the_tree_this_router_is_serving(router):
    # Arrange
    server, live_srv, replay_srv = router
    expected = os.path.dirname(os.path.dirname(os.path.realpath(router_mod.__file__)))
    # Act
    resp = _request(server, "GET", "/__serving_root")
    # Assert: 自分の実体位置から一意に決まる配信元を平文 1 行で返す
    assert resp.status == 200, f"expected 200, got {resp.status}/{resp.error}"
    assert resp.body.decode("utf-8").strip() == expected
    assert resp.headers.get("Content-Type", "").startswith("text/plain")


def test_serving_root_is_not_proxied_to_any_upstream(router):
    # Arrange: core へ透過させると、core 側の 404 が返って「答えない」状態になる
    server, live_srv, replay_srv = router
    # Act
    _request(server, "GET", "/__serving_root")
    # Assert: どちらの上流にも到達していない（ルータ自身が答える）
    assert len(live_srv.records) == 0
    assert len(replay_srv.records) == 0


def test_serving_root_answers_with_query_string(router):
    # Arrange: 呼び出し側がキャッシュ回避のクエリを付けても答える必要がある
    server, live_srv, replay_srv = router
    # Act
    resp = _request(server, "GET", "/__serving_root?t=1")
    # Assert
    assert resp.status == 200, f"expected 200, got {resp.status}/{resp.error}"
    assert resp.body.decode("utf-8").strip().startswith(os.sep)


def test_serving_root_is_not_cached(router):
    # Arrange: 占有者が入れ替わっても即座に見える必要がある（古い答えを掴ませない）
    server, live_srv, replay_srv = router
    # Act
    resp = _request(server, "GET", "/__serving_root")
    # Assert
    assert "no-store" in resp.headers.get("Cache-Control", "")
