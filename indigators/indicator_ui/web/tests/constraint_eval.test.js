// constraint_eval.js の仕様検証（node:test / node:assert）。
//
// 目的: Python domain/validation.py ConstraintEvaluator.evaluate の JS 移植が
//       Python テスト api/tests/test_constraint_evaluator.py と同一の代表ベクタで
//       一致すること（§8 T-INT-5 パリティ）。
// 構造: Arrange-Act-Assert（AAA）。各テスト独立・再現可能（F.I.R.S.T）。
//
// パリティ対応表（Python テスト関数 ↔ 本ファイルの test 名）:
//   test_check_lt_returns_empty_when_q_low_less_than_q_high      ↔ lt: returns empty when q_low < q_high
//   test_check_lt_flags_violation_when_q_low_equals_q_high_..    ↔ lt: flags violation at equal boundary
//   test_check_lt_flags_violation_when_q_low_greater_..reversed  ↔ lt: flags violation when reversed
//   test_check_lte_allows_equal_values                          ↔ lte: allows equal values
//   test_check_gt_flags_violation_when_not_greater              ↔ gt: flags when not greater
//   test_check_gte_allows_equal_values                          ↔ gte: allows equal values
//   test_check_lt_supports_constant_operand                     ↔ lt: supports constant operand
//   test_check_range_open_returns_empty_inside_interval         ↔ range_open: empty inside interval
//   test_check_range_open_flags_lower_boundary_zero             ↔ range_open: flags lower boundary 0
//   test_check_range_open_flags_upper_boundary_one              ↔ range_open: flags upper boundary 1
//   test_check_range_open_flags_out_of_range_value              ↔ range_open: flags out of range
//   test_check_min_value_returns_empty_at_lower_boundary        ↔ min_value: empty at lower boundary
//   test_check_min_value_flags_below_threshold                  ↔ min_value: flags below threshold
//   test_check_gt_zero_flags_interval_at_zero_boundary          ↔ gt: flags interval at 0
//   test_check_gt_zero_returns_empty_for_positive_interval      ↔ gt: empty for positive interval
//   test_check_conditional_inactive_when_premise_false          ↔ conditional: inactive when premise false
//   test_check_conditional_active_flags_inner_violation..       ↔ conditional: flags inner when premise true
//   test_check_conditional_active_passes_when_inner_satisfied   ↔ conditional: passes when inner satisfied
//   test_check_q_chain_valid_default_returns_empty              ↔ q-chain: valid default empty
//   test_check_q_chain_flags_q_low_zero_boundary               ↔ q-chain: flags q_low=0
//   test_check_q_chain_flags_q_high_one_boundary               ↔ q-chain: flags q_high=1
//   test_check_q_chain_reports_multiple_violations_..          ↔ q-chain: multiple violations conjunctively
//   test_check_q_chain_flags_reversed_q_values                 ↔ q-chain: reversed q values lt only
//   test_check_probabilities_each_in_open_interval_returns_..  ↔ probabilities: each in open interval empty
//   test_check_probabilities_flags_element_at_one_boundary     ↔ probabilities: flags element at 1.0
//   test_check_range_from_less_than_range_to_returns_empty     ↔ range_from<range_to empty
//   test_check_range_from_not_less_than_range_to_flags_..      ↔ range_from>=range_to flags
//   test_check_enum_value_within_allowed_returns_empty         ↔ enum: within allowed empty
//   test_check_enum_value_out_of_allowed_flags_violation       ↔ enum: out of allowed flags
//   test_check_required_param_missing_flags_violation          ↔ required: missing flags
//   test_check_optional_param_with_default_missing_returns_..  ↔ optional with default empty
//   test_check_type_mismatch_int_param_given_string_flags_..   ↔ type(int): string flags
//   test_check_type_match_int_param_given_int_returns_empty    ↔ type(int): int empty
//   test_check_empty_param_defs_returns_empty                  ↔ empty param defs empty
//   test_check_returns_list_type                               ↔ returns array type

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  ConstraintKind,
  ParamType,
  evaluate,
} from '../js/domain/constraint_eval.js';

// ヘルパ: ParamDef を 1 制約付きで組む（Python _pdef 相当）。
function pdef(name, type, constraints = [], extra = {}) {
  return {
    name,
    labelKey: `label.${name}`,
    type,
    default: extra.default !== undefined ? extra.default : null,
    enumValues: extra.enumValues ?? null,
    constraints,
  };
}

function lt(a, b) {
  return { kind: ConstraintKind.LT, operands: [a, b], messageKey: 'k' };
}

// --- lt / lte / gt / gte ---------------------------------------------------

