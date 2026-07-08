"""ConstraintEvaluator（相関制約評価器）と値オブジェクトの仕様検証。

対象: indigators/indicator_ui/api/domain/
  - Violation / Constraint / ParamDef / ConstraintEvaluator
設計入力:
  - 内部設計書 §3.1.1（ParamDef/Constraint enum）、§3.1.5（ConstraintEvaluator/Violation）、
    §6.3.4（violations 構造）、§7.3（単一定義／二重実装禁止）
  - 基本設計書 §5.5.5（制約種別 lt/lte/gt/gte/range_open/min_value/conditional・チェーン分解）
実コード根拠（テストケースの正当性）:
  - tgp_btlm: src/core.py:147-148（0<pp<1 ValueError）、SPEC.md:67（0<q_low<q_high<1）
  - price_range_power: src/core.py:109-110（interval<=0 ValueError）、
    src/lwc_chart.py:68-69（top_n<0 ValueError）
  - profit_band: src/robust_bands.py:136-137（normalize=="atr" のとき atr_period 使用）

import 規約: conftest.py が api/ を sys.path へ追加 → from domain import ...（テスト基盤集約）。
テスト構造: Arrange-Act-Assert（AAA）。各テストは独立・再現可能（F.I.R.S.T）。
"""

import pytest

# import パス（api/）は conftest.py で通す。
from domain.param_def import (
    Constraint,
    ConstraintKind,
    ParamDef,
    ParamType,
)
from domain.validation import (
    ConstraintEvaluator,
    Violation,
)


# ---------------------------------------------------------------------------
# 値オブジェクト基本性質（不変・属性）
# ---------------------------------------------------------------------------


def test_violation_holds_param_constraint_expected_and_actual():
    # Arrange / Act
    v = Violation(
        param="q_low",
        constraint="lt(q_low,q_high)",
        expected="q_low<q_high",
        actual=0.96,
    )
    # Assert（§6.3.4 violations 構造に整合する4属性）
    assert v.param == "q_low"
    assert v.constraint == "lt(q_low,q_high)"
    assert v.expected == "q_low<q_high"
    assert v.actual == 0.96


def test_violation_is_frozen_immutable():
    # Arrange
    v = Violation(param="x", constraint="c", expected="e", actual=1)
    # Act / Assert（frozen dataclass のため再代入不可）
    with pytest.raises(Exception):
        v.param = "y"  # type: ignore[misc]


def test_constraint_holds_kind_and_operands():
    # Arrange / Act
    c = Constraint(
        kind=ConstraintKind.LT,
        operands=("q_low", "q_high"),
        message_key="err.q_order",
    )
    # Assert
    assert c.kind is ConstraintKind.LT
    assert c.operands == ("q_low", "q_high")
    assert c.message_key == "err.q_order"


def test_param_def_holds_type_default_and_constraints():
    # Arrange
    c = Constraint(kind=ConstraintKind.GTE, operands=("top_n", 0), message_key="k")
    # Act
    p = ParamDef(
        name="top_n",
        label_key="label.top_n",
        type=ParamType.INT,
        default=2,
        min=0,
        constraints=(c,),
    )
    # Assert
    assert p.name == "top_n"
    assert p.type is ParamType.INT
    assert p.default == 2
    assert p.constraints == (c,)


def test_constraint_kind_enum_has_seven_kinds():
    # Assert（基本設計 §5.5.5：7 種別）
    kinds = {k.value for k in ConstraintKind}
    assert kinds == {
        "lt",
        "lte",
        "gt",
        "gte",
        "range_open",
        "min_value",
        "conditional",
    }


# ---------------------------------------------------------------------------
# ヘルパ: ParamDef を 1 制約付きで組む
# ---------------------------------------------------------------------------


def _pdef(name, type_, *constraints, **kw):
    return ParamDef(
        name=name,
        label_key=f"label.{name}",
        type=type_,
        default=kw.pop("default", None),
        constraints=tuple(constraints),
        **kw,
    )


# ---------------------------------------------------------------------------
# lt / lte / gt / gte（2 オペランド大小・実コード q_low<q_high）
# ---------------------------------------------------------------------------


def test_check_lt_returns_empty_when_q_low_less_than_q_high():
    # Arrange（正常系: 0.05 < 0.95）
    pdefs = [
        _pdef("q_low", ParamType.FLOAT,
              Constraint(ConstraintKind.LT, ("q_low", "q_high"), "err.q_order")),
        _pdef("q_high", ParamType.FLOAT),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {"q_low": 0.05, "q_high": 0.95})
    # Assert
    assert result == []


