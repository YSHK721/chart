// form_model.js の仕様検証（node:test / node:assert）。
//
// 対象: usecase/form_model.js（純関数・DOM/chart/fetch 非依存）。
// 設計入力: 内部設計_パラメータ設定ダイアログ.md v0.2.0
//   §3.1 ParamType→controlType 写像 / §3.3 UI メタデータ拡張 / §3.5 条件付き有効化
//   §4 3 指標フォーム定義 / §5 検証統合(F-11) / §7 デフォルト復元。
// 構造: Arrange-Act-Assert（AAA）。各テスト独立・再現可能（F.I.R.S.T）。
//
// 検証単一定義(C-3): validateForm は domain constraint_eval.js の evaluate へ委譲する。
// range_from<range_to の「両者非null時のみ」前提付き検証のみ form_model 側で最小実装
//   （素の LT は両者null時に null<null=false で誤検出するため。設計 §11.2 Q-3）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  buildFormModel,
  computeEnabled,
  computeVisible,
  validateForm,
  resetToDefaults,
} from '../js/usecase/form_model.js';
import { get } from '../js/usecase/catalog.js';
import { ParamType } from '../js/domain/constraint_eval.js';

// ---- buildFormModel: controlType 解決（§3.1 写像・明示優先）-------------------

test('buildFormModel: INT param resolves to number control by ParamType default mapping', () => {
  // Arrange: control_type 未指定の INT パラメータのみを持つ最小 def
  const def = { params: [{ name: 'maxbars', type: ParamType.INT, default: 100 }] };
  // Act
  const model = buildFormModel(def, {});
  // Assert: §3.1 INT→number
  const field = model.fields.find((f) => f.name === 'maxbars');
  assert.equal(field.controlType, 'number');
});

test('buildFormModel: ENUM param resolves to select with enumValues', () => {
  const def = { params: [{ name: 'fitter', type: ParamType.ENUM, default: 'ols', enumValues: ['ols', 'tgp'] }] };
  const model = buildFormModel(def, {});
  const field = model.fields.find((f) => f.name === 'fitter');
  assert.equal(field.controlType, 'select');
  assert.deepEqual(field.enumValues, ['ols', 'tgp']);
});

test('buildFormModel: BOOL param resolves to checkbox', () => {
  const def = { params: [{ name: 'legend', type: ParamType.BOOL, default: false }] };
  const model = buildFormModel(def, {});
  assert.equal(model.fields.find((f) => f.name === 'legend').controlType, 'checkbox');
});

test('buildFormModel: FLOAT_LIST resolves to list editor', () => {
  const def = { params: [{ name: 'probabilities', type: ParamType.FLOAT_LIST, default: [0.95] }] };
  const model = buildFormModel(def, {});
  assert.equal(model.fields.find((f) => f.name === 'probabilities').controlType, 'list');
});

test('buildFormModel: ENUM_LIST resolves to multiselect', () => {
  const def = { params: [{ name: 'buckets', type: ParamType.ENUM_LIST, default: ['nOH'] }] };
  const model = buildFormModel(def, {});
  assert.equal(model.fields.find((f) => f.name === 'buckets').controlType, 'multiselect');
});

test('buildFormModel: COLOR resolves to color control', () => {
  const def = { params: [{ name: 'color', type: ParamType.COLOR, default: '#fff' }] };
  const model = buildFormModel(def, {});
  assert.equal(model.fields.find((f) => f.name === 'color').controlType, 'color');
});

test('buildFormModel: explicit controlType overrides ParamType default mapping (§3.1)', () => {
  // Arrange: FLOAT だが control_type を明示
  const def = { params: [{ name: 'window', type: ParamType.INT, default: 'expanding', controlType: 'window_compound' }] };
  const model = buildFormModel(def, {});
  assert.equal(model.fields.find((f) => f.name === 'window').controlType, 'window_compound');
});

// ---- buildFormModel: 初期値（currentParams 優先→default フォールバック）--------

test('buildFormModel: field value uses currentParams when present', () => {
  const def = { params: [{ name: 'maxbars', type: ParamType.INT, default: 100 }] };
  const model = buildFormModel(def, { maxbars: 250 });
  assert.equal(model.fields.find((f) => f.name === 'maxbars').value, 250);
});

test('buildFormModel: field value falls back to default when currentParams missing', () => {
  const def = { params: [{ name: 'maxbars', type: ParamType.INT, default: 100 }] };
  const model = buildFormModel(def, {});
  assert.equal(model.fields.find((f) => f.name === 'maxbars').value, 100);
});

