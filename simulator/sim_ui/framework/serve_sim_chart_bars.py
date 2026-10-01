"""serve_sim_chart_bars — 足の API を足した sim core（framework 層・ISSUE-552/554 段階 2-1）。

`simulator/sim_ui/framework/serve_sim_trace.py` の SimTraceApp と**同型**: 内側アプリを継承では
なく委譲で包み（OCP）、`GET /chart-bars/...` を 1 本足すだけで既存の配信面・API 面は素通しする
（応答 byte 不変）。Handler もサーバ生成も Phase 2 の実体をそのまま再利用する（下の re-export）。

エンドポイント（sim core は prefix 除去後のパスを受ける）:
    GET /chart-bars/{job_id}/extent               行数・列・指標・1 回の上限（宣言）
    GET /chart-bars/{job_id}/rows/{start}/{end}   位置の半開区間の列
POST は作らない（YAGNI）。

**なぜ既存の `/data/{job_id}/{filename}` で済まさないか**: front に parquet を読む手段は無く、
丸ごと配って画面が一部だけ描く形は実 UI でタブを落とした（ISSUE-552・2,152,183 本）。
範囲はサーバが解いて返す。
"""
from __future__ import annotations

from typing import Any

from api_shared.json_get_routes import GetRouteResponder
from simulator.sim_ui.adapter.chart_bars_api_controller import CHART_BARS_PATH_PREFIX

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

#: `SimChartBarsApp` が差し出す面（内側の面 ＋ 本層の追加）。外側の包み手はこれを転送する。
SIM_CHART_BARS_SURFACE: "tuple[str, ...]" = SIM_TRACE_SURFACE + (
    "chart_bars_controller",
)


@delegates_to_inner(*SIM_TRACE_SURFACE)
class SimChartBarsApp:
    """内側アプリを包み、GET の JSON ルートを 1 本（`/chart-bars`）足した面。

    ``inner``: 内側アプリ（分析 API までを持つ面）。
    ``controller``: `ChartBarsApiController`（`get(path) -> ApiResponse`）。

    内側へ転送する面は `SIM_TRACE_SURFACE`（宣言）。宣言に無い名は解決しない。
    prefix の一致判定と fallback は `GetRouteResponder` の単一ソースのままで、ここには写さない。
    """

    def __init__(self, *, inner: Any, controller: Any) -> None:
        self._inner = inner
        self._controller = controller
        verify_delegated_surface(self, inner)
        # ルートは lambda で包む（先例と同じ形。束縛メソッドを直に載せると構築時に
        # controller の面を要求することになる）。
        self.static_server = GetRouteResponder(
            routes={CHART_BARS_PATH_PREFIX: lambda path: controller.get(path)},
            fallback=inner.static_server,
        )

    @property
    def inner(self) -> Any:
        """包んでいる内側アプリ（結線を複製していないことを確かめる面）。"""
        return self._inner

    @property
    def chart_bars_controller(self) -> Any:
        """足の API の controller（合成根の検定が実物の結線を確かめるための面）。"""
        return self._controller
