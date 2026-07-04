"""spec — 接点仕様（ContactSpec）と増分1の実装（MovingAverageContact）（純・stdlib のみ）。

参照実装 prototype_260626-01/contact_scan/spec.py と規約 bit 一致。試作は ``ScanContext.df``
（pandas）依存だが、本移植では ``highs / lows / closes``（plain list）へ置換し usecase 層から
pandas を排除する。挙動（level=ma[i-1] / 先頭・前足MA無しスキップ / straddle=low<=level<=high /
tick=mid 恒等 / LEVEL_ID="ma_prev"）は不変。

ContactSpec は「1 バーに対し複数の接点レベルを返す IF」として定義し、増分2（W 本体×σ 水準・
複数 σ レベル）の拡張点を確保する。増分1（EMA）は 1 バー 1 レベル（``ma[i-1]``）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Level:
    """接点レベル 1 件。``level_id`` は同一バー内で複数レベルを識別する（増分2 で σ 水準名等）。"""
    level_id: str
    value: float


@dataclass
class ScanContext:
    """スキャンの読み取り専用コンテキスト（plain 配列・pandas 非依存）。

    highs / lows / closes : 各バーの高値 / 安値 / 終値（bar_times と位置対応）。
    bar_times  : 各バーの UNIX 秒（昇順・highs 等と同順）。
    ma_by_time : time(UNIX 秒) → MA 値。
    """
    highs: list
    lows: list
    closes: list
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
        return float(ctx.lows[i]) <= level.value <= float(ctx.highs[i])

    def tick_values(self, ctx: ScanContext, i: int, ticks: list) -> list:
        return [(int(t), float(m)) for t, m in ticks]  # mid 恒等
