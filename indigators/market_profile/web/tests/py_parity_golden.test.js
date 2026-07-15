// py_parity_golden.test.js — Python 実装との生成同期検定（ISSUE-087 🔴-3）。
//
// 権威は Python（marketdata.session_day / _value_area / marketdata.tf_meta）。fixture
// （fixtures/py_parity_golden.json＝tools/gen_js_parity_golden.py が生成）に対し、JS 側の
// 二重実装（domain/session_day.js・dwell_accumulator.valueArea・domain/tf_meta.js）の一致を
// 網羅検定する（旧: ハードコード 2 値の弱同期を置換）。規則変更時は fixture を再生成する。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import {
  sessionDayStart, nextSessionDayStart, sessionDateLabel, sessionBarTime,
} from '../js/domain/session_day.js';
import { TF_BAR_SEC } from '../js/domain/tf_meta.js';
import { valueArea } from '../js/domain/market_profile_dwell_accumulator.js';

const golden = JSON.parse(
  readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'py_parity_golden.json'), 'utf8'),
);

test('session_day.js は Python 実装と全境界ケースで一致する（DST 切替・週/月/年跨ぎ・160点）', () => {
  assert.ok(golden.session_day.length >= 100, 'fixture が境界網羅されている');
  for (const c of golden.session_day) {
    assert.equal(sessionDayStart(c.t), c.dayStart, `sessionDayStart(${c.t})`);
    assert.equal(nextSessionDayStart(c.dayStart), c.nextDayStart, `nextSessionDayStart(${c.dayStart})`);
    assert.equal(sessionDateLabel(c.t), c.label, `sessionDateLabel(${c.t})`);
    assert.equal(sessionBarTime(c.t), c.barTime, `sessionBarTime(${c.t})`);
  }
});

test('tf_meta.js の TF_BAR_SEC は Python marketdata.tf_meta と一致する', () => {
  assert.deepEqual({ ...TF_BAR_SEC }, golden.tf_bar_sec);
});

test('valueArea は Python _value_area と一致する（整数 TPO・float z の両系）', () => {
  for (const c of golden.value_area) {
    const [lo, hi] = valueArea(c.centers, c.tpo, c.pct);
    assert.deepEqual([lo, hi], c.expected, `VA(${JSON.stringify(c.tpo)})`);
  }
});
