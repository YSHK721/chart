"""serve_sim_indicators — 指標一覧 API を足した sim コア（framework 層・Phase 3 F-5）。

Phase 2 の `serve_sim_jobs` を **継承ではなく委譲で包む**（OCP）。`serve_sim_jobs` は
1 バイトも変えない。

    SimJobApp ──(委譲)── SimIndicatorApp
                          static_server だけ GetRouteResponder へ差し替える
                          それ以外の属性は __getattr__ で内側へ委譲する

**継承しない理由**: Phase 2 は Phase 1 を継承で拡張した（`SimJobApp(SimApp)` /
`JobHandler(Base)`）。ここでさらに継承を重ねると、Handler が 3 段になり
`make_handler` / `make_server` / `serve` の 15 行を三重に複製することになる
（ISSUE-374-1 と同型の壊れ方）。**Handler もサーバ生成も Phase 2 のものをそのまま
再利用する**（下の re-export）。Handler は `app.static_server` / `app.controller` /
`app.result_server` を属性で引くだけなので、同じ属性を出せる包み手で足りる。

エンドポイント（sim core は prefix 除去後のパスを受ける）:
    GET /indicators   因果性検定の結果を**系列単位**で返す一覧（未通過も reason つきで
                      含む・503 は台帳不在）
POST は作らない（YAGNI・§11.4）。検定は CLI が行い、結果は台帳が持つ。
"""
from __future__ import annotations

from typing import Any

from simulator.sim_ui.framework.json_get_routes import GetRouteResponder

# Handler・サーバ生成・起動は Phase 2 の実体をそのまま使う（複製しない）。
from simulator.sim_ui.framework.serve_sim_jobs import (  # noqa: F401
    make_handler,
    make_server,
    serve,
)

#: 指標一覧のパス（sim core が受ける prefix 除去後の形）。
INDICATORS_PATH = "/indicators"


class SimIndicatorApp:
    """`SimJobApp` を包み、GET の JSON ルートを 1 本足したアプリケーション面。

    ``inner``: `SimJobApp`（配信面 ＋ ジョブ実行系）。
    ``controller``: `IndicatorApiController`（`list() -> ApiResponse`・系列単位の一覧）。
    """

    def __init__(self, *, inner: Any, controller: Any) -> None:
        self._inner = inner
        self._controller = controller
        # 静的面の前に JSON ルートを挟む。静的配信そのもの（許可根・応答 byte・
        # CWE-22 防御）は内側の StaticFileServer が単一ソースのまま担う。
        self.static_server = GetRouteResponder(
            routes={INDICATORS_PATH: lambda _path: controller.list()},
            fallback=inner.static_server,
        )

    @property
    def inner(self) -> Any:
        """包んでいる `SimJobApp`（結線を複製していないことを確かめる面）。"""
        return self._inner

    @property
    def indicator_controller(self) -> Any:
        """指標一覧の controller（合成根の検定が実物の結線を確かめるための面）。"""
        return self._controller

    @property
    def causality_ledger(self) -> Any:
        """一覧の出所となる因果性台帳（`SimJobApp.ledger` と対称の公開面）。"""
        return self._controller.ledger

    def __getattr__(self, name: str) -> Any:
        """自分が持たない属性は内側の `SimJobApp` へ委譲する。

        Handler は `app.controller`（ジョブ API）・`app.result_server`（`/data/*`）を
        属性で引く。ここが解決できないと、受け口はあるのに結線が死ぬ（ISSUE-291 の形）。
        """
        inner = self.__dict__.get("_inner")
        if inner is None:  # __init__ 完了前・複製時の再帰防止
            raise AttributeError(name)
        return getattr(inner, name)
