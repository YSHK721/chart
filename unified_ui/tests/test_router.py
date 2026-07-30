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
Response = namedtuple("Response", ["status", "content_type", "body", "error"])

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
        return Response(resp.status, resp.getheader("Content-Type"), data, None)
    except (
        http.client.RemoteDisconnected,
        http.client.BadStatusLine,
        ConnectionError,
        OSError,
    ) as exc:
        return Response(None, None, b"", repr(exc))
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
        live_upstream=_base_url(live_srv),
        replay_upstream=_base_url(replay_srv),
        web_root=WEB_ROOT,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, live_srv, replay_srv
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
        live_upstream=_base_url(slow_srv),
        replay_upstream=_base_url(slow_srv),
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
        live_upstream=_base_url(hung_srv),
        replay_upstream=_base_url(hung_srv),
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
        live_upstream=_base_url(live_srv),
        replay_upstream=_base_url(replay_srv),
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