test('lt: returns empty when q_low < q_high', () => {
  // Arrange
  const pdefs = [
    pdef('q_low', ParamType.FLOAT, [{ kind: ConstraintKind.LT, operands: ['q_low', 'q_high'], messageKey: 'err.q_order' }]),
    pdef('q_high', ParamType.FLOAT),
  ];
  // Act
  const result = evaluate(pdefs, { q_low: 0.05, q_high: 0.95 });
  // Assert
  assert.deepEqual(result, []);
});

test('lt: flags violation at equal boundary', () => {
  // Arrange
  const pdefs = [
    pdef('q_low', ParamType.FLOAT, [{ kind: ConstraintKind.LT, operands: ['q_low', 'q_high'], messageKey: 'err.q_order' }]),
    pdef('q_high', ParamType.FLOAT),
  ];
  // Act
  const result = evaluate(pdefs, { q_low: 0.5, q_high: 0.5 });
  // Assert
  assert.equal(result.length, 1);
  assert.equal(result[0].param, 'q_low');
  assert.equal(result[0].constraint, 'lt(q_low,q_high)');
  assert.equal(result[0].expected, 'q_low<q_high');
  assert.equal(result[0].actual, 0.5);
});

test('lt: flags violation when reversed', () => {
  // Arrange
  const pdefs = [
    pdef('q_low', ParamType.FLOAT, [{ kind: ConstraintKind.LT, operands: ['q_low', 'q_high'], messageKey: 'err.q_order' }]),
    pdef('q_high', ParamType.FLOAT),
  ];
  // Act
  const result = evaluate(pdefs, { q_low: 0.5, q_high: 0.4 });
  // Assert
  assert.equal(result.length, 1);
  assert.equal(result[0].param, 'q_low');
});

test('lte: allows equal values', () => {
  // Arrange
  const pdefs = [
    pdef('a', ParamType.FLOAT, [{ kind: ConstraintKind.LTE, operands: ['a', 'b'], messageKey: 'err.le' }]),
    pdef('b', ParamType.FLOAT),
  ];
  // Act
  const result = evaluate(pdefs, { a: 0.5, b: 0.5 });
  // Assert
  assert.deepEqual(result, []);
});

test('gt: flags when not greater', () => {
  // Arrange
  const pdefs = [
    pdef('a', ParamType.FLOAT, [{ kind: ConstraintKind.GT, operands: ['a', 'b'], messageKey: 'err.gt' }]),
    pdef('b', ParamType.FLOAT),
  ];
  // Act
  const result = evaluate(pdefs, { a: 1.0, b: 2.0 });
  // Assert
  assert.equal(result.length, 1);
  assert.equal(result[0].param, 'a');
});

test('gte: allows equal values', () => {
  // Arrange
  const pdefs = [
    pdef('a', ParamType.INT, [{ kind: ConstraintKind.GTE, operands: ['a', 'b'], messageKey: 'err.ge' }]),
    pdef('b', ParamType.INT),
  ];
  // Act
  const result = evaluate(pdefs, { a: 3, b: 3 });
  // Assert
  assert.deepEqual(result, []);
});

test('lt: supports constant operand', () => {
  // Arrange（オペランドに定数: q_high < 1、境界違反 1<1 偽）
  const pdefs = [
    pdef('q_high', ParamType.FLOAT, [{ kind: ConstraintKind.LT, operands: ['q_high', 1], messageKey: 'err.lt1' }]),
  ];
  // Act
  const result = evaluate(pdefs, { q_high: 1 });
  // Assert
  assert.equal(result.length, 1);
  assert.equal(result[0].param, 'q_high');
  assert.equal(result[0].actual, 1);
});

// --- range_open ------------------------------------------------------------

test('range_open: empty inside interval', () => {
  const pdefs = [pdef('q_low', ParamType.FLOAT, [{ kind: ConstraintKind.RANGE_OPEN, operands: [0, 'q_low', 1], messageKey: 'err.range' }])];
  const result = evaluate(pdefs, { q_low: 0.5 });
  assert.deepEqual(result, []);
});

test('range_open: flags lower boundary 0', () => {
  const pdefs = [pdef('q_low', ParamType.FLOAT, [{ kind: ConstraintKind.RANGE_OPEN, operands: [0, 'q_low', 1], messageKey: 'err.range' }])];
  const result = evaluate(pdefs, { q_low: 0 });
  assert.equal(result.length, 1);
  assert.equal(result[0].param, 'q_low');
  assert.equal(result[0].actual, 0);
});

test('range_open: flags upper boundary 1', () => {
  const pdefs = [pdef('q_high', ParamType.FLOAT, [{ kind: ConstraintKind.RANGE_OPEN, operands: [0, 'q_high', 1], messageKey: 'err.range' }])];
  const result = evaluate(pdefs, { q_high: 1 });
  assert.equal(result.length, 1);
  assert.equal(result[0].param, 'q_high');
  assert.equal(result[0].actual, 1);
});

