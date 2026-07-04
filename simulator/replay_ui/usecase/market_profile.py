"""UC market_profile — /market_profile の usecase 結線（thin delegation）。

normal/sessions/replay モードの Market Profile を返す。独自ロジックは持たず ``MarketProfilePort`` へ
(ref,timeframe,limit,bins,va,src,barw,to,frm,today,sessions) を素通し委譲し、``(status, body)`` を
そのまま返す（indicator_ui の handle_market_profile 純ロジックを adapter 経由で再利用＝DRY）。
``to`` は必ずリビール T を透過する（因果＝as-seen-at-t・T 以前のみ・未来リーク防止）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from simulator.replay_ui.usecase.replay_ports import MarketProfilePort


@dataclass
class MarketProfileRequest:
    """/market_profile の入力（to は必ずリビール T＝as-seen-at-t）。"""
    ref: str
    timeframe: "str | None"
    limit: Any = None
    bins: Any = None
    va: Any = None
    src: Any = None
    barw: Any = None
    to: Any = None
    frm: Any = None      # ローリング窓の下限 time（UNIX 秒・省略時 None=全期間）。増分2 A。
    today: Any = None    # スナップショット当日強調（'1' で today[]/today_max）。増分2 C。
    sessions: Any = None  # 日別プロファイル分割（'1' で sessions[]）。


def market_profile(
    *, request: MarketProfileRequest, profile_port: "MarketProfilePort"
) -> "tuple[int, dict]":
    """Port へ素通し委譲して ``(status, body)`` を返す（thin・独自ロジック無し）。"""
    return profile_port.profile(
        request.ref,
        request.timeframe,
        request.limit,
        request.bins,
        request.va,
        request.src,
        request.barw,
        request.to,
        frm=request.frm,
        today=request.today,
        sessions=request.sessions,
    )
