"""TickvolProfileGateway — TickvolProfilePort 実装（indicator_ui bridge 委譲）。

CLEAN_ARCH §6: 取引密度プロファイルの集計（session_offset_profile / concentration_bands）と検証は
indicator_ui の ``handle_tickvol_profile`` 純ロジックに一元化されている。本 gateway はそれを
``api_loader`` 経由で read-only 再利用し、usecase へ ``(status, body)`` を返す
（DRY・無改変＝ライブとリプレイの帯が byte 一致する）。serve は本 gateway を Port として注入し
bridge を直 import しない（DIP・market_profile_gateway と同型）。

``until`` は必ずリビール T を渡す（当日を集計に含めない＝因果・未来リーク防止）。
"""
from __future__ import annotations

from typing import Any, Callable

from indigators.indicator_ui import api_loader


class TickvolProfileGateway:
    """TickvolProfilePort 実装。bridge の handle_tickvol_profile へ委譲する。"""

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
            else api_loader.load_tickvol_handler
        )

    def profile(
        self,
        ref: str,
        sessions: Any = None,
        pct: Any = None,
        until: Any = None,
    ) -> "tuple[int, dict]":
        bridge = self._loader(self._api_path, self._repo_root)
        return bridge.handle_tickvol_profile(ref, sessions, pct, until)
