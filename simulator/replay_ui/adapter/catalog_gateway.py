"""CatalogGateway — CatalogPort 実装（indicator_ui bridge 委譲・tickvol_profile_gateway と同型）。

CLEAN_ARCH §6: 指標 param の既定値と **variant ごとの受理 param（paramScopes）** の単一情報源は
ライブ側 back（``call_binding._TABLE``）にある。本 gateway はライブ controller ``handle_catalog``
を ``api_loader`` 経由で read-only 再利用し、usecase へ ``(status, body)`` を返す
（DRY・無改変＝ライブとリプレイで応答が byte 一致する）。serve は本 gateway を Port として注入し
bridge を直 import しない（DIP）。

ISSUE-278 #8/#4: standalone replay は ``GET /catalog`` を持たず front も ``catalog.load()`` を
呼んでいなかったため、variant が受理しない param を送っていた（ライブ側 back が無言で捨てていた
ため無症状だった）。無言破棄の撤去により、この欠落は ``validation`` エラーとして表面化する。
"""
from __future__ import annotations

from typing import Any, Callable

from indigators.indicator_ui import api_loader


class CatalogGateway:
    """CatalogPort 実装。bridge の handle_catalog へ委譲する。"""

    def __init__(
        self,
        api_path: Any = None,
        repo_root: Any = None,
        bridge_loader: "Callable[..., Any] | None" = None,
    ) -> None:
        self._api_path = api_path
        self._repo_root = repo_root
        # 既定は当該 handler のみのアクセサ（ISP: dataset/compute/MP Facade を import しない）。
        # テストは fake loader を注入して indicator_ui 実体に依存しない。
        self._loader = (
            bridge_loader
            if bridge_loader is not None
            else api_loader.load_catalog_handler
        )

    def catalog(self) -> "tuple[int, dict]":
        bridge = self._loader(self._api_path, self._repo_root)
        return bridge.handle_catalog()
