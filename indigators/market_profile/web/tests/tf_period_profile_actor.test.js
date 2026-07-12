// tf_period_profile_actor.js の検証（可視レンジ契機の ensure＋描画・onReady 再描画・enabled）。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { TfPeriodProfileActor } from '../js/adapter/front/tf_period_profile_actor.js';

function fakeBuf() {
  return {
    ensured: [], cols: [], u: 0.5,
    ensure(tf, from, to) { this.ensured.push([tf, from, to]); },
    getColumns(from, to) { return this.cols.filter((c) => c.time >= from && c.time <= to); },
    unit() { return this.u; },
  };
}
function fakePrim() { return { calls: [], setTfPeriods(cols, unit) { this.calls.push([cols, unit]); } }; }

function newActor(buf, prim, range = { from: 100, to: 300 }, tf = '5m') {
  return new TfPeriodProfileActor({
    jitterBuffer: buf, primitive: prim, getTimeframe: () => tf, getVisibleRange: () => range,
  });
}

test('setEnabled(true)→refresh: ensure(可視レンジ)＋現 ready 列を primitive へ', () => {
  const buf = fakeBuf(); buf.cols = [{ time: 150 }, { time: 250 }, { time: 999 }];
  const prim = fakePrim();
  const a = newActor(buf, prim);
  a.setEnabled(true);
  assert.deepEqual(buf.ensured.at(-1), ['5m', 100, 300]);
  assert.deepEqual(prim.calls.at(-1), [[{ time: 150 }, { time: 250 }], 0.5]); // 999 は窓外
});

test('setEnabled(false): primitive の tf-period を null で消す', () => {
  const buf = fakeBuf(); const prim = fakePrim();
  const a = newActor(buf, prim); a.setEnabled(true); prim.calls.length = 0;
  a.setEnabled(false);
  assert.deepEqual(prim.calls.at(-1), [null, null]);
  assert.equal(a.isEnabled(), false);
});

test('onChunkReady: 先読み完了で現可視レンジを再描画（enabled 時のみ）', () => {
  const buf = fakeBuf(); const prim = fakePrim();
  const a = newActor(buf, prim); a.setEnabled(true);
  buf.cols = [{ time: 120 }]; // 後から埋まった
  prim.calls.length = 0;
  a.onChunkReady();
  assert.deepEqual(prim.calls.at(-1), [[{ time: 120 }], 0.5]);
  // 無効時は再描画しない。
  a.setEnabled(false); prim.calls.length = 0;
  a.onChunkReady();
  assert.equal(prim.calls.length, 0);
});

test('不正レンジ（from>=to / null）は ensure/描画しない', () => {
  const buf = fakeBuf(); const prim = fakePrim();
  const a = new TfPeriodProfileActor({
    jitterBuffer: buf, primitive: prim, getTimeframe: () => '5m', getVisibleRange: () => null,
  });
  a.setEnabled(true);
  assert.equal(buf.ensured.length, 0);
});

// ISSUE-055: candle 透明化の委譲。列が描けたら true・列が無い/無効化で false（初回ちらつき・空白の回避）。
test('renderer 注入時: 列が描けたら candle 透明化 true・列無しは false・無効化で false', () => {
  const buf = fakeBuf(); const prim = fakePrim();
  const rc = { calls: [], setCandleTransparency(on) { this.calls.push(on); } };
  const a = new TfPeriodProfileActor({
    jitterBuffer: buf, primitive: prim, getTimeframe: () => '5m', getVisibleRange: () => ({ from: 100, to: 300 }), renderer: rc,
  });
  // 列がまだ無い状態で有効化 → 描画は空 → 透明化 false（candle 可視のまま列を待つ）。
  a.setEnabled(true);
  assert.equal(rc.calls.at(-1), false, '列が無い間は candle 可視（false）');
  // 列が届いた → onChunkReady で再描画 → 透明化 true。
  buf.cols = [{ time: 150 }];
  a.onChunkReady();
  assert.equal(rc.calls.at(-1), true, '列が描けたら candle 透明化 true');
  // 無効化 → candle 復元（false）。
  a.setEnabled(false);
  assert.equal(rc.calls.at(-1), false, '無効化で candle 復元 false');
});