test('range_open: flags out of range', () => {
  const pdefs = [pdef('q_high', ParamType.FLOAT, [{ kind: ConstraintKind.RANGE_OPEN, operands: [0, 'q_high', 1], messageKey: 'err.range' }])];
  const result = evaluate(pdefs, { q_high: 1.5 });
  assert.equal(result.length, 1);
});

// --- min_value / gt>0 ------------------------------------------------------

test('min_value: empty at lower boundary', () => {
  const pdefs = [pdef('top_n', ParamType.INT, [{ kind: ConstraintKind.MIN_VALUE, operands: ['top_n', 0], messageKey: 'err.min' }])];
  const result = evaluate(pdefs, { top_n: 0 });
  assert.deepEqual(result, []);
});

test('min_value: flags below threshold', () => {
  const pdefs = [pdef('top_n', ParamType.INT, [{ kind: ConstraintKind.MIN_VALUE, operands: ['top_n', 0], messageKey: 'err.min' }])];
  const result = evaluate(pdefs, { top_n: -1 });
  assert.equal(result.length, 1);
  assert.equal(result[0].param, 'top_n');
  assert.equal(result[0].actual, -1);
});

test('gt: flags interval at 0', () => {
  const pdefs = [pdef('interval', ParamType.FLOAT, [{ kind: ConstraintKind.GT, operands: ['interval', 0], messageKey: 'err.interval' }])];
  const result = evaluate(pdefs, { interval: 0 });
  assert.equal(result.length, 1);
  assert.equal(result[0].param, 'interval');
});

test('gt: empty for positive interval', () => {
  const pdefs = [pdef('interval', ParamType.FLOAT, [{ kind: ConstraintKind.GT, operands: ['interval', 0], messageKey: 'err.interval' }])];
  const result = evaluate(pdefs, { interval: 0.1 });
  assert.deepEqual(result, []);
});

// --- conditional -----------------------------------------------------------

function condAtr() {
  return {
    kind: ConstraintKind.CONDITIONAL,
    operands: ['atr_period', 0],
    messageKey: 'err.atr_period',
    when: { kind: ConstraintKind.GTE, operands: ['normalize', 'atr'], messageKey: '' },
  };
}

test('conditional: inactive when premise false', () => {
  const pdefs = [pdef('atr_period', ParamType.INT, [condAtr()]), pdef('normalize', ParamType.ENUM)];
  const result = evaluate(pdefs, { normalize: 'return', atr_period: 0 });
  assert.deepEqual(result, []);
});

test('conditional: flags inner when premise true', () => {
  const pdefs = [pdef('atr_period', ParamType.INT, [condAtr()]), pdef('normalize', ParamType.ENUM)];
  const result = evaluate(pdefs, { normalize: 'atr', atr_period: 0 });
  assert.equal(result.length, 1);
  assert.equal(result[0].param, 'atr_period');
});

test('conditional: passes when inner satisfied', () => {
  const pdefs = [pdef('atr_period', ParamType.INT, [condAtr()]), pdef('normalize', ParamType.ENUM)];
  const result = evaluate(pdefs, { normalize: 'atr', atr_period: 14 });
  assert.deepEqual(result, []);
});

// --- 実指標由来: q-chain 0<q_low<q_high<1 (tgp_btlm) -----------------------

function qParamDefs() {
  return [
    pdef('q_low', ParamType.FLOAT, [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'q_low', 1], messageKey: 'err.q_low.range' },
      { kind: ConstraintKind.LT, operands: ['q_low', 'q_high'], messageKey: 'err.q_order' },
    ], { default: 0.05 }),
    pdef('q_high', ParamType.FLOAT, [
      { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'q_high', 1], messageKey: 'err.q_high.range' },
    ], { default: 0.95 }),
  ];
}

test('q-chain: valid default empty', () => {
  const result = evaluate(qParamDefs(), { q_low: 0.05, q_high: 0.95 });
  assert.deepEqual(result, []);
});

test('q-chain: flags q_low=0', () => {
  const result = evaluate(qParamDefs(), { q_low: 0, q_high: 0.95 });
  const params = result.map((v) => v.param);
  assert.ok(params.includes('q_low'));
  assert.equal(result.length, 1);
});

test('q-chain: flags q_high=1', () => {
  const result = evaluate(qParamDefs(), { q_low: 0.05, q_high: 1 });
  assert.ok(result.some((v) => v.param === 'q_high'));
  assert.equal(result.length, 1);
});

test('q-chain: multiple violations conjunctively', () => {
  // q_low=0 → range違反, q_high=1.2 → range違反, lt(0,1.2) は満たす
  const result = evaluate(qParamDefs(), { q_low: 0, q_high: 1.2 });
  const params = result.map((v) => v.param).sort();
  assert.deepEqual(params, ['q_high', 'q_low']);
});

