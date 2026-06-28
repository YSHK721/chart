"""spec — 接点仕様（ContactSpec）と増分1の実装（MovingAverageContact）。

ContactSpec は「1 バーに対し複数の接点レベルを返す IF」として定義し、増分2（W 本体×σ 水準・
複数 σ レベル）の拡張点を確保する。増分1（EMA）は 1 バー 1 レベル（``ma[i-1]``）。

接点の定義（増分1・EMA）:
  - レベル = ``ma[i-1]``（直前確定足の MA 値）。先頭足 / 前足 MA 無しはスキップ（レベル 0 件）。
  - 跨ぎ判定（候補足）: ``low <= ma[i-1] <= high``（境界含む）。
  - tick_values: mid 恒等（mid 列をそのまま ``ma[i-1]`` と比較する。EMA 足内ラインを跨ぐ
    ⇔ 固定 ``ma[i-1]`` を跨ぐ、の代数的同値より）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class Level:
    """接点レベル 1 件。``level_id`` は同一バー内で複数レベルを識別する（増分2 で σ 水準名等）。"""
    level_id: str
    value: float


@dataclass
class ScanContext:
    """スキャンの読み取り専用コンテキスト。

    df         : OHLC DataFrame（index=datetime・列 open/high/low/close）。行は bar_times と位置対応。
    bar_times  : 各バーの UNIX 秒（df.index と同順）。
    ma_by_time : time(UNIX 秒) → MA 値。full_compute の name=='MA' 系列から構築。
    """
    df: Any
    bar_times: list
    ma_by_time: dict


@runtime_checkable
class ContactSpec(Protocol):
    """接点仕様 IF（増分2 拡張点）。バー i に対し複数レベルを返しうる。"""

    def levels(self, ctx: ScanContext, i: int) -> list:
        """バー i の候補接点レベル列。スキップ時は空リスト。"""
        ...

    def straddles(self, ctx: ScanContext, i: int, level: Level) -> bool:
        """バー i の OHLC が level を跨ぐ（候補足）か。"""
        ...

    def tick_values(self, ctx: ScanContext, i: int, ticks: list) -> list:
        """raw ティック ``[(sec, mid), ...]`` を比較系列 ``[(t, val), ...]`` へ写像する。"""
        ...


class MovingAverageContact:
    """増分1: 移動平均（EMA）× 価格。level = ma[i-1] / straddle = low<=ma_prev<=high / tick=mid 恒等。"""

    LEVEL_ID = "ma_prev"

    def levels(self, ctx: ScanContext, i: int) -> list:
        if i <= 0:                                     # 先頭足 = 前足なし = スキップ
            return []
        t_prev = ctx.bar_times[i - 1]
        ma_prev = ctx.ma_by_time.get(t_prev)
        if ma_prev is None:                            # 前足 MA 無し = スキップ
            return []
        return [Level(level_id=self.LEVEL_ID, value=float(ma_prev))]

    def straddles(self, ctx: ScanContext, i: int, level: Level) -> bool:
        row = ctx.df.iloc[i]
        return float(row["low"]) <= level.value <= float(row["high"])

    def tick_values(self, ctx: ScanContext, i: int, ticks: list) -> list:
        return [(int(t), float(m)) for t, m in ticks]  # mid 恒等
