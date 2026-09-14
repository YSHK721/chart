"""run 中の保有玉（`OpenTrade`）。UC-001 の協働クラスが共有する唯一の定義点。

なぜ独立モジュールか（ISSUE-502 段階 4A）:
    保有玉は 1 つのアクターの持ち物ではない——約定執行が**建て**、SL/TP 監視が**読み**、
    決済記帳が **position を差し替え**、建玉変更が **sl/tp を書き換える**。4 者が同じ
    値の形に合意している必要があるため、定義点はそのいずれでもない中立の場所に置く。
    Interactor（run ライフサイクル）の中に置いたままにすると、協働クラスが
    Interactor を import することになり依存が逆流する。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class OpenTrade:
    """run 中の保有玉（Position + SL/TP・建値時刻・建てた bar_index）。"""

    position: Any
    sl: float | None
    tp: float | None
    entry_time: Any
    entry_price: float
    opened_bar_index: int
    # 建てた足のティックで SL/TP 監視を抑止するか（成行=True で従来どおり「発注足は次tick
    # 以降まで監視しない」。ペンディング約定=False で「約定ティックより後は同一足内でも監視」）。
    # ペンディングは足途中の特定ティックで約定し、その後の同足ティックで SL/TP が決済され
    # 得る（実 MT5: 1 本の M1 足内で trigger→fill→SL/TP が連鎖。2603-01 journal で実証）。
    skip_entry_bar: bool = True
    # ペンディング約定時の建てたティックの序数（0=open）。同一足では「この序数より後」の
    # ティックでのみ SL/TP 監視する（約定ティック自身では判定しない＝実 MT5 server 整合）。
    opened_tick_ordinal: int = -1
