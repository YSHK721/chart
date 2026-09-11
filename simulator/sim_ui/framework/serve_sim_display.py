"""serve_sim_display — 表示層の配信面を足した sim コア（framework 層・Phase 4 F-6）。

Phase 3 の `serve_sim_indicators.SimIndicatorApp` を **継承ではなく委譲で包む**（OCP）。
Phase 3 は 1 バイトも変えない。

    SimIndicatorApp ──(委譲)── SimDisplayApp
                               static_server だけ StaticPrefixRoutes へ差し替える
                               それ以外の面は**宣言した名だけ**を内側へ転送する

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

# 委譲面の宣言と機構は Phase 3 の 1 箇所に閉じる（本層はそれを import して宣言するだけ）。
from simulator.sim_ui.framework.serve_sim_indicators import (
    delegates_to_inner,
    verify_delegated_surface,
)

# Handler・サーバ生成・起動は Phase 2 の実体をそのまま使う（複製しない）。
from simulator.sim_ui.framework.serve_sim_jobs import (  # noqa: F401
    make_handler,
    make_server,
    serve,
)
from simulator.sim_ui.framework.serve_sim_trace import SIM_TRACE_SURFACE
from simulator.sim_ui.framework.static_prefix_routes import StaticPrefixRoutes

#: `SimDisplayApp` が差し出す面。本層は API を 1 本も足さないので、内側の面と同一である。
#: 内側の面が増えたら**ここが追随する**のがこの層の OCP 拡張点である（宣言を 1 つ差し替える
#: だけで、転送の機構も応答 byte も変わらない。先例: ea-series → run-options →
#: settings-schema → trace の 4 本とも同じ差し替えで足りている）。
SIM_DISPLAY_SURFACE: "tuple[str, ...]" = SIM_TRACE_SURFACE


@delegates_to_inner(*SIM_TRACE_SURFACE)
class SimDisplayApp:
    """`SimIndicatorApp` を包み、静的配信の根を prefix で足したアプリケーション面。

    ``inner``: `SimIndicatorApp`（配信面 ＋ ジョブ実行系 ＋ 指標一覧）。
    ``static_routes``: ``{prefix: serve(handler, path) を持つ配信器}``。

    内側へ転送する面は `SIM_TRACE_SURFACE`（宣言）。宣言に無い名は解決しない。
    """

    def __init__(self, *, inner: Any, static_routes: "Mapping[str, Any]") -> None:
        self._inner = inner
        verify_delegated_surface(self, inner)
        # 既存の静的面（JSON ルート層を含む）を fallback にする。既存経路の応答 byte・
        # 許可根・CWE-22 防御は内側の単一ソースのまま変わらない。
        self.static_server = StaticPrefixRoutes(
            routes=static_routes, fallback=inner.static_server
        )

    @property
    def inner(self) -> Any:
        """包んでいる `SimIndicatorApp`（結線を複製していないことを確かめる面）。"""
        return self._inner