// ---- buildFormModel: グループ化・order（§3.4）-------------------------------

test('buildFormModel: same group is collected under one heading in first-seen order', () => {
  const def = {
    params: [
      { name: 'a', type: ParamType.INT, default: 1, group: 'calc' },
      { name: 'b', type: ParamType.INT, default: 1, group: 'display' },
      { name: 'c', type: ParamType.INT, default: 1, group: 'calc' },
    ],
  };
  const model = buildFormModel(def, {});
  const groupKeys = model.groups.map((g) => g.key);
  // 初出順: calc, display
  assert.deepEqual(groupKeys, ['calc', 'display']);
  const calc = model.groups.find((g) => g.key === 'calc');
  assert.deepEqual(calc.fields.map((f) => f.name), ['a', 'c']);
});

test('buildFormModel: group=null params collected under the leading basic group', () => {
  const def = {
    params: [
      { name: 'a', type: ParamType.INT, default: 1 },
      { name: 'b', type: ParamType.INT, default: 1, group: 'calc' },
    ],
  };
  const model = buildFormModel(def, {});
  // 先頭が無見出し(null)グループ、その後 calc
  assert.equal(model.groups[0].key, null);
  assert.deepEqual(model.groups[0].fields.map((f) => f.name), ['a']);
});

test('buildFormModel: fields within a group are sorted by order ascending', () => {
  const def = {
    params: [
      { name: 'a', type: ParamType.INT, default: 1, group: 'calc', order: 2 },
      { name: 'b', type: ParamType.INT, default: 1, group: 'calc', order: 1 },
    ],
  };
  const model = buildFormModel(def, {});
  const calc = model.groups.find((g) => g.key === 'calc');
  assert.deepEqual(calc.fields.map((f) => f.name), ['b', 'a']);
});

// ---- buildFormModel: ui_visible=false は除外 -------------------------------

test('buildFormModel: param with uiVisible false is excluded from the form', () => {
  const def = {
    params: [
      { name: 'maxbars', type: ParamType.INT, default: 100 },
      { name: 'time_column', type: ParamType.STRING, default: null, uiVisible: false },
    ],
  };
  const model = buildFormModel(def, {});
  assert.equal(model.fields.find((f) => f.name === 'time_column'), undefined);
  assert.ok(model.fields.find((f) => f.name === 'maxbars'));
});

// ---- buildFormModel: UI メタデータの素通し（tooltip/unit/step/min/max）--------

test('buildFormModel: passes through tooltip/unit/step/min/max metadata', () => {
  const def = {
    params: [{ name: 'q_low', type: ParamType.FLOAT, default: 0.05, tooltip: 'tip.q_low', unit: '%', step: 0.01, min: 0, max: 1 }],
  };
  const field = buildFormModel(def, {}).fields.find((f) => f.name === 'q_low');
  assert.equal(field.tooltip, 'tip.q_low');
  assert.equal(field.unit, '%');
  assert.equal(field.step, 0.01);
  assert.equal(field.min, 0);
  assert.equal(field.max, 1);
});

// ---- computeEnabled: 条件付き有効化（§3.5・robust_bands 実証）-----------------

test('computeEnabled: atr_period is enabled when normalize equals atr', () => {
  // Arrange: atr_period は normalize==atr のときのみ ATR 計算で使われる（robust_bands.py:135-138）
  const def = {
    params: [
      { name: 'normalize', type: ParamType.ENUM, default: 'return', enumValues: ['return', 'atr'] },
      { name: 'atr_period', type: ParamType.INT, default: 14, conditionalEnable: { when: { param: 'normalize', equals: 'atr' } } },
    ],
  };
  // Act
  const enabled = computeEnabled(def, { normalize: 'atr', atr_period: 14 });
  // Assert
  assert.equal(enabled.atr_period, true);
});

test('computeEnabled: atr_period is disabled when normalize equals return', () => {
  const def = {
    params: [
      { name: 'normalize', type: ParamType.ENUM, default: 'return', enumValues: ['return', 'atr'] },
      { name: 'atr_period', type: ParamType.INT, default: 14, conditionalEnable: { when: { param: 'normalize', equals: 'atr' } } },
    ],
  };
  const enabled = computeEnabled(def, { normalize: 'return', atr_period: 14 });
  assert.equal(enabled.atr_period, false);
});