def test_check_lt_flags_violation_when_q_low_equals_q_high_boundary():
    # Arrange（境界: 等値は lt 違反）
    pdefs = [
        _pdef("q_low", ParamType.FLOAT,
              Constraint(ConstraintKind.LT, ("q_low", "q_high"), "err.q_order")),
        _pdef("q_high", ParamType.FLOAT),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {"q_low": 0.5, "q_high": 0.5})
    # Assert
    assert len(result) == 1
    assert result[0].param == "q_low"
    assert result[0].constraint == "lt(q_low,q_high)"
    assert result[0].expected == "q_low<q_high"
    assert result[0].actual == 0.5


def test_check_lt_flags_violation_when_q_low_greater_than_q_high_reversed():
    # Arrange（異常系: 逆転 0.5 > 0.4）
    pdefs = [
        _pdef("q_low", ParamType.FLOAT,
              Constraint(ConstraintKind.LT, ("q_low", "q_high"), "err.q_order")),
        _pdef("q_high", ParamType.FLOAT),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {"q_low": 0.5, "q_high": 0.4})
    # Assert
    assert len(result) == 1
    assert result[0].param == "q_low"


def test_check_lte_allows_equal_values():
    # Arrange（境界: lte は等値許容）
    pdefs = [
        _pdef("a", ParamType.FLOAT,
              Constraint(ConstraintKind.LTE, ("a", "b"), "err.le")),
        _pdef("b", ParamType.FLOAT),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {"a": 0.5, "b": 0.5})
    # Assert
    assert result == []


def test_check_gt_flags_violation_when_not_greater():
    # Arrange（gt: a>b 不成立）
    pdefs = [
        _pdef("a", ParamType.FLOAT,
              Constraint(ConstraintKind.GT, ("a", "b"), "err.gt")),
        _pdef("b", ParamType.FLOAT),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {"a": 1.0, "b": 2.0})
    # Assert
    assert len(result) == 1
    assert result[0].param == "a"


def test_check_gte_allows_equal_values():
    # Arrange（境界: gte は等値許容）
    pdefs = [
        _pdef("a", ParamType.INT,
              Constraint(ConstraintKind.GTE, ("a", "b"), "err.ge")),
        _pdef("b", ParamType.INT),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {"a": 3, "b": 3})
    # Assert
    assert result == []


def test_check_lt_supports_constant_operand():
    # Arrange（オペランドに定数: q_high < 1）
    pdefs = [
        _pdef("q_high", ParamType.FLOAT,
              Constraint(ConstraintKind.LT, ("q_high", 1), "err.lt1")),
    ]
    # Act（境界違反: 1 < 1 は偽）
    result = ConstraintEvaluator.evaluate(pdefs, {"q_high": 1})
    # Assert
    assert len(result) == 1
    assert result[0].param == "q_high"
    assert result[0].actual == 1


# ---------------------------------------------------------------------------
# range_open（開区間 c1<x<c2・実コード 0<q<1）
# ---------------------------------------------------------------------------


def test_check_range_open_returns_empty_inside_interval():
    # Arrange（正常系: 0 < 0.5 < 1）
    pdefs = [
        _pdef("q_low", ParamType.FLOAT,
              Constraint(ConstraintKind.RANGE_OPEN, (0, "q_low", 1), "err.range")),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {"q_low": 0.5})
    # Assert
    assert result == []


def test_check_range_open_flags_lower_boundary_zero():
    # Arrange（境界: q_low=0 は開区間外）
    pdefs = [
        _pdef("q_low", ParamType.FLOAT,
              Constraint(ConstraintKind.RANGE_OPEN, (0, "q_low", 1), "err.range")),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {"q_low": 0})
    # Assert
    assert len(result) == 1
    assert result[0].param == "q_low"
    assert result[0].actual == 0


def test_check_range_open_flags_upper_boundary_one():
    # Arrange（境界: q_high=1 は開区間外）
    pdefs = [
        _pdef("q_high", ParamType.FLOAT,
              Constraint(ConstraintKind.RANGE_OPEN, (0, "q_high", 1), "err.range")),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {"q_high": 1})
    # Assert
    assert len(result) == 1
    assert result[0].param == "q_high"
    assert result[0].actual == 1


def test_check_range_open_flags_out_of_range_value():
    # Arrange（異常系: 1.5 > 1）
    pdefs = [
        _pdef("q_high", ParamType.FLOAT,
              Constraint(ConstraintKind.RANGE_OPEN, (0, "q_high", 1), "err.range")),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {"q_high": 1.5})
    # Assert
    assert len(result) == 1


