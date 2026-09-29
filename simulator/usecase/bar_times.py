"""足の時刻の列を epoch 秒の列へ**一括で**変換する（ISSUE-553 項目 3）。

なぜ在るか（実測 2026-09-28）: run の足の時刻を epoch 秒へ変換する処理が、run 本体と表示の
書き手の 6 箇所で 1 本ずつ `simulator.domain.bar_time.epoch_seconds` を呼んでいた。
215 万本で 1 箇所 5.4 秒・計約 32 秒。同じ列を numpy で一括変換すると 0.62 秒。

規則の所有者は domain のまま: 値は `epoch_seconds` と 1 本ずつ同じである。一括にするのは
すべての時刻が ``numpy.datetime64`` のときだけで、秒への変換は domain の
`epoch_seconds_of_datetime64_array`（スカラ版と同じ式）に委ねる。それ以外の表現が 1 つでも
混じれば 1 本ずつ `epoch_seconds` に委ねる（受理集合と例外は domain の規則そのまま）。
domain は numpy を import しない規律なので、配列を組むのは本モジュール（usecase）である。

観測の境界（検査側の設計・絶対命令 2026-09-25）: `set_observer` は変換 1 回ごとに
(変換した時刻の数, 一括だったか) を知らせる。検定の注入点であり、既定なし。
"""
from __future__ import annotations

from typing import Any, Callable, Sequence

import numpy as np

from simulator.domain.bar_time import (
    epoch_seconds,
    epoch_seconds_of_datetime64_array,
    is_numpy_datetime64,
)

_observer: "Callable[[int, bool], None] | None" = None


def set_observer(observer: "Callable[[int, bool], None] | None") -> None:
    """変換の観測口を差し替える（``None`` で外す）。検定の注入点。"""
    global _observer
    _observer = observer


def epoch_seconds_of(times: Sequence[Any]) -> "list[int]":
    """時刻の列を epoch 秒（int）の列へ（各値は `epoch_seconds(t)` と同じ）。"""
    batched = len(times) > 0 and all(is_numpy_datetime64(t) for t in times)
    if _observer is not None:
        _observer(len(times), batched)
    if batched:
        return epoch_seconds_of_datetime64_array(np.array(times))
    return [epoch_seconds(t) for t in times]


def bar_epoch_seconds(bars: Sequence[Any]) -> "list[int]":
    """Bar 列の時刻を epoch 秒の列へ（`epoch_seconds_of` を ``bar.time`` に掛ける）。"""
    return epoch_seconds_of([bar.time for bar in bars])
