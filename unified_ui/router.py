"""Unified UI reverse-proxy router (Green — 本体実装).

公開 8000 単一 URL の内側で 2 つの既存 core を別プロセスのまま維持するための
薄いリバースプロキシ。基本設計書 `.doc/LIVE_REPLAY_UNIFICATION_BASIC_DESIGN.md` §2。

契約:
  - `/live/*`   → prefix `/live` を除去して live upstream (既定 127.0.0.1:8001) へプロキシ
  - `/replay/*` → prefix `/replay` を除去して replay upstream (既定 127.0.0.1:8281) へプロキシ
  - method / query / body / status / content-type / header を透過する
  - prefix 除去は二重 slash を生まない（`/live/x` → `/x`、`/live` → `/`）
  - `/` および `/js/*`・`/sw.js` は unified web 静的資産を配信する
  - 上流ダウン時は当該系統のみ 502（別プロセス隔離）
  - prefix 無しの API パス（例 `/compute` 直）は 404

本ファイルは新規のみ。既存モジュール（indicator_ui / replay_ui / 共有 JS 等）へは一切
波及しない（コアはプロキシ経由で自分宛の素パス要求のみ受ける＝無編集で成立）。
"""

from __future__ import annotations

import http.client
import mimetypes
import os
import posixpath
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

# 統合層が配信してよい静的資産（最小権限・ISSUE-278 #9）。web_root 全体を許可すると
#   tests/・node_modules/・package-lock.json・vitest.config.js まで露出する。
_ASSET_FILES = frozenset({"index.html", "sw.js"})
_ASSET_SUBTREE_PREFIXES = ("js/",)

# モード prefix と、サーバインスタンス上の対応する上流 URL 属性名。
_PREFIX_TO_UPSTREAM_ATTR = (
    ("/live", "live_upstream"),
    ("/replay", "replay_upstream"),
)

# プロキシで転送してはならない hop-by-hop ヘッダ（RFC 7230 §6.1）＋ Host / Content-Length
# （Host / Content-Length は転送先で再計算する）。
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)


#: `send_response()` がルータ自身の値を必ず出すため、上流の同名ヘッダは転送しない。
#: 転送すると 1 応答に `Date` / `Server` が 2 回現れる（RFC 7231 §7.1.1.2 は `Date` の
#: 重複を明確に禁じる）。実測（`curl -D -` / `/live/live_ticks`）で重複を確認済み。
_GENERATED_BY_ROUTER = frozenset({"date", "server"})


class RouterServer(ThreadingHTTPServer):
    """accept backlog を広げた `ThreadingHTTPServer`（ISSUE-198）。

    既定の `request_queue_size = 5` は `listen(5)` を意味し、溢れたぶんの SYN は落とされる。
    ブラウザ側ではこれが原因不明の接続失敗（fetch の reject）として現れるため、初回ロードの
    ように多数の静的資産と API を一斉に要求する局面に耐える値へ広げる。

    `ThreadingHTTPServer` のクラス属性を直接書き換えると同一プロセス内の他サーバへも
    波及するため、**サブクラスとして閉じ込める**。
    """

    request_queue_size = 128


