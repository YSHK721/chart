"""静的 prefix ルート層（framework 層・Phase 4 F-5）。

`StaticFileServer` / `GetRouteResponder` と **同一の面**（``serve(handler, path)``）を持つ。
登録した prefix 配下の GET を「prefix を外したパス」で別の配信器（＝別の許可根を持つ
`StaticFileServer`）へ委譲し、それ以外は fallback へそのまま流す。

なぜ表にするか: sim の表示層は report_ui の JS / CSS を**同一実体のまま**読む
（複製禁止・§11.4）。実体は `simulator/report_ui/web/js` `.../css` にあり、sim の web 根
（`simulator/sim_ui/web`）の配下ではない。symlink で web 根へ引き込む案は採らない——
`StaticFileServer` は resolve() 後の実パスで許可根を判定するため、symlink 先が許可根の
外にある限り 404 になる（実測済み）。根が違うものは**根ごと**別の配信器を立て、prefix で
引くのが唯一の素直な形である。

prefix の一致は**区切り境界**で判定する。``str.startswith`` だけで判定すると
``/report-jsx.js`` のような別資産まで別根へ吸い込む（`json_get_routes` と同じ壊れ方）。

パストラバーサル防御（CWE-22）・応答 byte・Content-Type は委譲先 `StaticFileServer` の
単一ソースのまま。ここには 1 行も写さない。
"""
from __future__ import annotations

from typing import Any, Mapping


class StaticPrefixRoutes:
    """prefix → 配信器の表と静的 fallback を束ねた ``serve`` 面。

    ``routes``: ``{prefix: serve(handler, path) を持つもの}``（既定は `StaticFileServer`）。
    ``fallback``: 未登録パスの委譲先（既存の配信面。1 バイトも変えない）。
    """

    def __init__(self, *, routes: "Mapping[str, Any]", fallback: Any) -> None:
        self._routes = dict(routes)
        self._fallback = fallback

    @property
    def prefixes(self) -> "tuple[str, ...]":
        """登録済み prefix（合成根の検定が結線を確かめるための面）。"""
        return tuple(self._routes)

    def route(self, prefix: str) -> Any:
        """prefix に結線された配信器を返す（未登録は KeyError）。

        「何が配信されるか」の方針（例: vendor の許可ファイル集合）は配信器が持つ。
        結線を実 HTTP だけで確かめると、方針の変更が経路の増減として現れないため、
        表そのものを覗ける面を 1 つ用意する（合成根の検定が使う読み取り専用の口）。
        """
        return self._routes[prefix]

    def serve(self, handler: Any, path: str) -> Any:
        for prefix, server in self._routes.items():
            if path == prefix:
                # prefix ちょうどは当該根の「/」（＝index）として委譲する。
                return server.serve(handler, "/")
            if path.startswith(prefix + "/"):
                return server.serve(handler, path[len(prefix):])
        return self._fallback.serve(handler, path)
