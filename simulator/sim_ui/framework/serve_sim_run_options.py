"""serve_sim_run_options — run config 選択肢 API を足した sim コア（framework 層・Phase 6 拡張）.

`serve_sim_ea_series.SimEaSeriesApp` と**同型**: 内側アプリを継承ではなく委譲で包み（OCP）、
`GET /run-options` を 1 本足すだけで既存の配信面・API 面は素通しする（byte 不変）。
**Handler もサーバ生成も Phase 2 の実体をそのまま再利用する**（下の re-export）。

エンドポイント（sim core は prefix 除去後のパスを受ける）:
    GET /run-options   実行指示フォームの選択肢（datasets プロファイル＋ea_names）
POST は作らない（YAGNI）。選択肢は副作用なしに得られる（run と独立）。
"""
from __future__ import annotations

from typing import Any

from simulator.sim_ui.adapter.run_options_api_controller import RUN_OPTIONS_PATH
from api_shared.json_get_routes import GetRouteResponder

# 委譲面の宣言と機構は Phase 3 の 1 箇所に閉じる（本層はそれを import して宣言するだけ）。
from simulator.sim_ui.framework.serve_sim_ea_series import SIM_EA_SERIES_SURFACE
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

#: `SimRunOptionsApp` が差し出す面（内側の面 ＋ 本層の追加）。外側の包み手はこれを転送する。
SIM_RUN_OPTIONS_SURFACE: "tuple[str, ...]" = SIM_EA_SERIES_SURFACE + (
    "run_options_controller",
)


@delegates_to_inner(*SIM_EA_SERIES_SURFACE)
class SimRunOptionsApp:
    """内側アプリを包み、GET の JSON ルートを 1 本（`/run-options`）足した面。

    ``inner``: 内側アプリ（配信面 ＋ ジョブ実行系 ＋ 指標一覧 ＋ ea-series）。
    ``controller``: `RunOptionsApiController`（`list() -> ApiResponse`）。

    内側へ転送する面は `SIM_EA_SERIES_SURFACE`（宣言）。宣言に無い名は解決しない。
    """

    def __init__(self, *, inner: Any, controller: Any) -> None:
        self._inner = inner
        self._controller = controller
        verify_delegated_surface(self, inner)
        # JSON ルートを既存の static 面の前に挟む。/run-options 以外は内側へ落ちる。
        self.static_server = GetRouteResponder(
            routes={RUN_OPTIONS_PATH: lambda _path: controller.list()},
            fallback=inner.static_server,
        )

    @property
    def inner(self) -> Any:
        """包んでいる内側アプリ（結線を複製していないことを確かめる面）。"""
        return self._inner

    @property
    def run_options_controller(self) -> Any:
        """選択肢の controller（合成根の検定が実物の結線を確かめるための面）。"""
        return self._controller
