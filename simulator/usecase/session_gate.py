"""SessionGate: closed_bars セッション判定の単一責務化（ISSUE-094 🔴-1）。

RunBacktestInteractor が bar 用と tick 用の 2 エンジンに分かれていた頃、「当該バーが
市場閉鎖（トレードセッション外）か」の判定は両方へ散在していた。それを単一オブジェクトへ
集約する（エンジン自体は ISSUE-479 Wave2 4-10 で 1 本になった）。判定は集合メンバシップ
のみで、浮動小数演算・約定ロジックを一切含まない（純判定）。

セッション規約（実 MT5・ISSUE 群で実証）:
    - 顧客注文（新規約定・ペンディング fill・SL/TP）はトレードセッション外では実行しない。
    - stop-out（ブローカーのリスク清算）と含み損評価はセッション外でも継続する。
本ゲートは前者（顧客注文の抑止）に用いる「閉鎖バー集合」を保持し、`is_closed` で
判定を返すのみ。stop-out/equity の継続可否は呼び出し側が判断する。

usecase 層は domain のみ依存可。本モジュールは domain も import しない純粋判定。
"""
from __future__ import annotations

from typing import Any


class SessionGate:
    """閉鎖バー index 集合を保持し、当該バーが市場閉鎖かを判定する。

    振る舞いは「集合メンバシップ判定」のみ。カレンダー未注入時は空集合＝常時開場
    （既定経路 byte-identical）。
    """

    def __init__(self, closed_bars: "set[int]") -> None:
        self._closed_bars = closed_bars

    def is_closed(self, bar_index: int) -> bool:
        """当該バーが市場閉鎖（新規成行・ペンディング fill・SL/TP を抑止）か。"""
        return bar_index in self._closed_bars

    @property
    def closed_bars(self) -> "set[int]":
        """保持する閉鎖バー index 集合（読み取り用）。"""
        return self._closed_bars

    @classmethod
    def from_calendar(cls, session_calendar: Any, bars: list) -> "SessionGate":
        """カレンダー（DI・既定 None=常時開場）から閉鎖バー集合を導出する。

        session_calendar が None のときは空集合（既定経路 byte-identical）。
        非 None のときは `closed_bar_indices(bars)` へ委譲する（従来の
        RunBacktestInteractor._closed_bars と同一の導出規則）。
        """
        if session_calendar is None:
            return cls(set())
        return cls(session_calendar.closed_bar_indices(bars))
