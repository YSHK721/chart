// catalog.js の仕様検証（node:test / node:assert）。
//
// 対象: usecase/catalog.js レジストリ（list / get）。
// 設計入力: 内部設計書 §3.1.3（IndicatorDef）、実在 4 バインディング
//   （tgp_btlm / profit_band global,robust / price_range_power）。
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { list, get } from '../js/usecase/catalog.js';
import { IndicatorDef } from '../js/domain/domain_models.js';

test('catalog: list returns the 3 registered indicators', () => {
  // Act
  const defs = list();
  // Assert（tgp_btlm / profit_band / price_range_power）
  const ids = defs.map((d) => d.id).sort();
  assert.deepEqual(ids, ['price_range_power', 'profit_band', 'tgp_btlm']);
});

test('catalog: list returns IndicatorDef instances with series>=1', () => {
  const defs = list();
  for (const d of defs) {
    assert.ok(d instanceof IndicatorDef);
    assert.ok(d.series.length >= 1);
  }
});

test('catalog: get returns the indicator by id', () => {
  const d = get('tgp_btlm');
  assert.equal(d.id, 'tgp_btlm');
});

test('catalog: get unknown id returns null', () => {
  // 未知 id は null（呼び出し側で扱う）
  const d = get('does_not_exist');
  assert.equal(d, null);
});

test('catalog: profit_band exposes global and robust variants', () => {
  const d = get('profit_band');
  assert.deepEqual([...d.compute.variants].sort(), ['global', 'robust']);
});

test('catalog: tgp_btlm has fitter backend_param', () => {
  const d = get('tgp_btlm');
  assert.equal(d.compute.backendParam, 'fitter');
});

test('catalog: each indicator carries display name, category and tab for the dialog', () => {
  for (const d of list()) {
    assert.ok(typeof d.displayNameKey === 'string' && d.displayNameKey.length > 0);
    assert.ok(d.category && typeof d.category.group === 'string');
    assert.ok(typeof d.tab === 'string' && d.tab.length > 0);
  }
});
