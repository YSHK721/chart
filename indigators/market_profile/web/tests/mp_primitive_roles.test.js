// mp_primitive_roles.js — 単一 primitive 上のロール別ファサード（ISP・ISSUE-099 🟡-5）検証。
//
// 目的: god interface（MarketProfileHistogramPrimitive の 7 公開メソッド）を、排他的に使う
//   2 ロールへ分離する。ProfileSink={setProfile,setVisible,setSnapshot,setSessions,setCursorTime}
//   （MarketProfileActor 用）／TfPeriodSink={setTfPeriods,tfPeriodLevelAt}（TfPeriod 用）。
//   本テストは「各ファサードが正しいメソッド集合のみを公開し、下層 primitive へ透過委譲する」
//   ことと「attach 点は単一（seriesPrimitive で下層 ISeriesPrimitive を取り出せる）」を固定する。
// 構造: Arrange-Act-Assert。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ProfileSink, TfPeriodSink } from '../js/adapter/front/mp_primitive_roles.js';

// 呼び出しを記録する fake primitive（7 メソッド＝god interface）。
function fakePrimitive() {
  const calls = [];
  const rec = (name) => (...args) => { calls.push({ name, args }); return `${name}:ret`; };
  return {
    calls,
    setProfile: rec('setProfile'),
    setVisible: rec('setVisible'),
    setSnapshot: rec('setSnapshot'),
    setSessions: rec('setSessions'),
    setCursorTime: rec('setCursorTime'),
    setTfPeriods: rec('setTfPeriods'),
    tfPeriodLevelAt: rec('tfPeriodLevelAt'),
  };
}

function ownMethods(obj) {
  return Object.getOwnPropertyNames(Object.getPrototypeOf(obj))
    .filter((n) => n !== 'constructor' && typeof obj[n] === 'function')
    .sort();
}

test('ProfileSink はプロファイル役の 5 メソッド＋attach 用 seriesPrimitive のみ公開する', () => {
  const sink = new ProfileSink(fakePrimitive());
  const methods = ownMethods(sink);
  assert.deepEqual(methods,
    ['seriesPrimitive', 'setCursorTime', 'setProfile', 'setSessions', 'setSnapshot', 'setVisible']);
  // tf-period 役のメソッドは持たない（ISP: 未使用面を露出しない）。
  assert.equal(typeof sink.setTfPeriods, 'undefined');
  assert.equal(typeof sink.tfPeriodLevelAt, 'undefined');
});

test('TfPeriodSink は tf-period 役の 2 メソッドのみ公開する', () => {
  const sink = new TfPeriodSink(fakePrimitive());
  assert.deepEqual(ownMethods(sink), ['setTfPeriods', 'tfPeriodLevelAt']);
  // プロファイル役のメソッドは持たない。
  for (const m of ['setProfile', 'setVisible', 'setSnapshot', 'setSessions', 'setCursorTime']) {
    assert.equal(typeof sink[m], 'undefined', `${m} は非公開`);
  }
});

test('ProfileSink は下層 primitive へ同一引数で透過委譲し戻り値を素通す', () => {
  const prim = fakePrimitive();
  const sink = new ProfileSink(prim);
  assert.equal(sink.setProfile({ a: 1 }), 'setProfile:ret');
  assert.equal(sink.setVisible(true), 'setVisible:ret');
  assert.equal(sink.setSnapshot(false), 'setSnapshot:ret');
  assert.equal(sink.setSessions([1, 2]), 'setSessions:ret');
  assert.equal(sink.setCursorTime(99), 'setCursorTime:ret');
  assert.deepEqual(prim.calls, [
    { name: 'setProfile', args: [{ a: 1 }] },
    { name: 'setVisible', args: [true] },
    { name: 'setSnapshot', args: [false] },
    { name: 'setSessions', args: [[1, 2]] },
    { name: 'setCursorTime', args: [99] },
  ]);
});

test('TfPeriodSink は下層 primitive へ同一引数で透過委譲し戻り値を素通す', () => {
  const prim = fakePrimitive();
  const sink = new TfPeriodSink(prim);
  assert.equal(sink.setTfPeriods([{ time: 1 }], 0.5), 'setTfPeriods:ret');
  assert.equal(sink.tfPeriodLevelAt(1, 100), 'tfPeriodLevelAt:ret');
  assert.deepEqual(prim.calls, [
    { name: 'setTfPeriods', args: [[{ time: 1 }], 0.5] },
    { name: 'tfPeriodLevelAt', args: [1, 100] },
  ]);
});

test('ProfileSink.seriesPrimitive は単一 attach 点として下層 primitive を返す', () => {
  const prim = fakePrimitive();
  const sink = new ProfileSink(prim);
  assert.equal(sink.seriesPrimitive(), prim);
});
