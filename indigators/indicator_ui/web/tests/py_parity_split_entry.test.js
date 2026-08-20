// py_parity_split_entry.test.js — Python 権威（split_entry_plan.build_split_entry_plan）との
//   一致検定（ISSUE-368 スライス 2）。
//
// 権威は Python（simulator/usecase/split_entry_plan.py）。fixture
//   （simulator/tests/fixtures/split_entry/js_golden_cases.json＝
//   simulator/tools/export_split_entry_fixtures.py が生成）に対し、JS 側の写し
//   （js/domain/split_entry_plan.js）の一致を全ケース検定する。
//
// 許容差: **0（厳密一致）**。本モジュールは四則演算と Math.floor のみで超越関数を含まず、
//   ロスカットは account_margin_core.js（権威の鏡）へ委譲し、cap_lot の二分探索も
//   Python と同一の上限・反復回数・中点式を使う。したがって IEEE754 上で bit 一致する。
//   （参照実装 HTML との最終桁差は Python 側 split_entry_plan.py の docstring に実測記録があるが、
//    それは HTML↔権威の差であって JS↔Python の差ではない。）
//
// fixture への到達方法は market_profile/web/tests/py_parity_golden.test.js と同方式。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { buildSplitEntryPlan, generateWeights } from '../js/domain/split_entry_plan.js';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', '..');
const golden = JSON.parse(
  readFileSync(join(REPO, 'simulator', 'tests', 'fixtures', 'split_entry', 'js_golden_cases.json'), 'utf8'),
);

/** fixture の無限大トークン（標準 JSON に無限大が無いため文字列で運ぶ）を復元する。 */
function decode(value) {
  if (value === golden.infinity_token) {
    return Infinity;
  }
  if (Array.isArray(value)) {
    return value.map(decode);
  }
  return value;
}

test('fixture は格子＋分岐の境界ケースを保持している', () => {
  assert.ok(golden.cases.length >= 100, `ケース数 ${golden.cases.length}`);
  assert.equal(golden.infinity_token, 'Infinity');
});

test('generateWeights は Python 権威と一致する（equal/linear/double/custom）', () => {
  assert.deepEqual(generateWeights(3, 'equal'), [1, 1, 1]);
  assert.deepEqual(generateWeights(3, 'linear'), [1, 2, 3]);
  assert.deepEqual(generateWeights(4, 'double'), [1, 2, 4, 8]);
  assert.deepEqual(generateWeights(2, 'custom', [3, 1, 2]), [3, 1]);
  assert.throws(() => generateWeights(3, 'fib'), /weight_pattern/);
  assert.throws(() => generateWeights(3, 'custom'), /custom_weights/);
  assert.throws(() => generateWeights(3, 'custom', [1, 2]), /custom_weights/);
});

test('buildSplitEntryPlan は Python 権威と全ケース厳密一致する（許容 0）', () => {
  for (const c of golden.cases) {
    const got = buildSplitEntryPlan(c.spec);
    for (const [key, rawWant] of Object.entries(c.expected)) {
      const want = decode(rawWant);
      const value = got[key];
      if (Array.isArray(want)) {
        assert.equal(value.length, want.length, `${c.id} ${key} 要素数`);
        for (let i = 0; i < want.length; i += 1) {
          assert.equal(value[i], want[i], `${c.id} ${key}[${i}]`);
        }
      } else {
        assert.equal(value, want, `${c.id} ${key}`);
      }
    }
  }
});

test('4 分岐が fixture 側で真偽ともに現れ、JS でも同一条件で立つ', () => {
  for (const flag of ['stop_invalid', 'round_zeroed', 'immediate_lc', 'margin_binds']) {
    const truthy = golden.cases.filter((c) => c.expected[flag]);
    const falsy = golden.cases.filter((c) => !c.expected[flag]);
    assert.ok(truthy.length > 0 && falsy.length > 0, `${flag} が片側しかない`);
    for (const c of truthy) {
      assert.equal(buildSplitEntryPlan(c.spec)[flag], true, `${c.id} ${flag}`);
    }
  }
});
