"""scan_contacts の境界（Port）抽象（CLEAN_ARCH §5・vol_band_ports.py 流儀）。

usecase は tick 源・MA 計算という偶有的技術を知らない。本モジュールは注入対象を Protocol
として表明する（ports.py は無改変＝ISP: 新 Port は別ファイル）。numpy/pandas はここに漏らさ
ない（実装は adapter/tools 側）。戻り値は plain 値のみ。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TickWindowSource(Protocol):
    """足内窓 ``[start, end)``（UNIX 秒）の実ティックを ``[(sec, mid), ...]`` で返す callable。

    tick 源（parquet / CSV 等）と mid=(bid+ask)/2 の算出は adapter/tools 境界に閉じる。
    該当ティックなしは空 list。full_scan 時のみ engine から呼ばれる。
    """

    def __call__(self, start: int, end: int) -> "list[tuple[int, float]]":
        ...