test('computeEnabled: params without conditionalEnable are always enabled', () => {
  const def = { params: [{ name: 'maxbars', type: ParamType.INT, default: 100 }] };
  const enabled = computeEnabled(def, { maxbars: 100 });
  assert.equal(enabled.maxbars, true);
});

// ---- computeVisible: 条件付き表示（トグル・computeEnabled と対称）-------------

test('computeVisible: field is visible when conditionalVisible predicate matches', () => {
  // Arrange: bins は range==auto のときのみ表示（バー幅トグル）
  const def = {
    params: [
      { name: 'range', type: ParamType.ENUM, default: 'auto', enumValues: ['auto', '25'] },
      { name: 'bins', type: ParamType.INT, default: 60, conditionalVisible: { when: { param: 'range', equals: 'auto' } } },
    ],
  };
  // Act
  const visible = computeVisible(def, { range: 'auto', bins: 60 });
  // Assert
  assert.equal(visible.bins, true);
});

test('computeVisible: field is hidden when conditionalVisible predicate does not match', () => {
  const def = {
    params: [
      { name: 'range', type: ParamType.ENUM, default: 'auto', enumValues: ['auto', '25'] },
      { name: 'bins', type: ParamType.INT, default: 60, conditionalVisible: { when: { param: 'range', equals: 'auto' } } },
    ],
  };
  const visible = computeVisible(def, { range: '25', bins: 60 });
  assert.equal(visible.bins, false);
});

test('computeVisible: params without conditionalVisible are always visible', () => {
  const def = { params: [{ name: 'maxbars', type: ParamType.INT, default: 100 }] };
  const visible = computeVisible(def, { maxbars: 100 });
  assert.equal(visible.maxbars, true);
});

test('buildFormModel: passes through conditionalVisible metadata (default null when absent)', () => {
  const def = {
    params: [
      { name: 'a', type: ParamType.INT, default: 1 },
      { name: 'b', type: ParamType.INT, default: 2, conditionalVisible: { when: { param: 'a', equals: 1 } } },
    ],
  };
  const model = buildFormModel(def, {});
  assert.equal(model.fields.find((f) => f.name === 'a').conditionalVisible, null);
  assert.deepEqual(model.fields.find((f) => f.name === 'b').conditionalVisible, { when: { param: 'a', equals: 1 } });
});

// market_profile: バー幅=auto のとき bins 表示 / バー幅=25 のとき bins 非表示（トグル実挙動）。
test('computeVisible: market_profile bins toggles with range (auto→visible, 25→hidden)', () => {
  const def = get('market_profile');
  const visAuto = computeVisible(def, { ...resetToDefaults(def), range: 'auto' });
  assert.equal(visAuto.bins, true);
  const vis25 = computeVisible(def, { ...resetToDefaults(def), range: '25' });
  assert.equal(vis25.bins, false);
});

// ---- validateForm: ConstraintEvaluator への委譲（C-3 単一定義）----------------

test('validateForm: returns ok=true and empty violations for valid default tgp_btlm values', () => {
  const def = get('tgp_btlm');
  const result = validateForm(def, { fitter: 'ols', price: 'open', maxbars: 100, q_low: 0.05, q_high: 0.95 });
  assert.deepEqual(result.violations, []);
  assert.equal(result.ok, true);
});

test('validateForm: flags lt violation when q_low equals q_high (0<q_low<q_high<1 boundary)', () => {
  const def = get('tgp_btlm');
  const result = validateForm(def, { fitter: 'ols', price: 'open', maxbars: 100, q_low: 0.5, q_high: 0.5 });
  // lt(q_low,q_high) 違反が q_low 側に出る（constraint_eval.js:138）
  const v = result.violations.find((x) => x.param === 'q_low' && x.constraint.startsWith('lt'));
  assert.ok(v, 'expected lt violation on q_low');
  assert.equal(result.ok, false);
});

test('validateForm: flags range_open when q_low is at lower boundary 0', () => {
  const def = get('tgp_btlm');
  const result = validateForm(def, { fitter: 'ols', price: 'open', maxbars: 100, q_low: 0, q_high: 0.95 });
  const v = result.violations.find((x) => x.param === 'q_low' && x.constraint.startsWith('range_open'));
  assert.ok(v, 'expected range_open violation on q_low=0');
  assert.equal(result.ok, false);
});

