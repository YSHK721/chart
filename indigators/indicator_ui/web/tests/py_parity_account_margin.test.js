// py_parity_account_margin.test.js — Python 権威（account_engine.official_*）との一致検定
//   （ISSUE-368 スライス 2）。
//
// 権威は Python（simulator/usecase/account_engine.py の official_required_margin /
//   official_losscut_price）。fixture（simulator/tests/fixtures/account_engine/js_golden_cases.json＝
//   simulator/tools/export_account_engine_fixtures.py が生成）に対し、JS 側の写し
//   （js/domain/account_margin_core.js）の一致を全ケース検定する。
//   規則変更時は生成器を再実行する（Python 側の鮮度検定
//   simulator/tests/unit/test_account_engine_js_fixture_sync.py が再生成漏れを Red にする）。
//
// 許容差: **0（厳密一致）**。証拠金・ロスカットは四則演算のみで超越関数を含まないため、
//   IEEE754 の同一手順は Python と V8 で同一結果になる（ULP 差の余地が無い）。
//
// fixture への到達方法は market_profile/web/tests/py_parity_golden.test.js と同方式
//   （import.meta.url からの相対パス解決）。fixture は Python 側の追跡物であるため、
//   web/tests 配下へ写しを置かない（複製を作らない）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { requiredMargin, losscutPrice, totalUnits, averagePrice } from '../js/domain/account_margin_core.js';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', '..');
const golden = JSON.parse(
  readFileSync(join(REPO, 'simulator', 'tests', 'fixtures', 'account_engine', 'js_golden_cases.json'), 'utf8'),
);

test('fixture は生成器の格子（60 ケース）を保持している', () => {
  assert.equal(golden.cases.length, 60);
});

test('account_margin_core.js は Python 権威と全 60 ケースで厳密一致する', () => {
  for (const c of golden.cases) {
    const entries = c.entries;
    const exp = c.expected;
    assert.equal(totalUnits(entries), exp.total_units, `${c.id} total_units`);
    assert.equal(averagePrice(entries), exp.avg_price, `${c.id} avg_price`);

    const req = requiredMargin(entries, c.margin_rate, c.point_value);
    assert.equal(req, exp.required_margin, `${c.id} required_margin`);
    assert.equal(req / c.balance, exp.margin_use, `${c.id} margin_use`);

    const x = losscutPrice(c.direction, entries, c.balance, c.margin_rate, c.point_value);
    assert.equal(x, exp.losscut_price, `${c.id} losscut_price`);
    assert.equal(Math.abs(exp.avg_price - x), exp.losscut_distance, `${c.id} losscut_distance`);
  }
});

test('建玉が無いときのロスカット価格は null（Python の None と同義）', () => {
  assert.equal(losscutPrice('long', [], 172000, 0.1, 1), null);
  assert.equal(losscutPrice('long', [{ price: 58700, units: 0 }], 172000, 0.1, 1), null);
});