class RouterHandler(BaseHTTPRequestHandler):
    """8000 で待ち受けるルータのリクエストハンドラ。

    live_upstream / replay_upstream / web_root は `create_router_server` が
    サーバインスタンスへ格納した値を参照する。
    """

    #: HTTP/1.1（keep-alive）で応答する（ISSUE-198）。
    #:
    #: 既定の HTTP/1.0 では **1 リクエスト = 1 TCP 接続**になり、応答ごとに接続が閉じる。
    #: 本 UI は 1 画面で多数の API を並行に叩く（/live_ticks 2.5 秒周期・/forming_bar 5 秒周期・
    #: 指標ごとの /compute・/candles・/market_profile・静的資産）ため接続生成が集中し、
    #: accept backlog（既定 5）を溢れさせて接続が確立できなくなる。ブラウザ側ではこれが
    #: fetch の reject となり、Service Worker の `event.respondWith` が拒否されて
    #: 「The FetchEvent for … resulted in a network error response: the promise was rejected」
    #: として現れる。
    #:
    #: 実測（`/live/live_ticks?since=0`・200KB 応答・同時接続数を変えて計測）:
    #:   HTTP/1.0 + backlog 5 … 20/60/120 本は全数 200、**240 本で 38 件が TimeoutError**
    #:
    #: 安全性: HTTP/1.1 は応答長の確定を要求するが、本ハンドラの応答経路は 3 つとも
    #: `Content-Length` を明示している（`_proxy` は上流本体を全読みして自前で付与、
    #: `_serve_static` / `_send_simple` も明示）。未知メソッドの 501 は基底が付与する。
    protocol_version = "HTTP/1.1"

    #: idle な keep-alive 接続を回収する上限秒（HTTP/1.1 化の副作用対策）。
    #:
    #: `ThreadingHTTPServer` は **1 接続 = 1 スレッド**である。HTTP/1.1 では接続が応答後も
    #: 開いたままになるため、timeout を持たないとブラウザが閉じるまでスレッドが解放されない。
    #: 到達しなかったタブや中断されたロードの接続が積み上がるのを防ぐ。基底は socket の
    #: timeout 発生時に `close_connection` を立てるため、応答の途中切断は起きない。
    timeout = 65

    def _handle(self) -> None:
        prefix, upstream = self._match_prefix(self.path)
        if prefix is not None:
            self._proxy(upstream, self.path[len(prefix):])
            return
        self._serve_static(self.path)

    def _match_prefix(self, path: str):
        """path が `/live` / `/replay` 配下なら (prefix, upstream_base_url) を返す。

        `/live` 自体（末尾スラッシュ無し）と `/live/...` の両方を prefix 配下とみなす。
        `/livefoo` のような別語は配下としない（`/live/` 境界を厳格に判定する）。
        """
        for prefix, attr in _PREFIX_TO_UPSTREAM_ATTR:
            if path == prefix or path.startswith(prefix + "/"):
                return prefix, getattr(self.server, attr)
        return None, None

    def _proxy(self, upstream: str, rest: str) -> None:
        """prefix 除去後の rest を upstream へ透過プロキシする。"""
        # prefix 除去で先頭 slash が失われる場合（`/live` → ''）を補い、二重 slash も避ける。
        if not rest.startswith("/"):
            rest = "/" + rest

        parts = urlsplit(upstream)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        fwd_headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in _HOP_BY_HOP
        }

        # connect と read の timeout を分離する（code-review 🔴-2）。
        #   - connect_timeout: 接続確立の上限（短め）。実クラッシュはプロセス消滅＝接続拒否で
        #     即 502 になる（この値に依存しない）。
        #   - read_timeout: 応答本体の待ち上限。リプレイ全期間ロード等の重処理を 502 化しないよう
        #     production（main/serve.sh）では寛容値（None=無制限可）にする。None は「無制限」。
        connect_timeout = getattr(self.server, "connect_timeout", 5.0)
        read_timeout = getattr(self.server, "read_timeout", 4.0)
        conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=connect_timeout)
        try:
            conn.connect()
        except OSError:
            # 接続確立失敗（接続拒否・connect timeout）＝上流ダウン。当該系統のみ 502（隔離）。
            self._send_simple(502, b"upstream unavailable")
            try:
                conn.close()
            except OSError:
                pass
            return
        # 接続確立後は read フェーズ用 timeout へ切替（重処理は寛容・None で無制限）。
        try:
            if conn.sock is not None:
                conn.sock.settimeout(read_timeout)
        except OSError:
            pass
        try:
            conn.request(self.command, rest, body=body, headers=fwd_headers)
            resp = conn.getresponse()
            data = resp.read()
            resp_headers = resp.getheaders()
            status = resp.status
        except OSError:
            # read 中断（read timeout・接続リセット等）は当該系統のみ 502（別プロセス隔離）。
            self._send_simple(502, b"upstream unavailable")
            return
        finally:
            conn.close()

        self.send_response(status)
        for key, value in resp_headers:
            lowered = key.lower()
            # hop-by-hop はプロキシが終端する。ルータ自身が生成するヘッダ（Date/Server）は
            #   転送すると重複するため落とす（ISSUE-198）。
            if lowered in _HOP_BY_HOP or lowered in _GENERATED_BY_ROUTER:
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, path: str) -> None:
        """web_root 配下の静的資産を配信する。prefix 無し API 等・不在・配信面外は 404。"""
        url_path = urlsplit(path).path
        if url_path == "/":
            url_path = "/index.html"

        # パストラバーサル防止: 正規化後に web_root 外を指す経路を拒否する。
        rel = posixpath.normpath(url_path).lstrip("/")
        if rel.startswith("..") or os.path.isabs(rel):
            self._send_simple(404, b"not found")
            return

        # ISSUE-278 #9: web_root 全体を許可すると tests/・node_modules/・package-lock.json まで
        #   配信面に露出する（実測: GET /tests/sw_rewrite.test.js が 200 を返していた）。
        #   replay 側 static_file_server が既に採る最小権限（資産サブツリーのみ許可）へ揃える。
        if not self._is_asset(rel):
            self._send_simple(404, b"not found")
            return

        full = os.path.join(self.server.web_root, rel)
        real_root = os.path.realpath(self.server.web_root)
        real_full = os.path.realpath(full)
        if not (real_full == real_root or real_full.startswith(real_root + os.sep)):
            self._send_simple(404, b"not found")
            return
        if not os.path.isfile(real_full):
            self._send_simple(404, b"not found")
            return

        with open(real_full, "rb") as handle:
            data = handle.read()
        self.send_response(200)
        self.send_header("Content-Type", self._content_type(real_full))
        # ISSUE-278 #10: 両 core（indicator_ui/api/framework/server.py・replay の
        #   static_file_server）は「ブラウザが古い ES モジュールを掴んで修正が反映されない」
        #   問題を理由にキャッシュを無効化している。統合層の JS と /sw.js だけ素のキャッシュ
        #   対象になっていた（実配信ページだけが同じ問題を再現する）ため同一方針へ揃える。
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _is_asset(rel: str) -> bool:
        """配信してよい静的資産か（最小権限・ISSUE-278 #9）。

        統合層が配信するのはエントリ（index.html）・Service Worker（sw.js）・統合層 JS だけ。
        core 由来の資産は ``/live`` ``/replay`` のプロキシが返すため、ここに含める必要はない。
        """
        return rel in _ASSET_FILES or rel.startswith(_ASSET_SUBTREE_PREFIXES)

    @staticmethod
    def _content_type(full: str) -> str:
        """拡張子から content-type を決定する（.js は javascript を明示）。"""
        lower = full.lower()
        if lower.endswith(".js") or lower.endswith(".mjs"):
            return "text/javascript; charset=utf-8"
        if lower.endswith(".html") or lower.endswith(".htm"):
            return "text/html; charset=utf-8"
        if lower.endswith(".css"):
            return "text/css; charset=utf-8"
        if lower.endswith(".json"):
            return "application/json; charset=utf-8"
        ctype, _ = mimetypes.guess_type(full)
        return ctype or "application/octet-stream"

    def _send_simple(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        self._handle()

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        self._handle()

    def log_message(self, *args, **kwargs) -> None:  # noqa: D401
        # 実運用ログはノイズになるため抑制する（挙動には影響しない）。
        return


def create_router_server(
    bind_addr,
    *,
    live_upstream,
    replay_upstream,
    web_root,
    connect_timeout=5.0,
    read_timeout=300.0,
):
    """ルータ用 HTTP サーバを構築して返す。

    Parameters
    ----------
    bind_addr : tuple[str, int]
        バインドする (host, port)。テストは ephemeral port (host, 0) を渡す。
    live_upstream : str
        ライブ core のベース URL（例 "http://127.0.0.1:8001"）。
    replay_upstream : str
        リプレイ core のベース URL（例 "http://127.0.0.1:8281"）。
    web_root : str
        unified web 静的資産のルート（`unified_ui/web`）。

    Returns
    -------
    http.server.ThreadingHTTPServer
        `serve_forever()` 可能なサーバ。ハンドラは設定を参照してプロキシ／静的配信する。
    """
    server = RouterServer(bind_addr, RouterHandler)
    server.live_upstream = live_upstream
    server.replay_upstream = replay_upstream
    server.web_root = web_root
    server.connect_timeout = connect_timeout
    server.read_timeout = read_timeout
    return server


def main(argv=None):
    """CLI 起動。serve.sh から呼ばれる（既存 core は各 serve.sh が別に起動する）。

    ルータ自身はデータ watch を持たない新規プロキシのため python 直起動でよい
    （「生 python 起動禁止」は core 起動＝データ watch 併走が必須な indicator_ui /
    replay_ui にのみ適用される。ルータは cores へプロキシするだけ）。
    """
    import argparse

    parser = argparse.ArgumentParser(description="Unified UI reverse-proxy router")
    parser.add_argument("port", nargs="?", type=int, default=8000, help="公開ポート（既定 8000）")
    parser.add_argument("--host", default="", help="バインドホスト（既定 全 IF）")
    parser.add_argument(
        "--live-upstream",
        default=os.environ.get("UNIFIED_LIVE_UPSTREAM", "http://127.0.0.1:8001"),
        help="ライブ core のベース URL（既定 127.0.0.1:8001・loopback 限定）",
    )
    parser.add_argument(
        "--replay-upstream",
        default=os.environ.get("UNIFIED_REPLAY_UPSTREAM", "http://127.0.0.1:8281"),
        help="リプレイ core のベース URL（既定 127.0.0.1:8281・loopback 限定）",
    )
    parser.add_argument(
        "--web-root",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "web"),
        help="unified web 静的資産ルート",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=5.0,
        help="上流接続確立の上限秒（既定 5s・実クラッシュは接続拒否で即 502）",
    )
    parser.add_argument(
        "--read-timeout",
        type=float,
        default=300.0,
        help=(
            "上流応答の待ち上限秒（既定 300s＝重処理寛容・0 で無制限）。"
            "リプレイ全期間ロード等を 502 化しないため production は寛容値にする（🔴-2）。"
        ),
    )
    args = parser.parse_args(argv)

    # --read-timeout 0 は「無制限」(None) と解釈する（重処理を絶対に打ち切らない運用）。
    read_timeout = None if args.read_timeout == 0 else args.read_timeout

    server = create_router_server(
        (args.host, args.port),
        live_upstream=args.live_upstream,
        replay_upstream=args.replay_upstream,
        web_root=args.web_root,
        connect_timeout=args.connect_timeout,
        read_timeout=read_timeout,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
