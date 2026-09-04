"""serve_sim_ea_series — ea_name 別 registry 系列一覧 API を足した sim コア（framework 層・Phase 6 F-8）.

Phase 3 の `serve_sim_indicators.SimIndicatorApp` を **継承ではなく委譲で包む**（OCP）。
内側は 1 バイトも変えない（Phase 4 の `SimDisplayApp` と同型の包み方）。

    SimIndicatorApp ──(委譲)── SimEaSeriesApp
                               static_server だけ GetRouteResponder へ差し替える
                               それ以外の属性は __getattr__ で内側へ委譲する

**Handler もサーバ生成も Phase 2 の実体をそのまま再利用する**（下の re-export）。Handler は
`app.static_server` / `app.controller` / `app.result_server` を属性で引くだけなので、同じ属性を
出せる包み手で足りる。ここで Handler を継承し直すと段数が増え、`make_handler` / `make_server`
/ `serve` を複製することになる（ISSUE-374-1 と同型の壊れ方）。

なぜこの層を足すか（名前空間結線・依頼者承認 2026-08-12）: 実行指示パネルの指標候補源を
「選択 ea_name の registry 系列名」に一致させる単一ソースの供給口が要る。因果カタログの
`GET /indicators`（別名前空間・ea_name 非依存）は候補源にできない（選んだ指標が投入時 E-5 で
全て 400 になる・実測済み）。本層は `GET /ea-series/{ea_name}` を 1 本足すだけで、既存の
配信面・API 面は素通しする。

エンドポイント（sim core は prefix 除去後のパスを受ける）:
    GET /ea-series/{ea_name}   その EA の registry 系列名（build_ea_indicators 単一ソース）
POST は作らない（YAGNI）。系列は factory の副作用なしの探索で得られる（run と独立）。
"""
from __future__ import annotations

from typing import Any

from simulator.sim_ui.adapter.ea_series_api_controller import EA_SERIES_PATH
from api_shared.json_get_routes import GetRouteResponder

# Handler・サーバ生成・起動は Phase 2 の実体をそのまま使う（複製しない）。
from simulator.sim_ui.framework.serve_sim_jobs import (  # noqa: F401
    make_handler,
    make_server,
    serve,
)


class SimEaSeriesApp:
    """内側アプリを包み、GET の JSON ルートを 1 本（`/ea-series/{ea_name}`）足した面。

    ``inner``: 内側アプリ（`SimIndicatorApp` 等・配信面 ＋ ジョブ実行系 ＋ 指標一覧）。
    ``controller``: `EaSeriesApiController`（`list_for(path) -> ApiResponse`）。
    """

    def __init__(self, *, inner: Any, controller: Any) -> None:
        self._inner = inner
        self._controller = controller
        # JSON ルートを既存の static 面の前に挟む。静的配信・許可根・CWE-22 防御は内側の
        # 単一ソースのまま。/ea-series 以外は fallback（内側の static_server）へ落ちる。
        self.static_server = GetRouteResponder(
            routes={EA_SERIES_PATH: lambda path: controller.list_for(path)},
            fallback=inner.static_server,
        )

    @property
    def inner(self) -> Any:
        """包んでいる内側アプリ（結線を複製していないことを確かめる面）。"""
        return self._inner

    @property
    def ea_series_controller(self) -> Any:
        """系列一覧の controller（合成根の検定が実物の結線を確かめるための面）。"""
        return self._controller

    def __getattr__(self, name: str) -> Any:
        """自分が持たない属性は内側アプリへ委譲する。

        Handler は `app.controller`（ジョブ API）・`app.result_server`（`/data/*`）を属性で
        引く。ここが解決できないと、受け口はあるのに結線が死ぬ（ISSUE-291 の形）。
        """
        inner = self.__dict__.get("_inner")
        if inner is None:  # __init__ 完了前・複製時の再帰防止
            raise AttributeError(name)
        return getattr(inner, name)
