"""UC market_profile_forming — /market_profile_forming の usecase 結線（thin delegation）。

MP サブバー tick 逐次成長のための base + forming tick 列 + active table を返す。独自ロジックは持たず
``MarketProfileFormingPort`` へ (ref,timeframe,now,base,since,bins,va,barw) を素通し委譲し、
``(status, body)`` をそのまま返す（indicator_ui の handle_market_profile_forming 純ロジックを adapter
経由で再利用＝DRY）。``now`` は必ずリビール T を透過する（因果＝T 以前のみ・未来リーク防止）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from simulator.replay_ui.usecase.replay_ports import MarketProfileFormingPort


@dataclass
class MarketProfileFormingRequest:
    """/market_profile_forming の入力（now は必ずリビール T）。"""
    ref: str
    timeframe: "str | None"
    now: "int | None"
    base: Any = None
    since: Any = None
    bins: Any = None
    va: Any = None
    barw: Any = None
    frm: Any = None  # セッション窓 MP: base 累積下限 time（当日始まり=floor(now,86400)）。既定 None=全期間。


def market_profile_forming(
    *, request: MarketProfileFormingRequest, forming_port: "MarketProfileFormingPort"
) -> "tuple[int, dict]":
    """Port へ素通し委譲して ``(status, body)`` を返す（thin・独自ロジック無し）。

    ``frm``（セッション窓 base 下限・当日始まり）は None のとき Port へ渡さない（既存 8 引数実装を壊さない
    ＝後方互換）。非 None のときのみ keyword で透過する（additive）。
    """
    extra = {} if request.frm is None else {"frm": request.frm}
    return forming_port.forming(
        request.ref,
        request.timeframe,
        request.now,
        request.base,
        request.since,
        request.bins,
        request.va,
        request.barw,
        **extra,
    )
