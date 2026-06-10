"""ParamDef / Constraint 値オブジェクトと列挙（内部設計書 §3.1.1）。

標準ライブラリのみ。`@dataclass(frozen=True)`（DTO は不変）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ParamType(Enum):
    """パラメータ型（基本設計 §5.5.2）。"""

    INT = "int"
    FLOAT = "float"
    ENUM = "enum"
    BOOL = "bool"
    STRING = "string"
    COLOR = "color"
    FLOAT_LIST = "float_list"
    ENUM_LIST = "enum_list"


class ConstraintKind(Enum):
    """相関制約種別（基本設計 §5.5.5）。"""

    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"
    RANGE_OPEN = "range_open"
    MIN_VALUE = "min_value"
    CONDITIONAL = "conditional"


@dataclass(frozen=True)
class Constraint:
    """相関制約の値オブジェクト（内部設計書 §3.1.1）。

    operands: パラメータ名(str) または定数(number) のタプル。
      - lt/lte/gt/gte: (left, right)
      - range_open: (c1, param, c2)
      - min_value: (param, threshold)
      - conditional: 内側制約の operands を保持し、前提は `when` に持つ
    when: CONDITIONAL 用の前提条件（前提が真のときのみ内側制約を評価）。
    """

    kind: ConstraintKind
    operands: tuple[object, ...]
    message_key: str
    when: "Constraint | None" = None


@dataclass(frozen=True)
class ParamDef:
    """1 パラメータの定義（内部設計書 §3.1.1・基本設計 §5.5.2）。

    name: add_* の引数名と一致（例 "q_low"）。
    constraints: このパラメータを起点とする相関制約の単一定義（申し送り点4）。
    """

    name: str
    label_key: str
    type: ParamType
    default: object
    min: float | None = None
    max: float | None = None
    enum_values: tuple[object, ...] | None = None
    ui_visible: bool = True
    constraints: tuple[Constraint, ...] = field(default_factory=tuple)
