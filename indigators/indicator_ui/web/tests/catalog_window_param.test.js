// catalog.js の window パラメータ公開検証（node:test / node:assert）。
//
// 対象: usecase/catalog.js の因果化済み 5 指標
//   （profit_adx_needle / profit_arctan / profit_oscillator / profit_rmm / profit_rmm_macd）。
// 仕様: 各指標は標準化窓 window パラメータ（name='window', type=INT, default=120）を
//   UI カタログに公開する。事例は profit_volatility（既公開）と同一行
//   PF_INT('window', 120, { min: 2, label: '標準化窓 W（直近本数）' })。
// 対象外: profit_volatility（既公開・事例）/ profit_oscillator2（window 非対象）。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { get } from '../js/usecase/catalog.js';
import { ParamType } from '../js/domain/constraint_eval.js';

// パラメータ取得ヘルパ（params 配列から name で 1 件）。
function paramOf(def, name) {
  return def.params.find((p) => p.name === name);
}

// window 公開対象の 5 指標 id（因果化済み・volatility 事例に倣う）。
const WINDOW_TARGET_IDS = [
  'profit_adx_needle',
  'profit_arctan',
  'profit_oscillator',
  'profit_rmm',
  'profit_rmm_macd',
];

for (const id of WINDOW_TARGET_IDS) {
  test(`catalog window: ${id} publishes window param with INT type and default 120 (volatility 事例準拠)`, () => {
    // Arrange / Act
    const p = paramOf(get(id), 'window');
    // Assert: window パラメータが name='window' / type=INT / default=120 で公開されている。
    assert.ok(p, `${id} must publish a 'window' param`);
    assert.equal(p.name, 'window');
    assert.equal(p.type, ParamType.INT);
    assert.equal(p.default, 120);
  });
}

// PF_WINDOW ヘルパ DRY 化の核心不変条件: 5 指標の window param が volatility 事例と
// 完全同一（name/type/default/constraints/labelKey/min/label の全フィールド）であること。
// 将来 PF_WINDOW の誤改修（min/constraints/label 欠落）を name/type/default 検証では
// 検出できないため、基準（volatility）との deep-equal で固定する。
test('catalog window: 5 指標の window param が volatility 事例と完全同一（byte 一致）', () => {
  const base = paramOf(get('profit_volatility'), 'window');
  assert.ok(base, 'profit_volatility must publish a window param (事例基準)');
  for (const id of WINDOW_TARGET_IDS) {
    assert.deepEqual(paramOf(get(id), 'window'), base, `${id} window param must be identical to volatility precedent`);
  }
});