# ---------------------------------------------------------------------------
# min_value（x >= c・実コード top_n>=0）/ gt（interval>0）
# ---------------------------------------------------------------------------


def test_check_min_value_returns_empty_at_lower_boundary():
    # Arrange（境界: top_n=0 は min_value(>=0) を満たす）
    pdefs = [
        _pdef("top_n", ParamType.INT,
              Constraint(ConstraintKind.MIN_VALUE, ("top_n", 0), "err.min")),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {"top_n": 0})
    # Assert
    assert result == []


def test_check_min_value_flags_below_threshold():
    # Arrange（異常系: top_n=-1 < 0）
    pdefs = [
        _pdef("top_n", ParamType.INT,
              Constraint(ConstraintKind.MIN_VALUE, ("top_n", 0), "err.min")),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {"top_n": -1})
    # Assert
    assert len(result) == 1
    assert result[0].param == "top_n"
    assert result[0].actual == -1


def test_check_gt_zero_flags_interval_at_zero_boundary():
    # Arrange（実コード price_range_power: interval<=0 は不正 → interval>0 を gt で表現）
    pdefs = [
        _pdef("interval", ParamType.FLOAT,
              Constraint(ConstraintKind.GT, ("interval", 0), "err.interval")),
    ]
    # Act（境界: 0 < interval 不成立）
    result = ConstraintEvaluator.evaluate(pdefs, {"interval": 0})
    # Assert
    assert len(result) == 1
    assert result[0].param == "interval"


def test_check_gt_zero_returns_empty_for_positive_interval():
    # Arrange（正常系: interval=0.1 > 0）
    pdefs = [
        _pdef("interval", ParamType.FLOAT,
              Constraint(ConstraintKind.GT, ("interval", 0), "err.interval")),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {"interval": 0.1})
    # Assert
    assert result == []


# ---------------------------------------------------------------------------
# conditional（when P then constraint・実コード normalize=="atr" → atr_period 有効）
# ---------------------------------------------------------------------------


def test_check_conditional_inactive_when_premise_false():
    # Arrange（前提 normalize!="atr" のとき内側制約は無効＝違反なし）
    inner = Constraint(ConstraintKind.GT, ("atr_period", 0), "err.atr_period")
    when = Constraint(ConstraintKind.LT, ("normalize", "normalize"), "")  # placeholder不可
    # conditional は when を等値前提として持つ設計（kind=CONDITIONAL, when 付き）
    cond = Constraint(
        kind=ConstraintKind.CONDITIONAL,
        operands=("atr_period", 0),
        message_key="err.atr_period",
        when=Constraint(ConstraintKind.GTE, ("normalize", "atr"), "", ),
    )
    pdefs = [
        _pdef("atr_period", ParamType.INT, cond),
        _pdef("normalize", ParamType.ENUM),
    ]
    # Act（normalize=return → 前提偽 → 内側 gt(atr_period,0) を評価しない）
    result = ConstraintEvaluator.evaluate(
        pdefs, {"normalize": "return", "atr_period": 0}
    )
    # Assert
    assert result == []
    # 未使用ローカルの安全な参照（lint 抑止意図でなく明示）
    assert inner.kind is ConstraintKind.GT
    assert when.kind is ConstraintKind.LT


def test_check_conditional_active_flags_inner_violation_when_premise_true():
    # Arrange（前提 normalize=="atr" のとき atr_period>0 必須）
    cond = Constraint(
        kind=ConstraintKind.CONDITIONAL,
        operands=("atr_period", 0),
        message_key="err.atr_period",
        when=Constraint(ConstraintKind.GTE, ("normalize", "atr"), ""),
    )
    pdefs = [
        _pdef("atr_period", ParamType.INT, cond),
        _pdef("normalize", ParamType.ENUM),
    ]
    # Act（normalize=atr かつ atr_period=0 → 内側 gt(atr_period,0) 違反）
    result = ConstraintEvaluator.evaluate(
        pdefs, {"normalize": "atr", "atr_period": 0}
    )
    # Assert
    assert len(result) == 1
    assert result[0].param == "atr_period"


