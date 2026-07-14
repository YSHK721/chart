// tf_period_profile_actor.js の検証（可視レンジ契機の ensure＋描画・onReady 再描画・enabled）。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { TfPeriodProfileActor } from '../js/adapter/front/tf_period_profile_actor.js';

function fakeBuf() {
  return {
    ensured: [], cols: [], u: 0.5, ready: true, // ready=true: 可視範囲が揃っている（一括描画）。
    ensure(tf, from, to) { this.ensured.push([tf, from, to]); },
    allReady() { return this.ready; },
    getColumns(from, to) { return this.cols.filter((c) => c.time >= from && c.time <= to); },
    unit() { return this.u; },
  };
}
function fakePrim() { return { calls: [], setTfPeriods(cols, unit) { this.calls.push([cols, unit]); } }; }

// 制御可能な setTimeout（fired[] に (fn, ms) を積み、flush() で発火）。
function fakeTimers() {
  const t = { fired: [], _id: 0 };
  t.set = (fn, ms) => { t.fired.push({ id: ++t._id, fn, ms, live: true }); return t._id; };
  t.clear = (id) => { const e = t.fired.find((x) => x.id === id); if (e) e.live = false; };
  t.flush = () => { for (const e of t.fired) if (e.live) { e.live = false; e.fn(); } };
  return t;
}

function newActor(buf, prim, range = { from: 100, to: 300 }, tf = '5m', timers = null) {
  return new TfPeriodProfileActor({
    jitterBuffer: buf, primitive: prim, getTimeframe: () => tf, getVisibleRange: () => range,
    ...(timers ? { setTimeoutFn: timers.set, clearTimeoutFn: timers.clear } : {}),
  });
}

test('setEnabled(true)→refresh: 揃っていれば ensure(可視レンジ)＋一括描画（ISSUE-069）', () => {
  const buf = fakeBuf(); buf.ready = true; buf.cols = [{ time: 150 }, { time: 250 }, { time: 999 }];
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

test('揃うまで保留→onChunkReady で揃ったら一括描画（逐次描画しない・ISSUE-069）', () => {
  const buf = fakeBuf(); buf.ready = false; buf.cols = [{ time: 120 }]; // まだ揃っていない
  const prim = fakePrim();
  const timers = fakeTimers();
  const a = newActor(buf, prim, { from: 100, to: 300 }, '5m', timers);
  a.setEnabled(true);
  assert.equal(prim.calls.length, 0, '揃うまで描画しない（保留）');
  assert.equal(timers.fired.filter((t) => t.live).length, 1, '上限タイムアウトを張る');
  // まだ揃っていない onChunkReady → 描画しない
  a.onChunkReady();
  assert.equal(prim.calls.length, 0, '未 ready の onChunkReady は描画しない');
  // 揃った → onChunkReady で一括描画＋タイムアウト解除
  buf.ready = true; buf.cols = [{ time: 120 }, { time: 250 }];
  a.onChunkReady();
  assert.deepEqual(prim.calls.at(-1), [[{ time: 120 }, { time: 250 }], 0.5]);
  assert.equal(timers.fired.filter((t) => t.live).length, 0, '揃ったらタイムアウト解除');
});

test('上限タイムアウトで揃わなくても現時点 ready 分を描画（フォールバック・ISSUE-069）', () => {
  const buf = fakeBuf(); buf.ready = false; buf.cols = [{ time: 150 }];
  const prim = fakePrim();
  const timers = fakeTimers();
  const a = newActor(buf, prim, { from: 100, to: 300 }, '5m', timers);
  a.setEnabled(true);
  assert.equal(prim.calls.length, 0, '揃うまで保留');
  timers.flush(); // 上限到達
  assert.deepEqual(prim.calls.at(-1), [[{ time: 150 }], 0.5], 'タイムアウトで部分描画');
});

test('onChunkReady: 保留が無ければ（一括描画済み）再描画しない・無効時も no-op', () => {
  const buf = fakeBuf(); buf.ready = true; buf.cols = [{ time: 120 }];
  const prim = fakePrim();
  const a = newActor(buf, prim, { from: 100, to: 300 });
  a.setEnabled(true);            // 揃い済み → 一括描画（pending なし）
  prim.calls.length = 0;
  a.onChunkReady();
  assert.equal(prim.calls.length, 0, '保留無しの onChunkReady は逐次再描画しない');
  a.setEnabled(false); prim.calls.length = 0;
  a.onChunkReady();
  assert.equal(prim.calls.length, 0, '無効時は no-op');
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
  // 列がまだ揃わない状態で有効化 → 保留（描画しない）＝透明化も呼ばない（candle 可視のまま列を待つ）。
  buf.ready = false;
  a.setEnabled(true);
  assert.equal(rc.calls.length, 0, '揃うまで描画しない＝透明化も呼ばない（candle 可視のまま）');
  // 列が揃った → onChunkReady で一括描画 → 透明化 true。
  buf.ready = true; buf.cols = [{ time: 150 }];
  a.onChunkReady();
  assert.equal(rc.calls.at(-1), true, '列が描けたら candle 透明化 true');
  // 無効化 → candle 復元（false）。
  a.setEnabled(false);
  assert.equal(rc.calls.at(-1), false, '無効化で candle 復元 false');
});

// ---------------------------------------------------------------------------
// 方向注釈（依頼者指示 2026-07-13「どの時間足にも背景色(不透明度95%)で上下が分かる」）:
//   _render が getCandles の同 time candle から dirUp（陽=true/陰=false/不在=null）を列へ付与する。
// ---------------------------------------------------------------------------

test('_render は candle 突合で dirUp を列へ注釈する（陽/陰/不在null・未注入は注釈なし）', () => {
  const buf = fakeBuf(); buf.ready = true;
  buf.cols = [{ time: 120 }, { time: 180 }, { time: 240 }];
  const prim = fakePrim();
  const candles = [
    { time: 120, open: 100, close: 105 }, // 陽
    { time: 180, open: 105, close: 101 }, // 陰
    // time 240 の candle 無し → dirUp=null
  ];
  const a = new TfPeriodProfileActor({
    jitterBuffer: buf, primitive: prim, getTimeframe: () => '5m',
    getVisibleRange: () => ({ from: 100, to: 300 }), getCandles: () => candles,
  });
  a.setEnabled(true);
  const cols = prim.calls.at(-1)[0];
  assert.deepEqual(cols.map((c) => c.dirUp), [true, false, null]);
  // getCandles 未注入は注釈しない（後方互換）。
  const prim2 = fakePrim();
  const b = newActor(buf, prim2);
  b.setEnabled(true);
  assert.equal('dirUp' in prim2.calls.at(-1)[0][0], false, '未注入は dirUp を付けない');
});
