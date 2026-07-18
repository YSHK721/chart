"""MarketProfileGateway — MarketProfilePort 実装（indicator_ui bridge 委譲）。

CLEAN_ARCH §6: MP の計算（compute_candle_profile / market_profile_dwell / as-seen-at-t 切断）は
indicator_ui の ``handle_market_profile`` 純ロジックに一元化されている。本 gateway はそれを
``_indicator_ui_bridge`` 経由で read-only 再利用し、usecase へ ``(status, body)`` を返す（DRY・無改変）。
serve は本 gateway を Port として注入し、bridge を直 import しない（DIP・market_profile_forming_gateway と同型）。

``to`` は必ずリビール T を渡す（因果＝as-seen-at-t＝T 以前に観測できた足のみ・未来リーク防止）。予約語
``from``（frm）／``today``／``sessions`` は kwargs 経由で透過する（handle_market_profile は常に **kwargs を
受けるため None 透過も無害＝present server の呼び出しと同一）。
"""
from __future__ import annotations

from typing import Any, Callable

from simulator.replay_ui.adapter import _indicator_ui_bridge


class MarketProfileGateway:
    """MarketProfilePort 実装。bridge の handle_market_profile へ委譲する。"""

    def __init__(
        self,
        api_path: Any = None,
        repo_root: Any = None,
        bridge_loader: "Callable[..., Any] | None" = None,
    ) -> None:
        self._api_path = api_path
        self._repo_root = repo_root
        # 既定は MP handlers のみのアクセサ（ISSUE-136 ISP: dataset/compute Facade を import しない）。
        # テストは fake loader を注入して indicator_ui 実体に依存しない。
        self._loader = (
            bridge_loader if bridge_loader is not None else _indicator_ui_bridge.load_mp_handlers
        )

    def profile(
        self,
        ref: str,
        timeframe: "str | None",
        limit: Any,
        bins: Any,
        va: Any,
        src: Any,
        barw: Any,
        to: Any,
        frm: Any = None,
        today: Any = None,
        sessions: Any = None,
    ) -> "tuple[int, dict]":
        bridge = self._loader(self._api_path, self._repo_root)
        # 予約語 from は kwargs 経由で透過する（present server の呼び出しと同一・handle_market_profile が
        #   from/today/sessions を **kwargs で受ける）。None も透過（handle 側で現行挙動＝後方互換）。
        return bridge.handle_market_profile(
            ref, timeframe=timeframe, limit=limit, bins=bins, va=va, src=src,
            barw=barw, to=to,
            **{"from": frm, "today": today, "sessions": sessions},
        )