def test_check_conditional_active_passes_when_inner_satisfied():
    # Arrange（前提真 かつ 内側満足）
    cond = Constraint(
        kind=ConstraintKind.CONDITIONAL,
        operands=("atr_period", 0),
        message_key="err.atr_period",
        when=Constraint(ConstraintKind.GTE, ("normalize", "atr"), ""),
    )
    pdefs = [
        _pdef("atr_period", ParamType.INT, cond),
        _pdef("normalize", ParamType.ENUM),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(
        pdefs, {"normalize": "atr", "atr_period": 14}
    )
    # Assert
    assert result == []


# ---------------------------------------------------------------------------
# 実指標由来の複合: チェーン 0<q_low<q_high<1（tgp_btlm）
# ---------------------------------------------------------------------------


def _q_param_defs():
    return [
        ParamDef(
            name="q_low",
            label_key="label.q_low",
            type=ParamType.FLOAT,
            default=0.05,
            constraints=(
                Constraint(ConstraintKind.RANGE_OPEN, (0, "q_low", 1), "err.q_low.range"),
                Constraint(ConstraintKind.LT, ("q_low", "q_high"), "err.q_order"),
            ),
        ),
        ParamDef(
            name="q_high",
            label_key="label.q_high",
            type=ParamType.FLOAT,
            default=0.95,
            constraints=(
                Constraint(ConstraintKind.RANGE_OPEN, (0, "q_high", 1), "err.q_high.range"),
            ),
        ),
    ]


def test_check_q_chain_valid_default_returns_empty():
    # Arrange（正常系: 0<0.05<0.95<1）
    # Act
    result = ConstraintEvaluator.evaluate(_q_param_defs(), {"q_low": 0.05, "q_high": 0.95})
    # Assert
    assert result == []


def test_check_q_chain_flags_q_low_zero_boundary():
    # Arrange（境界: q_low=0 は range_open 違反）
    # Act
    result = ConstraintEvaluator.evaluate(_q_param_defs(), {"q_low": 0, "q_high": 0.95})
    # Assert（range_open(0,q_low,1) 違反 1 件、lt(q_low,q_high) は満たす）
    params = [v.param for v in result]
    assert "q_low" in params
    assert len(result) == 1


def test_check_q_chain_flags_q_high_one_boundary():
    # Arrange（境界: q_high=1 は range_open 違反）
    # Act
    result = ConstraintEvaluator.evaluate(_q_param_defs(), {"q_low": 0.05, "q_high": 1})
    # Assert
    assert any(v.param == "q_high" for v in result)
    assert len(result) == 1


def test_check_q_chain_reports_multiple_violations_conjunctively():
    # Arrange（複合違反: q_low=0（range違反）かつ q_low>q_high（lt違反）かつ q_high=1.2（range違反））
    # Act
    result = ConstraintEvaluator.evaluate(_q_param_defs(), {"q_low": 0, "q_high": 1.2})
    # Assert（論理積評価で違反を漏れなく列挙: q_low range + q_low<q_high(0<1.2 OK実は満たす) ...）
    # q_low=0 → range_open 違反, q_high=1.2 → range_open 違反, lt(0,1.2) は満たす
    params = sorted(v.param for v in result)
    assert params == ["q_high", "q_low"]


def test_check_q_chain_flags_reversed_q_values():
    # Arrange（逆転: q_low=0.96 > q_high=0.5、両者は範囲内）
    # Act
    result = ConstraintEvaluator.evaluate(_q_param_defs(), {"q_low": 0.96, "q_high": 0.5})
    # Assert（lt(q_low,q_high) のみ違反）
    assert len(result) == 1
    assert result[0].param == "q_low"
    assert result[0].constraint == "lt(q_low,q_high)"


# ---------------------------------------------------------------------------
# 実指標由来: profit_band probabilities（各 0<p<1）/ float_list
# ---------------------------------------------------------------------------


def test_check_probabilities_each_in_open_interval_returns_empty():
    # Arrange（正常系: [0.95, 0.99] すべて 0<p<1）
    pdefs = [
        ParamDef(
            name="probabilities",
            label_key="label.probabilities",
            type=ParamType.FLOAT_LIST,
            default=(0.95, 0.99),
            constraints=(
                Constraint(ConstraintKind.RANGE_OPEN, (0, "probabilities", 1), "err.prob"),
            ),
        ),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {"probabilities": (0.95, 0.99)})
    # Assert
    assert result == []


def test_check_probabilities_flags_element_at_one_boundary():
    # Arrange（境界: 要素 1.0 は開区間外）
    pdefs = [
        ParamDef(
            name="probabilities",
            label_key="label.probabilities",
            type=ParamType.FLOAT_LIST,
            default=(0.95, 0.99),
            constraints=(
                Constraint(ConstraintKind.RANGE_OPEN, (0, "probabilities", 1), "err.prob"),
            ),
        ),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {"probabilities": (0.95, 1.0)})
    # Assert
    assert len(result) == 1
    assert result[0].param == "probabilities"


# ---------------------------------------------------------------------------
# 実指標由来: price_range_power range_from < range_to
# ---------------------------------------------------------------------------


def test_check_range_from_less_than_range_to_returns_empty():
    # Arrange（正常系: 1.0 < 2.0）
    pdefs = [
        _pdef("range_from", ParamType.FLOAT,
              Constraint(ConstraintKind.LT, ("range_from", "range_to"), "err.range_order")),
        _pdef("range_to", ParamType.FLOAT),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {"range_from": 1.0, "range_to": 2.0})
    # Assert
    assert result == []


def test_check_range_from_not_less_than_range_to_flags_violation():
    # Arrange（異常系: 2.0 >= 1.0）
    pdefs = [
        _pdef("range_from", ParamType.FLOAT,
              Constraint(ConstraintKind.LT, ("range_from", "range_to"), "err.range_order")),
        _pdef("range_to", ParamType.FLOAT),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {"range_from": 2.0, "range_to": 1.0})
    # Assert
    assert len(result) == 1
    assert result[0].param == "range_from"


# ---------------------------------------------------------------------------
# enum 範囲外 / 必須欠落 / 型不一致
# ---------------------------------------------------------------------------


def test_check_enum_value_within_allowed_returns_empty():
    # Arrange（正常系: enum 許容値内）
    pdefs = [
        ParamDef(
            name="interval",
            label_key="label.interval",
            type=ParamType.ENUM,
            default=0.1,
            enum_values=(0.1, 0.01, 0.001),
        ),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {"interval": 0.01})
    # Assert
    assert result == []


def test_check_enum_value_out_of_allowed_flags_violation():
    # Arrange（異常系: enum 許容外 0.5）
    pdefs = [
        ParamDef(
            name="interval",
            label_key="label.interval",
            type=ParamType.ENUM,
            default=0.1,
            enum_values=(0.1, 0.01, 0.001),
        ),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {"interval": 0.5})
    # Assert
    assert len(result) == 1
    assert result[0].param == "interval"
    assert result[0].actual == 0.5


def test_check_required_param_missing_flags_violation():
    # Arrange（必須欠落: default=None かつ ui_visible=True を必須とみなす）
    pdefs = [
        ParamDef(
            name="q_low",
            label_key="label.q_low",
            type=ParamType.FLOAT,
            default=None,
        ),
    ]
    # Act（values に q_low が無い）
    result = ConstraintEvaluator.evaluate(pdefs, {})
    # Assert
    assert len(result) == 1
    assert result[0].param == "q_low"


def test_check_optional_param_with_default_missing_returns_empty():
    # Arrange（任意: default 有り → 欠落しても違反でない）
    pdefs = [
        ParamDef(
            name="top_n",
            label_key="label.top_n",
            type=ParamType.INT,
            default=2,
        ),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {})
    # Assert
    assert result == []


def test_check_type_mismatch_int_param_given_string_flags_violation():
    # Arrange（型不一致: int パラメータに文字列）
    pdefs = [
        ParamDef(
            name="top_n",
            label_key="label.top_n",
            type=ParamType.INT,
            default=2,
        ),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {"top_n": "abc"})
    # Assert
    assert len(result) == 1
    assert result[0].param == "top_n"
    assert result[0].actual == "abc"


def test_check_type_match_int_param_given_int_returns_empty():
    # Arrange（型一致）
    pdefs = [
        ParamDef(
            name="top_n",
            label_key="label.top_n",
            type=ParamType.INT,
            default=2,
        ),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {"top_n": 5})
    # Assert
    assert result == []


# ---------------------------------------------------------------------------
# 集合的性質: 全制約論理積・空入力
# ---------------------------------------------------------------------------


def test_check_empty_param_defs_returns_empty():
    # Arrange / Act（制約が無ければ常に妥当）
    result = ConstraintEvaluator.evaluate([], {})
    # Assert
    assert result == []


def test_check_returns_list_type():
    # Arrange
    pdefs = [
        _pdef("a", ParamType.FLOAT,
              Constraint(ConstraintKind.LT, ("a", "b"), "k")),
        _pdef("b", ParamType.FLOAT),
    ]
    # Act
    result = ConstraintEvaluator.evaluate(pdefs, {"a": 1, "b": 2})
    # Assert（戻り値は list）
    assert isinstance(result, list)
