// properties_dialog.js の純ヘルパ検証（node:test / node:assert）。
//
// 対象: adapter/front/properties_dialog.js のうち DOM 非依存な純関数 toHex のみ。
//   ダイアログ DOM 本体は jsdom 等の新規依存が必要（C-2 で禁止）のため E2E（xvfb）で
//   カバーし、ここでは node:test で検証可能な純ロジックに限定する（設計 §10.4）。
// 構造: Arrange-Act-Assert（AAA）。各テスト独立・再現可能（F.I.R.S.T）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { toHex, PropertiesDialog } from '../js/adapter/front/properties_dialog.js';

// 最小 DOM スタブ（jsdom 等の新規依存を避ける・C-2）。_buildAMethodNote は createElement
//   と className/dataset/textContent のみを使うため、これらを備えた要素を返せば足りる。
function fakeDoc() {
  return {
    createElement() {
      return { className: '', dataset: {}, textContent: '' };
    },
  };
}

const MIN_DEF = { id: 'tgp_btlm', displayNameKey: 'ind.tgp_btlm', params: [], series: [], compute: { variants: ['default'] } };

test('toHex: passes through a 6-digit hex unchanged (lowercased)', () => {
  assert.equal(toHex('#2E9E5B'), '#2e9e5b');
});

test('toHex: expands a 3-digit hex to 6 digits', () => {
  assert.equal(toHex('#0a0'), '#00aa00');
});

test('toHex: converts rgba() to #rrggbb dropping alpha (bull_color)', () => {
  // price_range_power bull_color 既定（lwc_chart.py:27）
  assert.equal(toHex('rgba(46, 158, 91, 0.9)'), '#2e9e5b');
});

test('toHex: converts rgb() to #rrggbb', () => {
  assert.equal(toHex('rgb(210, 67, 58)'), '#d2433a');
});

test('toHex: clamps out-of-range channel values into [0,255]', () => {
  assert.equal(toHex('rgb(300, -5, 128)'), '#ff0080');
});

test('toHex: returns safe default for unparseable input', () => {
  assert.equal(toHex('not-a-color'), '#2962ff');
  assert.equal(toHex(null), '#2962ff');
  assert.equal(toHex(42), '#2962ff');
});

// A 方式注記の出し分け（§9.3・H-1）: B 方式（served）では実反映されるため注記を出さない。
test('_buildAMethodNote returns a note element in A-mode (file://) with the a-method marker', () => {
  // Arrange
  const dialog = new PropertiesDialog({ document: fakeDoc(), def: MIN_DEF, instance: null, mode: 'a' });
  // Act
  const note = dialog._buildAMethodNote();
  // Assert
  assert.ok(note);
  assert.equal(note.className, 'prop-a-method-note');
  assert.equal(note.dataset.aMethodNote, '1');
});

test('_buildAMethodNote returns null in B-mode (served) so the A-method note is hidden', () => {
  // Arrange
  const dialog = new PropertiesDialog({ document: fakeDoc(), def: MIN_DEF, instance: null, mode: 'b' });
  // Act
  const note = dialog._buildAMethodNote();
  // Assert
  assert.equal(note, null);
});

test('PropertiesDialog defaults to A-mode when mode is omitted (backward compatible)', () => {
  const dialog = new PropertiesDialog({ document: fakeDoc(), def: MIN_DEF, instance: null });
  assert.ok(dialog._buildAMethodNote());
});
