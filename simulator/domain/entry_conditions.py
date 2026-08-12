"""E-EntryConditions: エントリ条件（Value Object・domain 層・Phase 6 F-8・TBD-11）。

TBD-11（entry = レベル2）:
    比較演算（``>`` / ``<`` の厳密不等号のみ）＋ AND 連鎖 ＋ 履歴参照 [bar−N]。
    OR・グループ化・``>=`` / ``<=`` / ``==``・インジケーター駆動は**実装しない**。

構造:
    :class:`Condition` = {indicator, shift, op, rhs}。``rhs`` は定数（number）または
    :class:`IndicatorRef`（指標同士の比較）。:class:`EntryConditions` は Condition の
    順序保存タプル（順序保存＝決定的）で、``matches`` は AND 連鎖（全真で成立）。

判定（決定性）:
    厳密不等号の生 float 比較（許容誤差なし）。参照が NaN のとき比較は必ず偽になる
    （warmup の誤シグナルを構造的に禁止する）。

検証（構築時 ConfigError）:
    op ∈ {">","<"} 違反・shift < 0（lhs / rhs 双方）は :class:`EntryConditions` 構築時に
    :class:`ConfigError`。domain は pandas/JSON を知らない（DIP: 系列参照は呼出側が
    ``sample`` コールバックで橋渡しする）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Union

from simulator.domain.exceptions import ConfigError

# TBD-11: 厳密不等号のみ。>= / <= / == は許容しない。
_OPS = frozenset({">", "<"})


@dataclass(frozen=True)
class IndicatorRef:
    """rhs が指標系列を指す場合の参照（indicator[bar−shift]）。"""

    indicator: str
    shift: int


@dataclass(frozen=True)
class Condition:
    """1 つの比較条件: ``indicator[bar−shift] op rhs``（rhs は定数 or IndicatorRef）。"""

    indicator: str
    shift: int
    op: str
    rhs: "Union[float, IndicatorRef]"


class EntryConditions:
    """条件の AND 連鎖（全真で成立）を表す Value Object。

    ``sample(name, shift) -> float`` は「系列 ``name`` の bar−shift 値」を返すコールバック。
    domain は pandas/registry を知らないため、系列参照は呼出側が注入する（DIP）。
    """

    def __init__(self, conditions: "list[Condition]") -> None:
        conds = tuple(conditions)
        for c in conds:
            if c.op not in _OPS:
                raise ConfigError(
                    f"op は {sorted(_OPS)} のいずれか（TBD-11: 厳密不等号のみ）",
                    context={"op": c.op, "indicator": c.indicator},
                )
            if c.shift < 0:
                raise ConfigError(
                    "shift は 0 以上（[bar−shift]・過去参照のみ）",
                    context={"shift": c.shift, "indicator": c.indicator},
                )
            if isinstance(c.rhs, IndicatorRef) and c.rhs.shift < 0:
                raise ConfigError(
                    "rhs 参照の shift は 0 以上",
                    context={"shift": c.rhs.shift, "indicator": c.rhs.indicator},
                )
        self._conditions = conds

    @property
    def conditions(self) -> "tuple[Condition, ...]":
        return self._conditions

    @property
    def max_shift(self) -> int:
        """lhs / rhs 双方の shift の最大（warmup 境界）。空条件は 0。"""
        shifts = [0]
        for c in self._conditions:
            shifts.append(c.shift)
            if isinstance(c.rhs, IndicatorRef):
                shifts.append(c.rhs.shift)
        return max(shifts)

    def __len__(self) -> int:
        return len(self._conditions)

    def __bool__(self) -> bool:
        return bool(self._conditions)

    def matches(self, sample: "Callable[[str, int], float]") -> bool:
        """全条件（AND 連鎖）が真なら True。空条件は真（呼出側が __bool__ で無効化する）。"""
        for c in self._conditions:
            lhs = sample(c.indicator, c.shift)
            if isinstance(c.rhs, IndicatorRef):
                rhs = sample(c.rhs.indicator, c.rhs.shift)
            else:
                rhs = c.rhs
            if c.op == ">":
                ok = lhs > rhs
            else:  # "<"（_OPS で構築時に限定済み）
                ok = lhs < rhs
            if not ok:
                return False
        return True
