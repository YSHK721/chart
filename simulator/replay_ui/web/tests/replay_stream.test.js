// replay/stream.js — 足内更新ストリーム構築の純ロジック検証（DOM/lwc/fetch 非依存・AAA）。
//
// 参照実装＝プロト web/js/replay.js（buildStream/cap/flattenM1/synthM1/durationSecs/
//   足内窓算出[左右ラベル]）。fetch は副作用として View に残し、fetch 後の純変換
//   （buildStreamFromResponse）と窓算出（intrabarWindow）のみを抽出＝挙動は 1つも足さず/削らず。
//
// ★この時点で web/js/replay/stream.js は未実装（Red）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  cap,
  flattenM1,
  synthM1,
  durationSecs,
  intrabarWindow,
  buildStreamFromResponse,
  ANIM_FINE,
  ANIM_COARSE,
} from '../js/replay/stream.js';
import { sessionDayStart, nextSessionDayStart } from '../js/domain/session_day.js';

// --- cap（極値＋先頭/末尾を必ず保持しつつ n 点へ間引く） ------------------------- //
test('cap returns the same array when length <= n', () => {
  const a = [1, 2, 3];
  assert.equal(cap(a, 5), a); // 同一参照（間引かない）
});
test('cap always preserves first, last, max and min values (no phantom high/low)', () => {
  const arr = [5, 1, 9, 3, 2, 8];
  const out = cap(arr, 3);
  assert.ok(out.includes(9)); // 最高（極値）を捨てない
  assert.ok(out.includes(1)); // 最安（極値）を捨てない
  assert.equal(out[0], 5); // 先頭
  assert.equal(out[out.length - 1], 8); // 末尾
});

// --- flattenM1（各 M1 を O,H,L,C の 4 疑似ティックへ） ---------------------------- //
test('flattenM1 expands each M1 bar into O,H,L,C in order', () => {
  assert.deepEqual(flattenM1([[1, 2, 0, 1.5], [2, 3, 1, 2.5]]), [1, 2, 0, 1.5, 2, 3, 1, 2.5]);
});

// --- synthM1（O→H→L→C を中点補間で多数化） -------------------------------------- //
test('synthM1 interpolates O,mid,H,mid,L,mid,C per M1 bar', () => {
  assert.deepEqual(synthM1([[0, 4, 0, 2]]), [0, 2, 4, 2, 0, 1, 2]);
});

// --- durationSecs（時間足→秒。未知足は 86400 近似） ------------------------------ //
test('durationSecs maps timeframe to seconds and defaults unknown to 86400', () => {
  assert.equal(durationSecs('1m'), 60);
  assert.equal(durationSecs('1D'), 86400);
  assert.equal(durationSecs('zz'), 86400);
});

// --- intrabarWindow（日中＝[t,次足)／1D＝セッション窓（ISSUE-130）／右ラベル(1W,1M)＝[前足+1日, 今足+1日)） --- //
test('intrabarWindow (1D, ISSUE-130) is the SESSION window [sessionDayStart(cd), sessionDayStart(next))', () => {
  // 2026-04-24(金) → 次バー 2026-04-27(月)。月曜バーのセッションは日曜 21:00 UTC 始まり（夏時間）。
  const w = intrabarWindow({ timeframe: '1D', cd: { time: 1777248000 }, prevCandle: { time: 1776988800 }, nextCandle: { time: 1777334400 } });
  assert.deepEqual(w, { winStart: sessionDayStart(1777248000), winEnd: sessionDayStart(1777334400) });
  assert.equal(w.winStart, 1777237200, '月曜バーの窓始端＝日曜 21:00 UTC（日曜夕データは月曜バーに属する）');
});
test('intrabarWindow (1D) with no next uses nextSessionDayStart (DST-safe)', () => {
  const w = intrabarWindow({ timeframe: '1D', cd: { time: 1777248000 }, prevCandle: null, nextCandle: null });
  assert.deepEqual(w, { winStart: sessionDayStart(1777248000), winEnd: nextSessionDayStart(1777248000) });
});
test('intrabarWindow (left-labeled intraday 15m) is [cd.time, next.time)', () => {
  const w = intrabarWindow({ timeframe: '15m', cd: { time: 1000 }, prevCandle: null, nextCandle: { time: 1900 } });
  assert.deepEqual(w, { winStart: 1000, winEnd: 1900 });
});
test('intrabarWindow (left-labeled intraday 15m) with no next uses cd.time + durationSecs', () => {
  const w = intrabarWindow({ timeframe: '15m', cd: { time: 1000 }, prevCandle: null, nextCandle: null });
  assert.deepEqual(w, { winStart: 1000, winEnd: 1000 + 900 });
});
test('intrabarWindow (right-labeled 1W) is [prev.time+DAY, cd.time+DAY)', () => {
  const w = intrabarWindow({ timeframe: '1W', cd: { time: 5000000 }, prevCandle: { time: 4000000 }, nextCandle: null });
  assert.deepEqual(w, { winStart: 4000000 + 86400, winEnd: 5000000 + 86400 });
});
test('intrabarWindow (right-labeled 1M) with no prev uses (cd.time-duration)+DAY', () => {
  const w = intrabarWindow({ timeframe: '1M', cd: { time: 5000000 }, prevCandle: null, nextCandle: null });
  assert.deepEqual(w, { winStart: (5000000 - 2592000) + 86400, winEnd: 5000000 + 86400 });
});

