// properties_dialog.js の純ヘルパ検証（node:test / node:assert）。
//
// 対象: adapter/front/properties_dialog.js のうち DOM 非依存な純関数 toHex のみ。
//   ダイアログ DOM 本体は jsdom 等の新規依存が必要（C-2 で禁止）のため E2E（xvfb）で
//   カバーし、ここでは node:test で検証可能な純ロジックに限定する（設計 §10.4）。
// 構造: Arrange-Act-Assert（AAA）。各テスト独立・再現可能（F.I.R.S.T）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { toHex } from '../js/adapter/front/properties_dialog.js';

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
