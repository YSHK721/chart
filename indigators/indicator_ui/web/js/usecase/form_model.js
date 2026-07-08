// フォームモデル構築（usecase/form_model.js）。
//
// IndicatorDef.params（catalog の ParamDef[]）からプロパティダイアログのフォーム項目を
// 構築し、条件付き有効化・検証委譲・既定復元を提供する純関数群。
// DOM/chart/fetch/localStorage 非依存（母体「純ロジック分離」方針・§10.4）。
//
// 検証単一定義（C-3）: validateForm は domain constraint_eval.js の evaluate へ委譲する。
// range_from<range_to の「両者非 null 時のみ」前提付き検証のみ本モジュールで最小実装する
//   （素の LT は両者 null 時に null<null=false で誤検出するため。設計 §11.2 Q-3）。
// ConstraintEvaluator のロジックは一切変更しない。

import { evaluate, ParamType } from '../domain/constraint_eval.js';

// ParamType → デフォルトコントロール種別の写像（§3.1）。
// control_type 明示があれば優先（buildFormModel 内で上書き）。
const CONTROL_BY_TYPE = Object.freeze({
  [ParamType.INT]: 'number',
  [ParamType.FLOAT]: 'number',
  [ParamType.ENUM]: 'select',
  [ParamType.BOOL]: 'checkbox',
  [ParamType.STRING]: 'text',
  [ParamType.COLOR]: 'color',
  [ParamType.FLOAT_LIST]: 'list',
  [ParamType.ENUM_LIST]: 'multiselect',
});

// ParamDef からコントロール種別を一意に決定（明示 controlType 優先→ParamType 写像）。
function resolveControlType(pdef) {
  if (pdef.controlType !== null && pdef.controlType !== undefined) {
    return pdef.controlType;
  }
  return CONTROL_BY_TYPE[pdef.type] ?? 'text';
}

// ParamDef → FieldDesc の任意メタデータ既定値（省略キーのフォールバックを 1 箇所に集約）。
const FIELD_META_DEFAULTS = Object.freeze({
  enumValues: null,
  step: null,
  min: null,
  max: null,
  unit: null,
  tooltip: null,
  group: null,
  order: null,
  conditionalEnable: null,
  // conditionalVisible: 条件付き“表示”（トグル）。conditionalEnable（グレーアウト）と対称。
  //   { when: { param, equals } } が偽のとき当該フィールド行を非表示にする（§3.5 拡張）。
  conditionalVisible: null,
  // enumLabels: enum 値 → 表示名マップ（properties_dialog の select 日本語表示）。
  enumLabels: null,
});

// pdef の任意メタデータを FIELD_META_DEFAULTS のキーで既定フォールバック付きに解決する。
function pickFieldMeta(pdef) {
  const out = {};
  for (const key of Object.keys(FIELD_META_DEFAULTS)) {
    out[key] = pdef[key] ?? FIELD_META_DEFAULTS[key];
  }
  return out;
}

// 1 ParamDef を FieldDesc へ変換（初期値は currentParams 優先→default フォールバック）。
function paramToField(pdef, currentParams) {
  const hasCurrent = Object.prototype.hasOwnProperty.call(currentParams, pdef.name);
  return {
    name: pdef.name,
    // label 直接指定（日本語）優先 → labelKey → 既定 label.<name>。
    label: pdef.label ?? pdef.labelKey ?? `label.${pdef.name}`,
    controlType: resolveControlType(pdef),
    value: hasCurrent ? currentParams[pdef.name] : pdef.default,
    default: pdef.default,
    constraints: pdef.constraints ?? [],
    ...pickFieldMeta(pdef),
  };
}

// IndicatorDef.params → フォームモデル。
// 戻り値: { fields: FieldDesc[], groups: [{ key, fields: FieldDesc[] }] }。
// - uiVisible===false の param は除外（§3.3.1）。
// - グループは初出順、グループ内は order 昇順（order 無しは元配列順を保持・安定ソート）。
// - group=null は先頭の無見出しグループ（参照 UI「基本」群相当・§3.4）。
export function buildFormModel(def, currentParams = {}) {
  const fields = (def.params ?? [])
    .filter((p) => p.uiVisible !== false)
    .map((p) => paramToField(p, currentParams));

  // グループ初出順を保持しつつ集約。
  const groupOrder = [];
  const byGroup = new Map();
  for (const field of fields) {
    const key = field.group ?? null;
    if (!byGroup.has(key)) {
      byGroup.set(key, []);
      groupOrder.push(key);
    }
    byGroup.get(key).push(field);
  }

  const groups = groupOrder.map((key) => ({
    key,
    fields: sortByOrder(byGroup.get(key)),
  }));

  return { fields, groups };
}