test('q-chain: reversed q values lt only', () => {
  // q_low=0.96 > q_high=0.5、両者範囲内 → lt のみ違反
  const result = evaluate(qParamDefs(), { q_low: 0.96, q_high: 0.5 });
  assert.equal(result.length, 1);
  assert.equal(result[0].param, 'q_low');
  assert.equal(result[0].constraint, 'lt(q_low,q_high)');
});

// --- 実指標由来: probabilities (float_list, profit_band) -------------------

function probParamDefs() {
  return [pdef('probabilities', ParamType.FLOAT_LIST, [
    { kind: ConstraintKind.RANGE_OPEN, operands: [0, 'probabilities', 1], messageKey: 'err.prob' },
  ], { default: [0.95, 0.99] })];
}

test('probabilities: each in open interval empty', () => {
  const result = evaluate(probParamDefs(), { probabilities: [0.95, 0.99] });
  assert.deepEqual(result, []);
});

test('probabilities: flags element at 1.0', () => {
  const result = evaluate(probParamDefs(), { probabilities: [0.95, 1.0] });
  assert.equal(result.length, 1);
  assert.equal(result[0].param, 'probabilities');
});

// --- 実指標由来: price_range_power range_from < range_to -------------------

test('range_from<range_to empty', () => {
  const pdefs = [
    pdef('range_from', ParamType.FLOAT, [{ kind: ConstraintKind.LT, operands: ['range_from', 'range_to'], messageKey: 'err.range_order' }]),
    pdef('range_to', ParamType.FLOAT),
  ];
  const result = evaluate(pdefs, { range_from: 1.0, range_to: 2.0 });
  assert.deepEqual(result, []);
});

test('range_from>=range_to flags', () => {
  const pdefs = [
    pdef('range_from', ParamType.FLOAT, [{ kind: ConstraintKind.LT, operands: ['range_from', 'range_to'], messageKey: 'err.range_order' }]),
    pdef('range_to', ParamType.FLOAT),
  ];
  const result = evaluate(pdefs, { range_from: 2.0, range_to: 1.0 });
  assert.equal(result.length, 1);
  assert.equal(result[0].param, 'range_from');
});

// --- enum / required / type ------------------------------------------------

test('enum: within allowed empty', () => {
  const pdefs = [pdef('interval', ParamType.ENUM, [], { default: 0.1, enumValues: [0.1, 0.01, 0.001] })];
  const result = evaluate(pdefs, { interval: 0.01 });
  assert.deepEqual(result, []);
});

test('enum: out of allowed flags', () => {
  const pdefs = [pdef('interval', ParamType.ENUM, [], { default: 0.1, enumValues: [0.1, 0.01, 0.001] })];
  const result = evaluate(pdefs, { interval: 0.5 });
  assert.equal(result.length, 1);
  assert.equal(result[0].param, 'interval');
  assert.equal(result[0].actual, 0.5);
});

test('required: missing flags', () => {
  const pdefs = [pdef('q_low', ParamType.FLOAT, [], { default: null })];
  const result = evaluate(pdefs, {});
  assert.equal(result.length, 1);
  assert.equal(result[0].param, 'q_low');
});

test('optional with default empty', () => {
  const pdefs = [pdef('top_n', ParamType.INT, [], { default: 2 })];
  const result = evaluate(pdefs, {});
  assert.deepEqual(result, []);
});

test('type(int): string flags', () => {
  const pdefs = [pdef('top_n', ParamType.INT, [], { default: 2 })];
  const result = evaluate(pdefs, { top_n: 'abc' });
  assert.equal(result.length, 1);
  assert.equal(result[0].param, 'top_n');
  assert.equal(result[0].actual, 'abc');
});

test('type(int): int empty', () => {
  const pdefs = [pdef('top_n', ParamType.INT, [], { default: 2 })];
  const result = evaluate(pdefs, { top_n: 5 });
  assert.deepEqual(result, []);
});

test('type(int): boolean is not int (flags)', () => {
  // Python: isinstance(True, bool) → 違反（bool を int 扱いしない）
  const pdefs = [pdef('top_n', ParamType.INT, [], { default: 2 })];
  const result = evaluate(pdefs, { top_n: true });
  assert.equal(result.length, 1);
  assert.equal(result[0].param, 'top_n');
});

// --- 集合的性質 ------------------------------------------------------------

test('empty param defs empty', () => {
  const result = evaluate([], {});
  assert.deepEqual(result, []);
});

test('returns array type', () => {
  const pdefs = [pdef('a', ParamType.FLOAT, [lt('a', 'b')]), pdef('b', ParamType.FLOAT)];
  const result = evaluate(pdefs, { a: 1, b: 2 });
  assert.ok(Array.isArray(result));
});
