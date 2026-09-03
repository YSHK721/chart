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
    ``writer``: ``(handler, ApiResponse) -> None``。既定は :func:`write_json`。

    **書き出し器を注入で受ける理由**（ISSUE-479 Wave2 3-3・加法）:
        replay 側の JSON 応答ヘッダは sim 側と異なる（``Content-Type: application/json`` に
        charset が付かない）。この違いは front と検定が見ている実体なので、骨格を共有する
        ついでに統一してはならない（応答 byte が変わる）。一方「prefix で選び、外れたら
        静的配信へ落とす」骨格は完全に同じで、2 つ書く理由が無い。**書き出し方だけ**を
        差し替え点にすれば、規則の第 2 実装を作らずに両方を賄える。
        渡さなければ現行と 1 ビットも変わらない。

    **クエリの扱い**（同上・加法）:
        prefix の一致判定と fallback へ渡す値は**クエリを落とした path**、ルート関数へ渡す
        値は**元の path** とする。sim 側の呼び出しは呼ぶ前にクエリを落としているため
        両者は同一であり挙動は変わらない。replay のルートはクエリを読むため、元の path が
        要る（``/candles?datasetRef=...``）。

    prefix の一致は**区切り境界**で判定する。``str.startswith`` だけで判定すると
    ``/indicators-extra.js`` のような別資産まで JSON 経路へ吸い込む。
    """

    def __init__(
        self,
        *,
        routes: "Mapping[str, Callable[[str], ApiResponse]]",
        fallback: Any,
        writer: "Callable[[Any, ApiResponse], None] | None" = None,
    ) -> None:
        self._routes = dict(routes)
        self._fallback = fallback
        self._writer = writer if writer is not None else write_json

    def serve(self, handler: Any, path: str) -> None:
        route_path = path.split("?", 1)[0]
        for prefix, responder in self._routes.items():
            if route_path == prefix or route_path.startswith(prefix + "/"):
                return self._writer(handler, responder(path))
        return self._fallback.serve(handler, route_path)
