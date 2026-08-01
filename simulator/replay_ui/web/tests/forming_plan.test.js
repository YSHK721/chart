// forming_plan.test.js — 足内一括計算の純ロジック（ISSUE-232）。
//
// 固定する契約:
//   - sampleIndices: 昇順ユニーク・末尾 n-1 を必ず含む（末尾＝バー確定値。欠けると確定に往復が要る）
//   - formingStatesAt: animateForming と同一の畳み方（open 固定・hi/lo 累積・close=当該ティック）
//   - planSignature: 指標構成・variant・params・窓のいずれかが変われば必ず変わる（誤描画の遮断）

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  sampleIndices, formingStatesAt, planSignature, MAX_FORMING_STEPS,
} from '../js/replay/forming_plan.js';

test('sampleIndices: 上限以下なら全ティックを対象にする', () => {
  assert.deepEqual(sampleIndices(5, 32), [0, 1, 2, 3, 4]);
});

test('sampleIndices: 上限超過は等間隔へ縮退し、先頭と末尾を必ず含む', () => {
  const idx = sampleIndices(1000, 10);
  assert.equal(idx.length, 10);
  assert.equal(idx[0], 0);
  assert.equal(idx[idx.length - 1], 999, '末尾を含まないとバー確定値に往復が必要になる');
  for (let i = 1; i < idx.length; i++) {
    assert.ok(idx[i] > idx[i - 1], '昇順ユニークでない');
  }
});

test('sampleIndices: 実 tick 数（数万）でも上限で頭打ちになる', () => {
  assert.ok(sampleIndices(30000).length <= MAX_FORMING_STEPS + 1);
});

test('sampleIndices: 空・不正は空配列', () => {
  assert.deepEqual(sampleIndices(0), []);
  assert.deepEqual(sampleIndices(-1), []);
  assert.deepEqual(sampleIndices(NaN), []);
});

test('formingStatesAt: open 固定・high/low 累積・close=当該ティック（animateForming と同一規則）', () => {
  const cd = { time: 100 };
  const prices = [10, 12, 9, 11];
  const states = formingStatesAt(cd, prices, [0, 1, 2, 3]);
  assert.deepEqual(states, [
    { time: 100, open: 10, high: 10, low: 10, close: 10 },
    { time: 100, open: 10, high: 12, low: 10, close: 12 },
    { time: 100, open: 10, high: 12, low: 9, close: 9 },
    { time: 100, open: 10, high: 12, low: 9, close: 11 },
  ]);
});

test('formingStatesAt: 間引いた添字でも high/low は全ティックの累積（飛ばした極値を落とさない）', () => {
  const states = formingStatesAt({ time: 1 }, [10, 99, 5, 11], [3]);
  assert.deepEqual(states, [{ time: 1, open: 10, high: 99, low: 5, close: 11 }]);
});

test('formingStatesAt: 空入力は空配列', () => {
  assert.deepEqual(formingStatesAt(null, [1], [0]), []);
  assert.deepEqual(formingStatesAt({ time: 1 }, [], [0]), []);
});

test('planSignature: params / variant / 窓が変われば署名も変わる', () => {
  const base = {
    targets: [{ instanceId: 'ma#1', indicatorId: 'moving_averages', variant: 'default', params: { length: 20 } }],
    timeframe: '5m', limit: 100, untilTime: 999,
  };
  const sig = planSignature(base);
  assert.equal(planSignature({ ...base }), sig, '同一入力で署名が揺れる');
  assert.notEqual(planSignature({ ...base, timeframe: '1h' }), sig);
  assert.notEqual(planSignature({ ...base, limit: 101 }), sig);
  assert.notEqual(planSignature({ ...base, untilTime: 1000 }), sig);
  assert.notEqual(planSignature({
    ...base,
    targets: [{ ...base.targets[0], params: { length: 21 } }],
  }), sig, 'params 変更が署名に出ない（陳腐化した値で描画してしまう）');
  assert.notEqual(planSignature({
    ...base,
    targets: [{ ...base.targets[0], variant: 'other' }],
  }), sig);
});
