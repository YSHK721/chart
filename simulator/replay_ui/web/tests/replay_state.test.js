// replay/state.js — 再生状態機械の純ロジック検証（DOM/lwc/fetch/timer 非依存・AAA）。
//
// 参照実装＝プロト web/js/replay.js。抽出対象（挙動を 1つも足さず/削らず）:
//   - bar クランプ / idxForTime（二分探索・time>=target の最小 index＝ceil 規約）
//   - applyView 可視範囲 [bar-幅, bar+RIGHT_MARGIN]（followOn/activePeriodBars/全期間null）
//   - scrollViewTo 範囲（左端=[0,width] / 右端=[to-width,to]）
//   - renderPresets の replayStart/activePeriodBars 算出（present−期間分）
//   - boundaryTimeValue（replayStart>0 かつ candle 有 → time、他 null）
//   - syncModeOptions の縮退モード集合（1m は ohlc_1min/every_tick）
//   - 停止足の続き再開判定 / generation 破棄判定 / animGen supersede 判定
//
// ★この時点で web/js/replay/state.js は未実装（Red）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  clampBar,
  idxForTime,
  visibleRange,
  scrollRange,
  presetSelection,
  boundaryTimeValue,
  degenerateModes,
  resumeDecision,
  isStale,
  isSuperseded,
  RIGHT_MARGIN,
  FOLLOW_BARS,
} from '../js/replay/state.js';

const TIMES = [100, 200, 300, 400, 500];
const candlesOf = (times) => times.map((t) => ({ time: t, open: t, high: t + 1, low: t - 1, close: t }));

// --- clampBar ------------------------------------------------------------------- //
test('clampBar clamps into [0, length-1]', () => {
  assert.equal(clampBar(-5, 5), 0);
  assert.equal(clampBar(99, 5), 4);
  assert.equal(clampBar(3, 5), 3);
});

// --- idxForTime（ceil: time>=target の最小 index。replay.js 準拠＝floor でない） --- //
test('idxForTime returns smallest index whose time >= target (ceil, not floor)', () => {
  const c = candlesOf(TIMES);
  assert.equal(idxForTime(c, 250), 2); // 200,300 の間 → 300（index2）＝ceil
  assert.equal(idxForTime(c, 200), 1); // 完全一致
  assert.equal(idxForTime(c, 100), 0); // 下限
  assert.equal(idxForTime(c, 50), 0); // 先頭より前 → 0
  assert.equal(idxForTime(c, 9999), 4); // 末尾より後 → 末尾
});

// --- visibleRange（applyView） -------------------------------------------------- //
test('visibleRange follows FOLLOW_BARS window when followOn', () => {
  const r = visibleRange({ bar: 200, followOn: true, activePeriodBars: null });
  assert.deepEqual(r, { from: 200 - FOLLOW_BARS, to: 200 + RIGHT_MARGIN });
});
test('visibleRange starts at logical 0 when width is null (全期間)', () => {
  const r = visibleRange({ bar: 40, followOn: false, activePeriodBars: null });
  assert.deepEqual(r, { from: 0, to: 40 + RIGHT_MARGIN });
});
test('visibleRange clamps from at 0 when bar-width is negative', () => {
  const r = visibleRange({ bar: 10, followOn: false, activePeriodBars: 30 });
  assert.deepEqual(r, { from: 0, to: 10 + RIGHT_MARGIN });
});

// --- scrollRange（scrollViewTo：現在ズーム幅を維持） ----------------------------- //
test('scrollRange left keeps width and snaps to logical 0', () => {
  const r = scrollRange({ edge: 'left', currentRange: { from: 100, to: 150 }, bar: 200 });
  assert.deepEqual(r, { from: 0, to: 50 });
});
test('scrollRange right keeps width and snaps to bar+RIGHT_MARGIN', () => {
  const r = scrollRange({ edge: 'right', currentRange: { from: 100, to: 150 }, bar: 200 });
  const to = 200 + RIGHT_MARGIN;
  assert.deepEqual(r, { from: to - 50, to });
});

// --- presetSelection（renderPresets onclick） ----------------------------------- //
test('presetSelection for 全期間 (secs=null) resets to start-of-history', () => {
  const c = candlesOf(TIMES);
  assert.deepEqual(presetSelection({ candles: c, secs: null }), { replayStart: 0, activePeriodBars: null });
});
test('presetSelection computes replayStart=present-secs and width=present-replayStart', () => {
  const c = candlesOf(TIMES); // present=index4, time=500
  const sel = presetSelection({ candles: c, secs: 200 }); // 500-200=300 → idx2
  assert.deepEqual(sel, { replayStart: 2, activePeriodBars: 2 }); // width=4-2
});

// --- boundaryTimeValue（減光境界） ---------------------------------------------- //
test('boundaryTimeValue is null at 全期間 (replayStart=0)', () => {
  assert.equal(boundaryTimeValue({ replayStart: 0, candles: candlesOf(TIMES) }), null);
});
test('boundaryTimeValue returns candle time at replayStart', () => {
  assert.equal(boundaryTimeValue({ replayStart: 2, candles: candlesOf(TIMES) }), 300);
});
test('boundaryTimeValue is null when replayStart is out of range', () => {
  assert.equal(boundaryTimeValue({ replayStart: 99, candles: candlesOf(TIMES) }), null);
});

// --- degenerateModes（syncModeOptions） ----------------------------------------- //
test('degenerateModes hides ohlc_1min/every_tick on 1m only', () => {
  const d = degenerateModes('1m');
  assert.ok(d.has('ohlc_1min') && d.has('every_tick'));
  assert.equal(d.size, 2);
  assert.equal(degenerateModes('1D').size, 0);
});

// --- resumeDecision（停止足の続き再開） ----------------------------------------- //
test('resumeDecision resumes only when pausedForm.time equals current candle time', () => {
  assert.equal(resumeDecision({ time: 200 }, { time: 200 }), true);
  assert.equal(resumeDecision({ time: 200 }, { time: 300 }), false);
  assert.equal(resumeDecision(null, { time: 200 }), false);
  assert.equal(resumeDecision({ time: 200 }, undefined), false);
});

// --- generation / animGen 破棄・置換判定 ---------------------------------------- //
test('isStale discards a render whose generation was superseded', () => {
  assert.equal(isStale(1, 2), true);
  assert.equal(isStale(2, 2), false);
});
test('isSuperseded aborts a forming animation replaced by a newer animGen', () => {
  assert.equal(isSuperseded(1, 2), true);
  assert.equal(isSuperseded(2, 2), false);
});
