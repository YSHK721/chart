"""serve_dashboard — 水準到達シート core の HTTP フレームワーク層（127.0.0.1:8481）。

エンドポイント:
    GET  /            → `web/index.html`（未実装のうちは待受け確認用の 200 プレースホルダ）
    GET  /<資産>      → `web/` の静的配信
    POST /reach_sheet → シート 1 枚（arch-spec §9 の JSON 契約）

ライブ core への相乗りではなく**専用プロセス**である（arch-spec §3）。ライブ core の
`do_GET` は if 連鎖で拡張点が無く、相乗りには改変が要る＝OCP に反するためである。計算は
HTTP でライブ core を叩かず in-process で読む（プールを奪わない）。

統合 UI の起動スクリプトは `GET /` が 200 を返すまで待ってから router を起動する。
したがって `web/` が未実装でも `GET /` は 200 でなければならない。

静的配信とパストラバーサル防御は `simulator.replay_ui.framework.static_file_server` を
**import で再利用**する（防御を複製しない＝片方だけ腐る事故を構造的に防ぐ・CWE-22）。

CLEAN_ARCH §6: HTTP・スレッド・静的配信という偶有的技術を最外層へ隔離する
（`serve_sim.py` / `serve_replay.py` と同型の ThreadingHTTPServer 構成）。
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from simulator.replay_ui.framework.static_file_server import StaticFileServer

#: シート要求の経路（router が `/dashboard` を剥がした後の形）。
REACH_SHEET_PATH = "/reach_sheet"

#: 既定の待受けポート（統合 UI の `DASHBOARD_PORT` と同値）。
DEFAULT_PORT = 8481

#: 配信元ツリーを明示する起動引数（ISSUE-348 の規律）。
REPO_ROOT_OPTION = "--repo-root"

#: 受け付ける本文の上限（素材は送らない＝束の宣言だけなので小さくてよい）。
_MAX_BODY = 1_000_000

#: `web/index.html` が無いときの待受け確認用の本文（統合 UI の起動待ち用）。
_PLACEHOLDER = (
    "<!doctype html><meta charset=\"utf-8\">"
    "<title>水準到達シート</title>"
    "<p>dashboard core は稼働しています（画面は未配置）。</p>"
).encode("utf-8")


class DashboardApp:
    """dashboard core のアプリケーション面（framework 層）。

    Args:
        controller_factory: 要求 1 件ぶんの controller を作る。**要求ごとに口を組み直す**
            ため、素材（足・系列）は毎回読み直される（古い足を配らない）。当てはめの
            epoch は controller の外（要求をまたぐ状態）が持つので、組み直しても段 2 の
            「発行 0 回」は保たれる。
        web_dir: フロントの配信根（None で静的配信無効）。
        shared_js_root: 単一ソース共有のフォールバック根（`js/` `css/` `vendor/` のみ）。
    """

    def __init__(
        self,
        *,
        controller_factory: Callable[[], Any],
        web_dir: Any = None,
        shared_js_root: Any = None,
    ) -> None:
        self.controller_factory = controller_factory
        self.web_dir = Path(web_dir).resolve() if web_dir else None
        self.shared_js_root = Path(shared_js_root).resolve() if shared_js_root else None
        self.static_server = StaticFileServer(self.web_dir, self.shared_js_root)

    def reach_sheet(self, body: bytes) -> "tuple[int, dict]":
        """要求本文（JSON）から応答（状態コード, JSON）を作る。"""
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError) as error:
            return 400, {
                "ok": False,
                "error": {"type": "validation", "message": f"JSON として読めません: {error}"},
            }
        response = self.controller_factory().handle(payload)
        return (200 if response.get("ok") else 400), response


def make_handler(app: DashboardApp):
    """`app` を束ねた `BaseHTTPRequestHandler` サブクラスを返す（serve_sim と同型）。"""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # noqa: D401 — アクセスログ抑制（他 core と同一）
            pass

        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path
            if path in ("/", "") and app.static_server.resolve(path) is None:
                return _write(self, 200, "text/html", _PLACEHOLDER)
            return app.static_server.serve(self, path)

        def do_POST(self):  # noqa: N802
            path = urlparse(self.path).path
            if path != REACH_SHEET_PATH:
                self.send_response(404)
                self.end_headers()
                return
            length = min(int(self.headers.get("Content-Length") or 0), _MAX_BODY)
            status, payload = app.reach_sheet(self.rfile.read(length))
            return _write(
                self, status, "application/json",
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )

    return Handler


def _write(handler: BaseHTTPRequestHandler, status: int, content_type: str,
           body: bytes) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", f"{content_type}; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def make_server(
    app: DashboardApp, host: str = "127.0.0.1", port: "int | None" = None
) -> ThreadingHTTPServer:
    """サーバを生成して返す（起動はしない）。`port=None` は空きポート（テスト用）。"""
    return ThreadingHTTPServer((host, port or 0), make_handler(app))


def serve(app: DashboardApp, host: str = "127.0.0.1", port: "int | None" = None) -> None:
    """サーバを起動して待ち受ける（ブロッキング）。"""
    server = make_server(app, host, port)
    actual = server.server_address[1]
    print(f"dashboard backend: http://{host}:{actual}/  (Ctrl-C 停止)")
    server.serve_forever()


def main(argv: "list[str] | None" = None) -> None:
    """`python -m dashboard_ui.framework.serve_dashboard <port> [--repo-root <path>]`。

    `--repo-root` は**配信元ツリーの絶対パス**である。省略時は Composition Root が自分の
    ファイル位置から解決する（既定の挙動は不変）。統合 UI の起動スクリプト（unified_ui/serve.sh）は必ず渡す:
    PYTHONPATH は ps の argv に現れないため、これが無いと停止側が「8481 を握っているのが
    どのツリーの core か」を判定できず、別ツリーの残骸を掴んだまま起動する
    （ISSUE-348 / ISSUE-355 と同型の「他人のコードを自分のものとして見る」事故）。
    """
    import sys

    from dashboard_ui.main.composition_root import build_dashboard_app

    port, repo_root = _parse(list(sys.argv[1:] if argv is None else argv))
    serve(build_dashboard_app(repo_root=repo_root), port=port)


def _parse(arguments: "list[str]") -> "tuple[int, str | None]":
    """`<port> [--repo-root <path>]` を `(port, repo_root)` へ。"""
    port = DEFAULT_PORT
    repo_root: "str | None" = None
    rest = list(arguments)
    while rest:
        token = rest.pop(0)
        if token == REPO_ROOT_OPTION and rest:
            repo_root = rest.pop(0)
            continue
        if not token.startswith("-"):
            port = int(token)
    return port, repo_root


if __name__ == "__main__":
    main()
