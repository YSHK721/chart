"""ReplayCatalogApp — 指標カタログのルートを担う App（ISSUE-479 Wave2 3-4 / S-3）。

serve_replay_candles と同じ様式で ``/catalog`` の 1 ルートだけを持つ。応答の形と例外分類は
分割前の do_GET から逐語で移してあり、応答は 1 バイトも変わらない。

Port が未注入なら本ルートを持たず静的配信へフォールバックする（分割前の
``and app.catalog_enabled`` と同値）。

重い処理のワーカーとロックは内側の単一インスタンスを共有する（自前で作らない）。
"""
from __future__ import annotations

from typing import Any

from simulator.replay_ui.framework.serve_replay import (
    _error_response,
    write_replay_json,
)
from simulator.sim_ui.framework.json_get_routes import GetRouteResponder

#: 本 App が持つルート。
CATALOG_PATH = "/catalog"


class ReplayCatalogApp:
    """内側 App を包み、指標カタログのルートを JSON 経路として前置きした面。"""

    def __init__(self, *, inner: Any) -> None:
        self._inner = inner
        routes: "dict[str, Any]" = {}
        if inner.catalog_enabled:
            routes[CATALOG_PATH] = self._catalog
        self.static_server = GetRouteResponder(
            routes=routes, fallback=inner.static_server, writer=write_replay_json
        )

    @property
    def inner(self) -> Any:
        """包んでいる内側 App（結線を複製していないことを確かめる面）。"""
        return self._inner

    def _catalog(self, _path: str) -> "tuple[int, Any]":
        # 指標 param の既定値＋variant ごとの受理 param（ISSUE-278 #8/#4）。front は
        #   これで表示コントロールと送信 params を決める。実体はライブ側 controller。
        try:
            return self._inner.catalog()
        except Exception as e:  # noqa: BLE001 — 例外分類は _error_response へ集約（ISSUE-097 🟡-4）
            return _error_response(e)

    def __getattr__(self, name: str) -> Any:
        """自分が持たない属性は内側 App へ委譲する（結線を殺さない）。"""
        inner = self.__dict__.get("_inner")
        if inner is None:  # __init__ 完了前・複製時の再帰防止
            raise AttributeError(name)
        return getattr(inner, name)
