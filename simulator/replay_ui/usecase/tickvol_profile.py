"""UC tickvol_profile — /tickvol_profile の usecase 結線（thin delegation）。

取引密度（ティック数）の時刻帯プロファイルと HIGH 帯を返す。独自ロジックは持たず
``TickvolProfilePort`` へ (ref, sessions, pct, until) を素通し委譲し ``(status, body)`` を返す
（ライブ側 handle_tickvol_profile 純ロジックを adapter 経由で再利用＝DRY・ライブと byte 一致）。

``until`` は必ずリビール T（単一時計 to）を透過する。``until`` が属するセッション日は集計に
含まれない（当日非参照＝因果・未来リーク防止）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from simulator.replay_ui.usecase.replay_ports import TickvolProfilePort


@dataclass
class TickvolProfileRequest:
    """/tickvol_profile の入力（until は必ずリビール T＝当日を含まない因果窓の基準）。"""
    ref: str
    sessions: Any = None
    pct: Any = None
    until: Any = None


def tickvol_profile(
    *, request: TickvolProfileRequest, profile_port: "TickvolProfilePort"
) -> "tuple[int, dict]":
    """Port へ素通し委譲して ``(status, body)`` を返す（thin・独自ロジック無し）。"""
    return profile_port.profile(
        request.ref,
        sessions=request.sessions,
        pct=request.pct,
        until=request.until,
    )
