"""serve_sim_trace — 実行トレース分析 API を足した sim core（framework 層・§9.2/§9.3）。

serve_sim_settings_schema.SimSettingsSchemaApp と**同型**: 内側アプリを継承ではなく
委譲で包み（OCP）、`GET /trace/...` を 1 本足すだけで既存の配信面・API 面は素通しする
（応答 byte 不変）。**Handler もサーバ生成も Phase 2 の実体をそのまま再利用する**
（下の re-export）。

エンドポイント（sim core は prefix 除去後のパスを受ける）:
    GET /trace/{job_id}/extent                  記録の範囲・run の設定値・宣言
    GET /trace/{job_id}/points/{start}/{end}    窓の点列・事象・DD
POST は作らない（YAGNI）。分析は副作用なしに得られる。

**なぜ既存の `/data/{job_id}/{filename}` で済まさないか**（§9.2）:
    実測 1 run の trace_points.parquet は 1,036,394 行 / 9.7MB であり、front に
    parquet を読む手段はリポジトリ内に 1 つも無い（package.json の依存 0・vendor は
    lightweight-charts と chart.umd のみ）。丸ごと配って front で数千点だけ描く形は
    「作ってから捨てる」であり絶対命令に反する。窓・列はサーバが解いて返す。
"""
from __future__ import annotations

from typing import Any

from api_shared.json_get_routes import GetRouteResponder
from simulator.sim_ui.adapter.trace_api_controller import TRACE_PATH_PREFIX

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
from simulator.sim_ui.framework.serve_sim_settings_schema import (
    SIM_SETTINGS_SCHEMA_SURFACE,
)

#: `SimTraceApp` が差し出す面（内側の面 ＋ 本層の追加）。外側の包み手はこれを転送する。
SIM_TRACE_SURFACE: "tuple[str, ...]" = SIM_SETTINGS_SCHEMA_SURFACE + (
    "trace_controller",
)


@delegates_to_inner(*SIM_SETTINGS_SCHEMA_SURFACE)
class SimTraceApp:
    """内側アプリを包み、GET の JSON ルートを 1 本（`/trace`）足した面。

    ``inner``: 内側アプリ（配信面 ＋ ジョブ実行系 ＋ 指標一覧 ＋ ea-series ＋
        run-options ＋ settings-schema）。
    ``controller``: `TraceApiController`（`get(path) -> ApiResponse`）。

    内側へ転送する面は `SIM_SETTINGS_SCHEMA_SURFACE`（宣言）。宣言に無い名は解決しない。

    ルート関数が **path をそのまま受ける**のは、窓（epoch ミリ秒 2 つ）がパスセグメント
    で運ばれるためである（`trace_api_controller` の docstring 参照——sim core の GET は
    ハンドラでクエリを落とす）。prefix の一致判定と fallback は `GetRouteResponder` の
    単一ソースのままで、ここには写さない。
    """

    def __init__(self, *, inner: Any, controller: Any) -> None:
        self._inner = inner
        self._controller = controller
        verify_delegated_surface(self, inner)
        # JSON ルートを既存の面の前に挟む。/trace 以外は内側へ落ちる。
        # ルートは lambda で包む（先例 3 本と同じ形）。束縛メソッドを直に載せると
        # **構築時**に controller の面を要求することになり、面の宣言だけを満たす
        # 差し替えが構築で落ちる（置換可能性は宣言した契約の範囲でしか保証されない）。
        self.static_server = GetRouteResponder(
            routes={TRACE_PATH_PREFIX: lambda path: controller.get(path)},
            fallback=inner.static_server,
        )

    @property
    def inner(self) -> Any:
        """包んでいる内側アプリ（結線を複製していないことを確かめる面）。"""
        return self._inner

    @property
    def trace_controller(self) -> Any:
        """分析 API の controller（合成根の検定が実物の結線を確かめるための面）。"""
        return self._controller