// order 昇順の安定ソート（order 無し=元順を維持）。
// 元インデックスを退避して order 同値・null 同士は元順を保つ（安定性を明示）。
function sortByOrder(fields) {
  const indexed = fields.map((field, originalIndex) => ({ field, originalIndex }));
  indexed.sort((a, b) => {
    const orderA = a.field.order;
    const orderB = b.field.order;
    if (orderA === null && orderB === null) return a.originalIndex - b.originalIndex;
    if (orderA === null) return 1;
    if (orderB === null) return -1;
    if (orderA !== orderB) return orderA - orderB;
    return a.originalIndex - b.originalIndex;
  });
  return indexed.map((entry) => entry.field);
}

// 各パラメータの有効/無効を決定（§3.5 条件付き有効化）。
// conditionalEnable.when（{param,equals}）が偽のとき disabled。未指定は常時 enabled。
// 実コード対応: atr_period は normalize=="atr" のときのみ ATR 計算で使用（robust_bands.py:135-138）。
export function computeEnabled(def, values = {}) {
  const enabled = {};
  for (const pdef of def.params ?? []) {
    const cond = pdef.conditionalEnable;
    if (cond === null || cond === undefined) {
      enabled[pdef.name] = true;
      continue;
    }
    const { param, equals } = cond.when;
    enabled[pdef.name] = values[param] === equals;
  }
  return enabled;
}

// 各パラメータの表示/非表示を決定（§3.5 条件付き表示・computeEnabled と対称）。
// conditionalVisible.when（{param,equals}）が偽のとき非表示（hidden）。未指定は常時 visible。
// 静的除外（uiVisible===false）は buildFormModel が担い、本関数は動的トグルを担う（併存）。
// 用途: market_profile の bins は resmode==bins のとき表示 / range は resmode==range のとき表示（解像度トグル）。
export function computeVisible(def, values = {}) {
  const visible = {};
  for (const pdef of def.params ?? []) {
    const cond = pdef.conditionalVisible;
    if (cond === null || cond === undefined) {
      visible[pdef.name] = true;
      continue;
    }
    const { param, equals } = cond.when;
    visible[pdef.name] = values[param] === equals;
  }
  return visible;
}

// フォーム検証（F-11・§5）。
// ConstraintEvaluator.evaluate（単一定義・C-3）へ委譲し、その違反へ
// range_from<range_to の前提付き違反（両者非 null 時のみ）を付加する（§11.2 Q-3）。
// 戻り値: { violations: Violation[], ok: boolean }。
export function validateForm(def, values) {
  const violations = evaluate(def.params, values);

  // range_from<range_to: 両者が非 null（指定）のときのみ評価。素の LT を
  // ConstraintEvaluator に置くと両者 null 時に null<null=false で誤検出するため、
  // 「両者非 null」前提を本モジュールで前処理する（ConstraintEvaluator は不変）。
  const hasRangeFrom = paramExists(def, 'range_from');
  const hasRangeTo = paramExists(def, 'range_to');
  if (hasRangeFrom && hasRangeTo) {
    const from = values.range_from;
    const to = values.range_to;
    if (isNonNullNumber(from) && isNonNullNumber(to) && !(from < to)) {
      violations.push({
        param: 'range_from',
        constraint: 'lt(range_from,range_to)',
        expected: 'range_from<range_to',
        actual: from,
      });
    }
  }

  return { violations, ok: violations.length === 0 };
}

function paramExists(def, name) {
  return (def.params ?? []).some((p) => p.name === name);
}

function isNonNullNumber(v) {
  return v !== null && v !== undefined && typeof v === 'number';
}

// 全パラメータを ParamDef.default へ復元（§7 デフォルト復元）。
// 戻り値: { name: default }。default が null の param も null として返す。
export function resetToDefaults(def) {
  const values = {};
  for (const pdef of def.params ?? []) {
    values[pdef.name] = pdef.default;
  }
  return values;
}