// --- buildStreamFromResponse（fetch 後の 5 モード点列） --------------------------- //
const CD = { time: 100, open: 10, high: 15, low: 8, close: 12 };

test('buildStreamFromResponse open_only yields the open price only', () => {
  const r = buildStreamFromResponse({ mode: 'open_only', cd: CD, m1: [], ticks: [] });
  assert.deepEqual(r.prices, [10]);
});
test('buildStreamFromResponse math yields the close price only', () => {
  const r = buildStreamFromResponse({ mode: 'math', cd: CD, m1: [], ticks: [] });
  assert.deepEqual(r.prices, [12]);
});
test('buildStreamFromResponse real_ticks returns ALL ticks uncapped (contact-scan absolute spec)', () => {
  const ticks = Array.from({ length: ANIM_FINE + 500 }, (_, i) => 10 + (i % 3)); // > ANIM_FINE
  const r = buildStreamFromResponse({ mode: 'real_ticks', cd: CD, m1: [], ticks });
  assert.equal(r.prices.length, ticks.length); // cap しない＝全件
});
test('buildStreamFromResponse real_ticks falls back to capped flattenM1 when no ticks', () => {
  const m1 = Array.from({ length: 1000 }, () => [1, 2, 0, 1]); // flatten=4000 点
  const r = buildStreamFromResponse({ mode: 'real_ticks', cd: CD, m1, ticks: [] });
  // cap は n の strided に加え 極値/両端(0,last,iMax,iMin) を必ず残すため最大 n+3。
  assert.ok(r.prices.length <= ANIM_FINE + 3);
  assert.ok(r.prices.length < 4000); // 間引かれている
});
test('buildStreamFromResponse real_ticks with no ticks and no m1 falls back to close', () => {
  const r = buildStreamFromResponse({ mode: 'real_ticks', cd: CD, m1: [], ticks: [] });
  assert.deepEqual(r.prices, [12]);
});
test('buildStreamFromResponse ohlc_1min caps flattenM1 to ANIM_COARSE', () => {
  const m1 = Array.from({ length: 1000 }, () => [1, 2, 0, 1]);
  const r = buildStreamFromResponse({ mode: 'ohlc_1min', cd: CD, m1, ticks: [] });
  assert.ok(r.prices.length <= ANIM_COARSE + 3); // cap は極値/両端で最大 n+3
  assert.ok(r.prices.length < 4000);
});
test('buildStreamFromResponse ohlc_1min with no m1 falls back to day OHLC 4 points', () => {
  const r = buildStreamFromResponse({ mode: 'ohlc_1min', cd: CD, m1: [], ticks: [] });
  assert.deepEqual(r.prices, [10, 15, 8, 12]);
});
test('buildStreamFromResponse every_tick synthesizes M1 capped to ANIM_FINE', () => {
  const m1 = Array.from({ length: 1000 }, () => [0, 4, 0, 2]);
  const r = buildStreamFromResponse({ mode: 'every_tick', cd: CD, m1, ticks: [] });
  assert.ok(r.prices.length <= ANIM_FINE + 3); // cap は極値/両端で最大 n+3
  assert.ok(r.prices.length > 4); // 補間で多数化
});
test('buildStreamFromResponse every_tick with no m1 falls back to OHLC 4 points', () => {
  const r = buildStreamFromResponse({ mode: 'every_tick', cd: CD, m1: [], ticks: [] });
  assert.deepEqual(r.prices, [10, 15, 8, 12]);
});
