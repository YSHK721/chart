"""ReplayCatalogApp — 指標カタログのルートを担う App（ISSUE-479 Wave2 3-4 / S-3）。

serve_replay_candles と同じ様式で ``/catalog`` の 1 ルートだけを持つ。応答の形と例外分類は
分割前の do_GET から逐語で移してあり、応答は 1 バイトも変わらない。

Port が未注入なら本ルートを持たず ``fallback`` へ落ちる（分割前の
``and app.catalog_enabled`` と同値）。

業務の入口（replay backend の App）は ``core`` として明示で受け取り、必要な面はクラス属性の
宣言表で表明する（ISSUE-502 段階 5B: 透過委譲の撤去）。``core`` の catalog は成否の分類つき
結果を返し、HTTP ステータスへの写像は ``http_response_for`` の 1 箇所へ委ねる
（本 App は番号を持たない）。

重い処理のワーカーとロックは ``core`` が 1 つだけ持つ（本 App は自前で作らない）。
"""
from __future__ import annotations

from typing import Any

from simulator.replay_ui.framework.serve_replay import (
    _error_response,
    http_response_for,
    require_core_members,
    write_replay_json,
)
from api_shared.json_get_routes import GetRouteResponder

#: 本 App が持つルート。
CATALOG_PATH = "/catalog"


class ReplayCatalogApp:
    """指標カタログのルートを持ち、外れた path を ``fallback`` へ落とす面。

    ``core``: 業務の入口（`ReplayApp`）。``fallback``: 1 つ前の配信面。
    """

    #: 本 App が ``core`` へ要求する面（生成時に不足を検査する＝起動時 fail-stop）。
    REQUIRED_CORE_MEMBERS = ("catalog", "catalog_enabled")

    def __init__(self, *, core: Any, fallback: Any) -> None:
        require_core_members(core, self.REQUIRED_CORE_MEMBERS, owner=type(self).__name__)
        self._core = core
        routes: "dict[str, Any]" = {}
        if core.catalog_enabled:
            routes[CATALOG_PATH] = self._catalog
        self.static_server = GetRouteResponder(
            routes=routes, fallback=fallback, writer=write_replay_json
        )

    @property
    def core(self) -> Any:
        """業務の入口（結線を複製していないことを確かめる面）。"""
        return self._core

    def _catalog(self, _path: str) -> "tuple[int, Any]":
        # 指標 param の既定値＋variant ごとの受理 param（ISSUE-278 #8/#4）。front は
        #   これで表示コントロールと送信 params を決める。実体はライブ側 controller。
        try:
            return http_response_for(self._core.catalog())
        except Exception as e:  # noqa: BLE001 — 例外分類は _error_response へ集約（ISSUE-097 🟡-4）
            return _error_response(e)