test('validateForm: flags min_value when top_n is below 0 (price_range_power)', () => {
  const def = get('price_range_power');
  const result = validateForm(def, { interval: 0.1, range_from: null, range_to: null, top_n: -1 });
  const v = result.violations.find((x) => x.param === 'top_n' && x.constraint.startsWith('min_value'));
  assert.ok(v, 'expected min_value violation on top_n=-1');
  assert.equal(result.ok, false);
});

// ---- validateForm: range_from<range_to 前提付き検証（§11.2 Q-3・form_model 側）--

test('validateForm: no range violation when both range_from and range_to are null', () => {
  // 両者 null（自動）のとき range_from<range_to は評価しない（素の LT 誤検出を回避）
  const def = get('price_range_power');
  const result = validateForm(def, { interval: 0.1, range_from: null, range_to: null, top_n: 5 });
  assert.equal(result.violations.find((x) => x.param === 'range_from' || x.param === 'range_to'), undefined);
  assert.equal(result.ok, true);
});

test('validateForm: flags range violation when range_from >= range_to (both non-null)', () => {
  const def = get('price_range_power');
  const result = validateForm(def, { interval: 0.1, range_from: 100, range_to: 50, top_n: 5 });
  const v = result.violations.find((x) => x.param === 'range_from' && x.constraint.startsWith('lt'));
  assert.ok(v, 'expected range_from<range_to violation');
  assert.equal(result.ok, false);
});

test('validateForm: no range violation when range_from < range_to (both non-null)', () => {
  const def = get('price_range_power');
  const result = validateForm(def, { interval: 0.1, range_from: 50, range_to: 100, top_n: 5 });
  assert.equal(result.violations.find((x) => x.param === 'range_from'), undefined);
  assert.equal(result.ok, true);
});

// ---- resetToDefaults: 既定復元（§7）---------------------------------------

test('resetToDefaults: returns each ParamDef default for tgp_btlm', () => {
  const def = get('tgp_btlm');
  const values = resetToDefaults(def);
  assert.equal(values.maxbars, 100); // M-1 是正（core.py:33 DEFAULT_MAXBARS=100）
  assert.equal(values.q_low, 0.05);
  assert.equal(values.q_high, 0.95);
  assert.equal(values.fitter, 'ols');
});

test('resetToDefaults: round-trips probabilities 7-level default for profit_band', () => {
  const def = get('profit_band');
  const values = resetToDefaults(def);
  // probabilities 実既定 7 水準（profit_band/src/core.py:19 PROBABILITIES）
  assert.deepEqual(values.probabilities, [0.51, 0.8, 0.85, 0.9, 0.95, 0.98, 0.99]);
});

test('resetToDefaults: default null param is restored to null', () => {
  const def = get('price_range_power');
  const values = resetToDefaults(def);
  // range_from/range_to 実既定 None（lwc_chart.py:41-42）
  assert.equal(values.range_from, null);
  assert.equal(values.range_to, null);
});

// ---- moving_averages: 単一MAフォーム（日本語ラベル/enumLabels/条件付き有効化）----------
test('buildFormModel: moving_averages surfaces Japanese label and enumLabels and 平滑化/計算 groups', () => {
  const def = get('moving_averages');
  const model = buildFormModel(def, {});
  const byName = new Map(model.fields.map((f) => [f.name, f]));
  // label 直接指定（日本語）がフィールドへ反映される。
  assert.equal(byName.get('ma_type').label, '種別');
  assert.equal(byName.get('source').label, 'ソース');
  // enumLabels が select 表示用にフィールドへ渡る。
  assert.equal(byName.get('source').enumLabels.hl2, '(高値 + 安値)/2');
  // グループ見出し（日本語キーをそのまま見出しに使う）。
  const groupKeys = model.groups.map((g) => g.key);
  assert.ok(groupKeys.includes(null)); // 基本（無見出し）
  assert.ok(groupKeys.includes('平滑化'));
  assert.ok(groupKeys.includes('計算'));
});

test('computeEnabled: moving_averages bb_stddev is enabled only when smoothing_type=sma_bb', () => {
  const def = get('moving_averages');
  // 既定（smoothing_type=none）→ bb_stddev 無効（グレーアウト・画像準拠）。
  const off = computeEnabled(def, resetToDefaults(def));
  assert.equal(off.bb_stddev, false);
  // sma_bb 選択 → 有効。
  const on = computeEnabled(def, { ...resetToDefaults(def), smoothing_type: 'sma_bb' });
  assert.equal(on.bb_stddev, true);
});
