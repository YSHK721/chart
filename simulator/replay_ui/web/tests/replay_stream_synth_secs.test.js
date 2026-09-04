// replay/stream.js — 合成 dwell secs（every_tick / ohlc_1min）の窓等分生成を検証（DOM/fetch 非依存・AAA）。
//
// MP tick-live 足内成長を「最新足更新」の全モードへ拡張する（#mp-growth-all-modes）。
//   every_tick（synthM1）/ ohlc_1min（flattenM1）の点列に対し、cap 後の N 点へ窓 [winStart, winEnd) を
//   等分したタイムスタンプ secs[i] = winStart + (winEnd-winStart)*i/(N-1) を並走生成する
//   （DwellAccumulator が隣接差分で dwell 化＝総 dwell=窓時間）。
//   real_ticks は実 tick_secs のまま（byte 不変・窓は無視）。open_only/math は secs:[] 据置。
//   prices は全分岐で従来と完全一致（挙動の正解＝既存 return）を不変に保つ（回帰）。
//
// ★この時点で buildStreamFromResponse は winStart/winEnd を無視し every_tick/ohlc_1min の secs は [] のまま（Red）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  buildStreamFromResponse, cap, synthM1, flattenM1, ANIM_FINE, ANIM_COARSE,
} from '../js/replay/stream.js';

const CD = { time: 200, open: 10, high: 20, low: 5, close: 15 };
const M1 = [[10, 20, 5, 15], [15, 25, 12, 18]];
// 任意の足内窓（buildStreamFromResponse は与えられた winStart/winEnd を等分するのみ＝窓導出は
//   intrabarWindow の責務。ISSUE-130 以降 1D はセッション窓だが本テストの検証対象は等分規則）。
const WIN = { winStart: 200, winEnd: 86600 };

function assertEquidistantWindow(secs, winStart, winEnd) {
  assert.ok(secs.length >= 2, 'secs は 2 点以上');
  assert.equal(secs[0], winStart, 'secs[0] は winStart');
  assert.equal(secs[secs.length - 1], winEnd, 'secs[last] は winEnd（settle が winEnd へ収束する基点）');
  const d0 = secs[1] - secs[0];
  assert.ok(d0 > 0, '狭義単調増加');
  for (let i = 1; i < secs.length; i++) {
    const d = secs[i] - secs[i - 1];
    assert.ok(d > 0, `secs[${i}] は増加`);
    assert.ok(Math.abs(d - d0) < 1e-6, '窓等分（隣接差分は一定）');
    // 因果性: 窓外（未来）を含めない。
    assert.ok(secs[i] >= winStart && secs[i] <= winEnd, `secs[${i}] は [winStart, winEnd] 内`);
  }
}

// --- every_tick: synthM1 点へ合成 dwell secs を並走 ---
test('every_tick attaches window-equidistant synth secs parallel to synthM1 prices (prices unchanged)', () => {
  const out = buildStreamFromResponse({ mode: 'every_tick', cd: CD, m1: M1, ...WIN });
  const expectedPrices = cap(synthM1(M1), ANIM_FINE);
  assert.deepEqual(out.prices, expectedPrices, 'prices は従来（cap(synthM1, ANIM_FINE)）と完全一致');
  assert.equal(out.secs.length, out.prices.length, 'secs は prices と同長（並走）');
  assertEquidistantWindow(out.secs, WIN.winStart, WIN.winEnd);
});

// --- ohlc_1min: flattenM1 点へ合成 dwell secs を並走 ---
test('ohlc_1min attaches window-equidistant synth secs parallel to flattenM1 prices (prices unchanged)', () => {
  const out = buildStreamFromResponse({ mode: 'ohlc_1min', cd: CD, m1: M1, ...WIN });
  const expectedPrices = cap(flattenM1(M1), ANIM_COARSE);
  assert.deepEqual(out.prices, expectedPrices, 'prices は従来（cap(flattenM1, ANIM_COARSE)）と完全一致');
  assert.equal(out.secs.length, out.prices.length, 'secs は prices と同長（並走）');
  assertEquidistantWindow(out.secs, WIN.winStart, WIN.winEnd);
});

// --- every_tick M1 無フォールバック（日足 OHLC 4 点）にも合成 secs を並走 ---
test('every_tick fallback (no M1) still attaches synth secs for the 4 OHLC points', () => {
  const out = buildStreamFromResponse({ mode: 'every_tick', cd: CD, m1: [], ...WIN });
  assert.deepEqual(out.prices, [10, 20, 5, 15], 'prices は従来（OHLC4点）と一致');
  assert.equal(out.secs.length, 4);
  assertEquidistantWindow(out.secs, WIN.winStart, WIN.winEnd);
});

// --- real_ticks は byte 不変（窓を無視し実 tick_secs をそのまま並走） ---
test('real_ticks ignores the window and keeps real tick_secs byte-identical', () => {
  const out = buildStreamFromResponse({ mode: 'real_ticks', cd: CD, ticks: [11, 12, 13], secs: [210, 220, 230], ...WIN });
  assert.deepEqual(out.prices, [11, 12, 13], 'prices 不変');
  assert.deepEqual(out.secs, [210, 220, 230], 'real_ticks は合成せず実 tick_secs のまま（byte 不変）');
});

// --- open_only / math は窓を渡しても secs:[]（据置） ---
test('open_only and math keep secs:[] even when a window is provided', () => {
  assert.deepEqual(buildStreamFromResponse({ mode: 'open_only', cd: CD, ...WIN }).secs, []);
  assert.deepEqual(buildStreamFromResponse({ mode: 'open_only', cd: CD, ...WIN }).prices, [10]);
  assert.deepEqual(buildStreamFromResponse({ mode: 'math', cd: CD, ...WIN }).secs, []);
  assert.deepEqual(buildStreamFromResponse({ mode: 'math', cd: CD, ...WIN }).prices, [15]);
});

// --- 後方互換: 窓を渡さない従来呼び出しは every_tick/ohlc_1min とも secs:[]（prices 不変） ---
test('regression: without a window every_tick/ohlc_1min still yield secs:[] and unchanged prices', () => {
  const et = buildStreamFromResponse({ mode: 'every_tick', cd: CD, m1: M1 });
  assert.deepEqual(et.secs, []);
  assert.deepEqual(et.prices, cap(synthM1(M1), ANIM_FINE));
  const oc = buildStreamFromResponse({ mode: 'ohlc_1min', cd: CD, m1: M1 });
  assert.deepEqual(oc.secs, []);
  assert.deepEqual(oc.prices, cap(flattenM1(M1), ANIM_COARSE));
});
