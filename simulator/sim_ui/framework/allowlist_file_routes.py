"""許可ファイル名だけを内側の配信器へ通す Decorator（framework 層・Phase 5 R-1）。

`StaticFileServer` / `StaticPrefixRoutes` と**同一の面**（``serve(handler, path)``）を持ち、
許可集合に一致する path だけを内側へ委譲する。一致しないものは 404 で、**内側の配信器は
呼ばれない**。

なぜ「根ごと配信」ではなく「1 ファイルだけ許可」か（NFR-07 の構造担保）:
    report_ui の vendor 根には Chart.js v4.4.1（`chart.umd.js`）と lightweight-charts
    v4.1.3（`lightweight-charts.standalone.js`）が**同居**している。sim が要るのは前者
    だけで、後者は統合ページの v5.2.0 と二重に載る経路そのものである。根を丸ごと配信して
    「後で弾く」形にすると、内側の許可根判定を 1 つ変えただけで v4 lwc が露出する。
    渡さなければ露出しない——到達不能を経路の有無で担保する。

許可集合はここが決めない（合成根が持つ）。この Decorator にファイル名リテラルを書くと、
配信方針（何を出すか）と配信機構（どう出すか）が 1 か所に混ざり、方針変更のたびに
framework 層を触ることになる。

パストラバーサル防御（CWE-22）・応答 byte・Content-Type は委譲先 `StaticFileServer` の
単一ソースのまま。ここには 1 行も写さない（許可集合は「ちょうど一致」だけを見るので、
`..` を含む path はそもそも一致せず内側へ届かない）。
"""
from __future__ import annotations

from typing import Any, Iterable


class AllowlistFileRoutes:
    """許可ファイル名の集合に一致する path だけを ``inner`` へ委譲する ``serve`` 面。"""

    def __init__(self, inner: Any, *, allowed: "Iterable[str]") -> None:
        self._inner = inner
        self._allowed = frozenset(allowed)

    @property
    def allowed(self) -> "frozenset[str]":
        """許可ファイル名の集合（合成根の検定が方針を確かめるための面）。"""
        return self._allowed

    @property
    def inner(self) -> Any:
        """委譲先の配信器（結線の実測点）。"""
        return self._inner

    def serve(self, handler: Any, path: str) -> Any:
        if path not in self._allowed:
            handler.send_response(404)
            handler.end_headers()
            return None
        return self._inner.serve(handler, path)
