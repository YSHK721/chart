"""serve_sim_display — 表示層の配信面を足した sim コア（framework 層・Phase 4 F-6）。

Phase 3 の `serve_sim_indicators.SimIndicatorApp` を **継承ではなく委譲で包む**（OCP）。
Phase 3 は 1 バイトも変えない。

    SimIndicatorApp ──(委譲)── SimDisplayApp
                               static_server だけ StaticPrefixRoutes へ差し替える
                               それ以外の属性は __getattr__ で内側へ委譲する

**Handler もサーバ生成も Phase 2 の実体をそのまま使う**（下の re-export）。Handler は
`app.static_server` / `app.controller` / `app.result_server` を属性で引くだけなので、
同じ属性を出せる包み手で足りる。ここで Handler を継承し直すと 4 段目になり、
`make_handler` / `make_server` / `serve` を四重に複製することになる。

足すもの（配信のみ・API は 1 本も増えない）:
    GET /report-js/*    report_ui の JS 実体（`simulator/report_ui/web/js` 根）
    GET /report-css/*   report_ui の CSS 実体（`simulator/report_ui/web/css` 根）
どの prefix を載せるかは合成根（`main/composition_root_display.py`）が決める。
framework 層は「表を受けて差し替える」ことだけを知る（根の選択を持たない）。
"""
from __future__ import annotations

from typing import Any, Mapping

# Handler・サーバ生成・起動は Phase 2 の実体をそのまま使う（複製しない）。
from simulator.sim_ui.framework.serve_sim_jobs import (  # noqa: F401
    make_handler,
    make_server,
    serve,
)
from simulator.sim_ui.framework.static_prefix_routes import StaticPrefixRoutes


class SimDisplayApp:
    """`SimIndicatorApp` を包み、静的配信の根を prefix で足したアプリケーション面。

    ``inner``: `SimIndicatorApp`（配信面 ＋ ジョブ実行系 ＋ 指標一覧）。
    ``static_routes``: ``{prefix: serve(handler, path) を持つ配信器}``。
    """

    def __init__(self, *, inner: Any, static_routes: "Mapping[str, Any]") -> None:
        self._inner = inner
        # 既存の静的面（JSON ルート層を含む）を fallback にする。既存経路の応答 byte・
        # 許可根・CWE-22 防御は内側の単一ソースのまま変わらない。
        self.static_server = StaticPrefixRoutes(
            routes=static_routes, fallback=inner.static_server
        )

    @property
    def inner(self) -> Any:
        """包んでいる `SimIndicatorApp`（結線を複製していないことを確かめる面）。"""
        return self._inner

    def __getattr__(self, name: str) -> Any:
        """自分が持たない属性は内側の `SimIndicatorApp` へ委譲する。

        Handler は `app.controller`（ジョブ API）・`app.result_server`（`/data/*`）を
        属性で引く。ここが解決できないと、受け口はあるのに結線が死ぬ（ISSUE-291 の形）。
        """
        inner = self.__dict__.get("_inner")
        if inner is None:  # __init__ 完了前・複製時の再帰防止
            raise AttributeError(name)
        return getattr(inner, name)
