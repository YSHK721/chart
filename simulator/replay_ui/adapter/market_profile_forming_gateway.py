"""MarketProfileFormingGateway — MarketProfileFormingPort 実装（indicator_ui bridge 委譲）。

CLEAN_ARCH §6: MP forming の計算（forming_bar / market_profile_forming / market_profile_dwell）は
indicator_ui の ``handle_market_profile_forming`` 純ロジックに一元化されている。本 gateway はそれを
``_indicator_ui_bridge`` 経由で read-only 再利用し、usecase へ ``(status, body)`` を返す（DRY・無改変）。
serve は本 gateway を Port として注入し、bridge を直 import しない（DIP）。

``now`` は必ずリビール T を渡す（因果＝T 以前のみ・未来リーク防止）。base は controller が
``to=formingStart-1`` で forming を排除済（二重計上なし）。
"""
from __future__ import annotations

from typing import Any, Callable

from simulator.replay_ui.adapter import _indicator_ui_bridge


class MarketProfileFormingGateway:
    """MarketProfileFormingPort 実装。bridge の handle_market_profile_forming へ委譲する。"""

    def __init__(
        self,
        api_path: Any = None,
        repo_root: Any = None,
        bridge_loader: "Callable[..., Any] | None" = None,
    ) -> None:
        self._api_path = api_path
        self._repo_root = repo_root
        # 既定は実 bridge の load。テストは fake loader を注入して indicator_ui 実体に依存しない。
        self._loader = bridge_loader if bridge_loader is not None else _indicator_ui_bridge.load

    def forming(
        self,
        ref: str,
        timeframe: "str | None",
        now: "int | None",
        base: Any,
        since: Any,
        bins: Any,
        va: Any,
        barw: Any,
        frm: Any = None,
    ) -> "tuple[int, dict]":
        bridge = self._loader(self._api_path, self._repo_root)
        # frm（セッション窓 base 下限・当日始まり）は None のとき bridge へ渡さない（既存 export の後方互換）。
        #   非 None のときのみ keyword で透過する（additive）。controller が予約語 from へ写像する。
        extra = {} if frm is None else {"frm": frm}
        return bridge.handle_market_profile_forming(
            ref, timeframe=timeframe, since=since, base=base, now=now, bins=bins, va=va, barw=barw,
            **extra,
        )
