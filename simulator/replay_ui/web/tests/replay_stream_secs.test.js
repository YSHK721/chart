// replay/stream.js — sec 並行配列（MP tick-live 用）の後方互換拡張検証（DOM/fetch 非依存・AAA）。
//
// MP tick-live は DwellAccumulator.addTick(sec, mid) に sec を要す。buildStreamFromResponse は
//   既存 prices（挙動の正解）を 1つも変えず、real_ticks の実ティック経路のみ secs（tick_secs）を
//   並行で返す。他分岐は secs:[]（当バー MP skip・base 継続）。prices は全分岐で従来と完全一致。
//
// ★この時点で buildStreamFromResponse は secs を扱わない（Red）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { buildStreamFromResponse, ANIM_FINE, ANIM_COARSE } from '../js/replay/stream.js';

const CD = { time: 1000, open: 10, high: 20, low: 5, close: 15 };

// --- real_ticks: 実ティック経路のみ secs を並走（同順・同長） ---
test('real_ticks returns secs parallel to the uncapped prices when tick_secs provided', () => {
  // Arrange
  const ticks = [11, 12, 13];
  const secs = [1010, 1020, 1030];
  // Act
  const out = buildStreamFromResponse({ mode: 'real_ticks', cd: CD, ticks, secs });
  // Assert: prices は従来どおり全件（不変）＋secs 並走。
  assert.deepEqual(out.prices, [11, 12, 13]);
  assert.deepEqual(out.secs, [1010, 1020, 1030]);
});

test('real_ticks with ticks but no secs yields secs:[] (this bar skips MP, base continues)', () => {
  // Arrange: MP 無効バー（secs 未付与）でも prices は不変。
  const out = buildStreamFromResponse({ mode: 'real_ticks', cd: CD, ticks: [11, 12] });
  // Assert
  assert.deepEqual(out.prices, [11, 12]);
  assert.deepEqual(out.secs, []);
});

test('real_ticks fallback to M1 (no ticks) yields secs:[]', () => {
  // Arrange: 実ティック無→M1 代替経路。secs は無い（MP は当バー skip）。
  const out = buildStreamFromResponse({ mode: 'real_ticks', cd: CD, m1: [[10, 20, 5, 15]], secs: [] });
  // Assert: prices は従来どおり（capped flattenM1）＋secs:[]。
  assert.ok(Array.isArray(out.prices) && out.prices.length > 0);
  assert.deepEqual(out.secs, []);
});

// --- 他モードは secs:[]（prices 不変） ---
test('non real_ticks modes always yield secs:[] and unchanged prices', () => {
  assert.deepEqual(buildStreamFromResponse({ mode: 'open_only', cd: CD }).secs, []);
  assert.deepEqual(buildStreamFromResponse({ mode: 'math', cd: CD }).secs, []);
  assert.deepEqual(buildStreamFromResponse({ mode: 'ohlc_1min', cd: CD, m1: [[10, 20, 5, 15]] }).secs, []);
  assert.deepEqual(buildStreamFromResponse({ mode: 'every_tick', cd: CD, m1: [[10, 20, 5, 15]] }).secs, []);
  // prices 不変（従来 return と一致）。
  assert.deepEqual(buildStreamFromResponse({ mode: 'open_only', cd: CD }).prices, [10]);
  assert.deepEqual(buildStreamFromResponse({ mode: 'math', cd: CD }).prices, [15]);
});

// --- 回帰: secs を一切渡さない従来呼び出しでも prices は完全不変 ---
test('regression: omitting secs keeps prices identical to legacy behavior for all modes', () => {
  assert.deepEqual(buildStreamFromResponse({ mode: 'real_ticks', cd: CD, ticks: [11, 12, 13] }).prices, [11, 12, 13]);
  assert.deepEqual(buildStreamFromResponse({ mode: 'real_ticks', cd: CD }).prices, [15]); // 足内データ無→終値
  assert.equal(typeof ANIM_FINE, 'number');
  assert.equal(typeof ANIM_COARSE, 'number');
});
