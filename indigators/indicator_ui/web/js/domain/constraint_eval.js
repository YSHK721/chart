// ConstraintEvaluator の JS 移植（Python domain/validation.py の evaluate と一致）。
//
// CONSTRAINT 評価の唯一の実装（フロント事前検証 F-11）。DOM/chart/fetch 非依存の純ロジック。
// Python ConstraintEvaluator.evaluate と同一テストベクタで一致する（§8 T-INT-5 パリティ）。
//
// ParamDef（JS plain object）:
//   { name, labelKey, type, default, enumValues, constraints }
// Constraint（JS plain object）:
//   { kind, operands, messageKey, when? }
// Violation（戻り値要素）:
//   { param, constraint, expected, actual }

export const ParamType = Object.freeze({
  INT: 'int',
  FLOAT: 'float',
  ENUM: 'enum',
  BOOL: 'bool',
  STRING: 'string',
  COLOR: 'color',
  FLOAT_LIST: 'float_list',
  ENUM_LIST: 'enum_list',
});

export const ConstraintKind = Object.freeze({
  LT: 'lt',
  LTE: 'lte',
  GT: 'gt',
  GTE: 'gte',
  RANGE_OPEN: 'range_open',
  MIN_VALUE: 'min_value',
  CONDITIONAL: 'conditional',
});

// 2 オペランド比較種別の演算子・記号（lt/lte/gt/gte）。
const COMPARATORS = {
  [ConstraintKind.LT]: [(a, b) => a < b, '<'],
  [ConstraintKind.LTE]: [(a, b) => a <= b, '<='],
  [ConstraintKind.GT]: [(a, b) => a > b, '>'],
  [ConstraintKind.GTE]: [(a, b) => a >= b, '>='],
};

// operand が values に存在するパラメータ名か（true なら値へ解決可能）。
function isParamName(operand, values) {
  return typeof operand === 'string' && Object.prototype.hasOwnProperty.call(values, operand);
}

// operand がパラメータ名なら値へ解決、定数ならそのまま返す。
function resolve(operand, values) {
  return isParamName(operand, values) ? values[operand] : operand;
}

// 全 ParamDef の制約・必須・型・enum を論理積評価し違反を列挙。空配列 = 妥当。
export function evaluate(paramDefs, values) {
  const violations = [];
  for (const pdef of paramDefs) {
    checkRequired(pdef, values, violations);
    checkType(pdef, values, violations);
    checkEnum(pdef, values, violations);
    for (const constraint of pdef.constraints ?? []) {
      const v = evalConstraint(pdef, constraint, values);
      if (v !== null) {
        violations.push(v);
      }
    }
  }
  return violations;
}

// -- 必須/型/enum -----------------------------------------------------------

function checkRequired(pdef, values, violations) {
  const hasDefault = pdef.default !== null && pdef.default !== undefined;
  if (!hasDefault && !Object.prototype.hasOwnProperty.call(values, pdef.name)) {
    violations.push({ param: pdef.name, constraint: 'required', expected: 'present', actual: null });
  }
}

function checkType(pdef, values, violations) {
  if (!Object.prototype.hasOwnProperty.call(values, pdef.name)) {
    return;
  }
  const value = values[pdef.name];
  if (pdef.type === ParamType.INT) {
    // Python: isinstance(value, bool) or not isinstance(value, int) → 違反。
    // JS: boolean を int 扱いしない。整数のみ許容。
    if (typeof value === 'boolean' || !Number.isInteger(value)) {
      violations.push({ param: pdef.name, constraint: 'type(int)', expected: 'int', actual: value });
    }
  }
}

function checkEnum(pdef, values, violations) {
  if (pdef.type !== ParamType.ENUM || pdef.enumValues === null || pdef.enumValues === undefined) {
    return;
  }
  if (!Object.prototype.hasOwnProperty.call(values, pdef.name)) {
    return;
  }
  const value = values[pdef.name];
  if (!pdef.enumValues.includes(value)) {
    violations.push({
      param: pdef.name,
      constraint: 'enum',
      expected: `in ${JSON.stringify(pdef.enumValues)}`,
      actual: value,
    });
  }
}

// -- 相関制約 ---------------------------------------------------------------

function evalConstraint(pdef, constraint, values) {
  const kind = constraint.kind;
  if (kind in COMPARATORS) {
    return evalComparator(pdef, constraint, values);
  }
  if (kind === ConstraintKind.RANGE_OPEN) {
    return evalRangeOpen(pdef, constraint, values);
  }
  if (kind === ConstraintKind.MIN_VALUE) {
    return evalMinValue(pdef, constraint, values);
  }
  if (kind === ConstraintKind.CONDITIONAL) {
    return evalConditional(pdef, constraint, values);
  }
  return null;
}

function evalComparator(pdef, constraint, values) {
  const [op, sym] = COMPARATORS[constraint.kind];
  const [leftOperand, rightOperand] = constraint.operands;
  const left = resolve(leftOperand, values);
  const right = resolve(rightOperand, values);
  if (op(left, right)) {
    return null;
  }
  // 違反箇所のパラメータ: 左がパラメータならそれ、なければ右。
  const param = isParamName(leftOperand, values) ? leftOperand : rightOperand;
  const actual = resolve(param, values);
  return {
    param: String(param),
    constraint: `${constraint.kind}(${leftOperand},${rightOperand})`,
    expected: `${leftOperand}${sym}${rightOperand}`,
    actual,
  };
}

function evalRangeOpen(pdef, constraint, values) {
  const [c1, paramOperand, c2] = constraint.operands;
  const low = resolve(c1, values);
  const high = resolve(c2, values);
  const value = resolve(paramOperand, values);
  // float_list/enum_list はリスト要素ごとに開区間判定。
  const elements = Array.isArray(value) ? value : [value];
  for (const element of elements) {
    if (!(low < element && element < high)) {
      return {
        param: String(paramOperand),
        constraint: `range_open(${c1},_,${c2})`,
        expected: `${c1}<${paramOperand}<${c2}`,
        actual: element,
      };
    }
  }
  return null;
}

function evalMinValue(pdef, constraint, values) {
  const [paramOperand, thresholdOperand] = constraint.operands;
  const value = resolve(paramOperand, values);
  const threshold = resolve(thresholdOperand, values);
  if (value >= threshold) {
    return null;
  }
  return {
    param: String(paramOperand),
    constraint: `min_value(${paramOperand},${thresholdOperand})`,
    expected: `${paramOperand}>=${thresholdOperand}`,
    actual: value,
  };
}

function evalConditional(pdef, constraint, values) {
  // 前提 when が真のときのみ内側制約（operands=(param,threshold)、gt>）を評価。
  const when = constraint.when;
  if (when === null || when === undefined || !premiseHolds(when, values)) {
    return null;
  }
  const [paramOperand, thresholdOperand] = constraint.operands;
  const value = resolve(paramOperand, values);
  const threshold = resolve(thresholdOperand, values);
  if (value > threshold) {
    return null;
  }
  return {
    param: String(paramOperand),
    constraint: `conditional(${paramOperand}>${thresholdOperand})`,
    expected: `${paramOperand}>${thresholdOperand}`,
    actual: value,
  };
}

function premiseHolds(when, values) {
  // when 前提の成否（normalize==atr 等の等値前提）。
  const [leftOperand, rightOperand] = when.operands;
  const left = resolve(leftOperand, values);
  const right = resolve(rightOperand, values);
  return left === right;
}
