"""ConstraintEvaluator / Violation（内部設計書 §3.1.5・申し送り点4 単一定義）。

CONSTRAINT 評価の唯一の実装。API 入口（§7.3）とフロント事前検証（F-11）は本ロジックを共有する。
標準ライブラリのみ。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from domain.param_def import Constraint, ConstraintKind, ParamDef, ParamType


@dataclass(frozen=True)
class Violation:
    """制約違反（内部設計書 §3.1.5・§6.3.4 violations 構造に整合）。"""

    param: str
    constraint: str
    expected: str
    actual: object


# 2 オペランド比較種別の演算子・記号（lt/lte/gt/gte）
_COMPARATORS = {
    ConstraintKind.LT: (lambda a, b: a < b, "<"),
    ConstraintKind.LTE: (lambda a, b: a <= b, "<="),
    ConstraintKind.GT: (lambda a, b: a > b, ">"),
    ConstraintKind.GTE: (lambda a, b: a >= b, ">="),
}


def _is_param_name(operand: object, values: Mapping[str, object]) -> bool:
    """operand が values に存在するパラメータ名か（true なら値へ解決可能）。"""
    return isinstance(operand, str) and operand in values


def _resolve(operand: object, values: Mapping[str, object]) -> object:
    """operand がパラメータ名なら値へ解決、定数ならそのまま返す。"""
    return values[operand] if _is_param_name(operand, values) else operand


class ConstraintEvaluator:
    """相関制約評価器（CONSTRAINT 評価の唯一の実装）。"""

    @staticmethod
    def evaluate(
        param_defs: Sequence[ParamDef],
        values: Mapping[str, object],
    ) -> list[Violation]:
        """全 ParamDef の制約・必須・型・enum を論理積評価し違反を列挙。空 list = 妥当。"""
        violations: list[Violation] = []
        for pdef in param_defs:
            ConstraintEvaluator._check_required(pdef, values, violations)
            ConstraintEvaluator._check_type(pdef, values, violations)
            ConstraintEvaluator._check_enum(pdef, values, violations)
            for constraint in pdef.constraints:
                v = ConstraintEvaluator._eval_constraint(pdef, constraint, values)
                if v is not None:
                    violations.append(v)
        return violations

    # -- 必須/型/enum（単一値域）-------------------------------------------

    @staticmethod
    def _check_required(pdef, values, violations) -> None:
        if pdef.default is None and pdef.name not in values:
            violations.append(
                Violation(
                    param=pdef.name,
                    constraint="required",
                    expected="present",
                    actual=None,
                )
            )

    @staticmethod
    def _check_type(pdef, values, violations) -> None:
        if pdef.name not in values:
            return
        value = values[pdef.name]
        if pdef.type is ParamType.INT:
            if isinstance(value, bool) or not isinstance(value, int):
                violations.append(
                    Violation(
                        param=pdef.name,
                        constraint="type(int)",
                        expected="int",
                        actual=value,
                    )
                )

    @staticmethod
    def _check_enum(pdef, values, violations) -> None:
        if pdef.type is not ParamType.ENUM or pdef.enum_values is None:
            return
        if pdef.name not in values:
            return
        value = values[pdef.name]
        if value not in pdef.enum_values:
            violations.append(
                Violation(
                    param=pdef.name,
                    constraint="enum",
                    expected=f"in {list(pdef.enum_values)}",
                    actual=value,
                )
            )

    # -- 相関制約 ----------------------------------------------------------

    @staticmethod
    def _eval_constraint(pdef, constraint: Constraint, values) -> Violation | None:
        kind = constraint.kind
        if kind in _COMPARATORS:
            return ConstraintEvaluator._eval_comparator(pdef, constraint, values)
        if kind is ConstraintKind.RANGE_OPEN:
            return ConstraintEvaluator._eval_range_open(pdef, constraint, values)
        if kind is ConstraintKind.MIN_VALUE:
            return ConstraintEvaluator._eval_min_value(pdef, constraint, values)
        if kind is ConstraintKind.CONDITIONAL:
            return ConstraintEvaluator._eval_conditional(pdef, constraint, values)
        # ConstraintKind 全 7 種を上で網羅するため到達不能。未知種別は違反扱いせず無視する。
        return None  # pragma: no cover

    @staticmethod
    def _eval_comparator(pdef, constraint, values) -> Violation | None:
        op, sym = _COMPARATORS[constraint.kind]
        left_operand, right_operand = constraint.operands
        left = _resolve(left_operand, values)
        right = _resolve(right_operand, values)
        if op(left, right):
            return None
        # 違反箇所のパラメータ: 左がパラメータならそれ、なければ右。
        param = left_operand if _is_param_name(left_operand, values) else right_operand
        actual = _resolve(param, values)
        return Violation(
            param=str(param),
            constraint=f"{constraint.kind.value}({left_operand},{right_operand})",
            expected=f"{left_operand}{sym}{right_operand}",
            actual=actual,
        )

    @staticmethod
    def _eval_range_open(pdef, constraint, values) -> Violation | None:
        c1, param_operand, c2 = constraint.operands
        low = _resolve(c1, values)
        high = _resolve(c2, values)
        value = _resolve(param_operand, values)
        # float_list/enum_list はリスト要素ごとに開区間判定。
        elements = value if isinstance(value, (list, tuple)) else (value,)
        for element in elements:
            if not (low < element < high):
                return Violation(
                    param=str(param_operand),
                    constraint=f"range_open({c1},_,{c2})",
                    expected=f"{c1}<{param_operand}<{c2}",
                    actual=element,
                )
        return None

    @staticmethod
    def _eval_min_value(pdef, constraint, values) -> Violation | None:
        param_operand, threshold_operand = constraint.operands
        value = _resolve(param_operand, values)
        threshold = _resolve(threshold_operand, values)
        if value >= threshold:
            return None
        return Violation(
            param=str(param_operand),
            constraint=f"min_value({param_operand},{threshold_operand})",
            expected=f"{param_operand}>={threshold_operand}",
            actual=value,
        )

    @staticmethod
    def _eval_conditional(pdef, constraint, values) -> Violation | None:
        # 前提 when が真のときのみ内側制約（operands = (param, threshold)、gt>）を評価。
        # when 不在（前提なし）は評価対象外＝違反なし扱い。
        when = constraint.when
        if when is None or not ConstraintEvaluator._premise_holds(when, values):
            return None
        param_operand, threshold_operand = constraint.operands
        value = _resolve(param_operand, values)
        threshold = _resolve(threshold_operand, values)
        if value > threshold:
            return None
        return Violation(
            param=str(param_operand),
            constraint=f"conditional({param_operand}>{threshold_operand})",
            expected=f"{param_operand}>{threshold_operand}",
            actual=value,
        )

    @staticmethod
    def _premise_holds(when: Constraint, values) -> bool:
        """when 前提の成否（normalize==atr 等の等値前提）。"""
        left_operand, right_operand = when.operands
        left = _resolve(left_operand, values)
        # 右オペランドが values に無い文字列なら定数（例 "atr"）として等値比較。
        right = _resolve(right_operand, values)
        return left == right
