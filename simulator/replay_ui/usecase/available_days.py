"""UC-R6 available_days — /available_days の usecase 結線（AvailableDaysPort へ委譲）。

リプレイバーのカレンダー（再生開始日の選択）が「データが無い日をグレーアウトする」ために使う。
足が 1 本以上存在する UTC 日を昇順で返すだけの薄い Interactor（domain のみ依存）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulator.replay_ui.usecase.replay_ports import AvailableDaysPort


@dataclass
class AvailableDaysRequest:
    """/available_days の入力。"""
    ref: str
    timeframe: "str | None"


def available_days(
    *, request: AvailableDaysRequest, days_port: "AvailableDaysPort"
) -> "list[str]":
    """``["YYYY-MM-DD", ...]``（UTC・昇順）を返す（Port へ忠実委譲）。"""
    return days_port.load_days(request.ref, request.timeframe)
