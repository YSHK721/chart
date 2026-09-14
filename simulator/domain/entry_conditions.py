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

import operator
from dataclasses import dataclass
from typing import Callable, Protocol, Union, runtime_checkable

from simulator.domain.exceptions import ConfigError

# TBD-11: 厳密不等号のみ。>= / <= / == は許容しない。
#
# 宣言（許容集合）と評価（比較の実体）を 1 つの表で持つ。別々に持つと、片方だけを
# 増やしても構造上は矛盾せず静かに壊れる（許容集合に ">=" を足すと、評価側の
# else 分岐がそれを "<" として扱う）。**本表は拡張点ではない**: TBD-11 が
# >= / <= / == を不採用と明文化しており、増やすときは TBD の改訂が先である。
_OPS: "dict[str, Callable[[float, float], bool]]" = {
    ">": operator.gt,
    "<": operator.lt,
}


@runtime_checkable
class Rhs(Protocol):
    """比較の右辺が満たす振る舞い（定数と指標参照を同一に扱うための契約）。

    呼び出し側が種別を尋ねない（isinstance で分岐しない）ようにするための最小面。
    種別が増えても本 Protocol の実装を足すだけで、検証・warmup 境界・評価の
    どこも書き換わらない（OCP）。
    """

    def shift_of(self) -> int:
        """warmup 境界に寄与する過去参照量（定数は 0）。"""

    def value(self, sample: "Callable[[str, int], float]") -> float:
        """比較に使う値（定数は自身・指標参照は sample の結果）。"""

    def validate(self) -> None:
        """構築時の不変条件を検査する（違反時 ConfigError）。"""


@dataclass(frozen=True)
class IndicatorRef:
    """rhs が指標系列を指す場合の参照（indicator[bar−shift]）。"""

    indicator: str
    shift: int

    def shift_of(self) -> int:
        return self.shift

    def value(self, sample: "Callable[[str, int], float]") -> float:
        return sample(self.indicator, self.shift)

    def validate(self) -> None:
        if self.shift < 0:
            raise ConfigError(
                "rhs 参照の shift は 0 以上",
                context={"shift": self.shift, "indicator": self.indicator},
            )


class Constant(float):
    """rhs が定数の場合の値（``float`` そのものとして振る舞う Value Object）。

    ``float`` を継承するのは、既存の外部契約——``Condition.rhs`` が生の数値と等しく
    比較でき、比較演算の意味論が 1 ビットも変わらないこと——を保つためである
    （包む形にすると ``cond.rhs == 5.0`` が偽になり、既存の検定と実行時比較の
    両方が変わってしまう）。
    """

    def shift_of(self) -> int:
        return 0

    def value(self, sample: "Callable[[str, int], float]") -> float:
        return float(self)

    def validate(self) -> None:
        return None


@dataclass(frozen=True)
class Condition:
    """1 つの比較条件: ``indicator[bar−shift] op rhs``（rhs は定数 or IndicatorRef）。

    構築時に生の数値を :class:`Constant` へ正規化する。外部の構築 API は不変で、
    ``rhs=5.0`` はこれまでどおり書ける（正規化後も ``== 5.0`` は真である）。
    """

    indicator: str
    shift: int
    op: str
    rhs: "Union[float, IndicatorRef]"

    def __post_init__(self) -> None:
        if not isinstance(self.rhs, Rhs):        # 生の数値だけを包む（種別は尋ねない）
            object.__setattr__(self, "rhs", Constant(self.rhs))


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
            c.rhs.validate()
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
            shifts.append(c.rhs.shift_of())
        return max(shifts)

    def __len__(self) -> int:
        return len(self._conditions)

    def __bool__(self) -> bool:
        return bool(self._conditions)

    def matches(self, sample: "Callable[[str, int], float]") -> bool:
        """全条件（AND 連鎖）が真なら True。空条件は真（呼出側が __bool__ で無効化する）。"""
        for c in self._conditions:
            lhs = sample(c.indicator, c.shift)
            rhs = c.rhs.value(sample)
            if not _OPS[c.op](lhs, rhs):   # 表が宣言でも評価でもある（構築時に限定済み）
                return False
        return True
