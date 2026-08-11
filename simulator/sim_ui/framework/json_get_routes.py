"""GET の JSON ルート層（framework 層・Phase 3 F-5）。

`StaticFileServer` と **同一の面**（``serve(handler, path)``）を持つ。GET のうち登録済み
prefix に一致するものを JSON で応答し、それ以外は fallback（既存の `StaticFileServer`
インスタンス）へそのまま委譲する。

LSP: 呼び出し側（`serve_sim.make_handler` が返す Handler）は ``app.static_server.serve``
としか書いていない。同じ面を満たす限り、差し替えても静的配信の挙動は変わらない
（応答 byte・許可根・CWE-22 防御は `StaticFileServer` の単一ソースのまま）。

prefix の一致は**区切り境界**で判定する。``str.startswith`` だけで判定すると
``/indicators-extra.js`` のような別資産まで JSON 経路へ吸い込む。
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from simulator.sim_ui.adapter.job_api_controller import ApiResponse


def write_json(handler: Any, response: ApiResponse) -> None:
    """`ApiResponse` を HTTP 応答として書き出す（ジョブ API の書き出しと同一の形）。"""
    body = response.to_bytes()
    handler.send_response(response.status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class GetRouteResponder:
    """JSON ルート（prefix → 応答関数）と静的 fallback を束ねた ``serve`` 面。

    ``routes``: ``{prefix: (path) -> ApiResponse}``。
    ``fallback``: ``serve(handler, path)`` を持つもの（既定は `StaticFileServer`）。
    """

    def __init__(
        self, *, routes: "Mapping[str, Callable[[str], ApiResponse]]", fallback: Any
    ) -> None:
        self._routes = dict(routes)
        self._fallback = fallback

    def serve(self, handler: Any, path: str) -> None:
        for prefix, responder in self._routes.items():
            if path == prefix or path.startswith(prefix + "/"):
                return write_json(handler, responder(path))
        return self._fallback.serve(handler, path)
