// color_roles.test.js — ColorRole 語彙（基本設計_指標カラーテーマ.md §4.1.1）の台帳テスト。
//
// 検証対象は「語彙が閉じていること」そのもの。指標を何件追加しても語彙は増えない（OCP）ため、
//   件数・綴り・順序を固定する。順序はテーマ編集ダイアログの行順（§6.3）の単一情報源でもある。

import test from 'node:test';
import assert from 'node:assert/strict';

import { COLOR_ROLES, ColorRole, isColorRole } from '../js/domain/color_roles.js';

// §4.1.1 の表順（1..14）を逐語で固定する。ダイアログの行順（§6.3）はこの配列から導く。
const EXPECTED_ORDER = [
  'bullish', 'bearish', 'neutral', 'alert', 'primary', 'secondary', 'range',
  'level', 'muted', 'surface', 'grid', 'border', 'text', 'highlight',
];

test('§4.1.1 語彙は 14 種で閉じている（件数・綴り・順序）', () => {
  assert.deepEqual([...COLOR_ROLES], EXPECTED_ORDER);
  assert.equal(COLOR_ROLES.length, 14);
});

test('§4.1.1 ColorRole は各トークンの名前付き定数を持つ（綴りの単一情報源）', () => {
  for (const token of EXPECTED_ORDER) {
    const key = token.toUpperCase();
    assert.equal(ColorRole[key], token, `ColorRole.${key}`);
  }
  assert.equal(Object.keys(ColorRole).length, 14);
});

test('§4.1.1 語彙台帳は凍結されている（実行時に増減しない）', () => {
  assert.ok(Object.isFrozen(COLOR_ROLES));
  assert.ok(Object.isFrozen(ColorRole));
});

test('isColorRole は全域的（未知値・非文字列でも例外を投げず false）', () => {
  for (const token of EXPECTED_ORDER) {
    assert.equal(isColorRole(token), true, token);
  }
  // §5.7 F-C3: 未知トークンは「未宣言」として扱うため、述語は例外ではなく false を返す。
  for (const v of [null, undefined, '', 'Bullish', 'signal', 'center', 'band', 'extreme', 'readout',
    0, 1, {}, [], () => {}, Symbol('bullish')]) {
    assert.equal(isColorRole(v), false, String(typeof v === 'symbol' ? 'symbol' : v));
  }
});

test('§4.1.2 v0.1.0 の旧 7 語のうち改称された 5 語は語彙に含まれない（二重の呼び名を残さない）', () => {
  for (const old of ['signal', 'center', 'band', 'extreme', 'readout']) {
    assert.equal(isColorRole(old), false, old);
  }
  // 不変だった 2 語は在席する。
  assert.equal(isColorRole('primary'), true);
  assert.equal(isColorRole('level'), true);
});
