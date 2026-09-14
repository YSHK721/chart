// timeline_player.js（usecase/timeline_player.js）— 再生状態機械の純ロジック検証。
//
// 設計入力（plan §新設（純ロジック・usecase）/ §テスト）:
//   再生状態機械。frameIndex / playing / fpsCap / bounds、indexForTime / timeForIndex
//   （二分探索・往復一致）、clampIndex、stepNext / stepPrev、delayMs(fpsCap)。
//   タイマー非保持。throttle 判定は持たない（毎フレーム再計算が絶対条件）。
//
// DOM/lwc/timer/fetch 非依存（純ロジック・node:test 対象）。AAA 構造。
//
// ★この時点で web/js/usecase/timeline_player.js は未実装（Red）。import 解決失敗 or
//   関数未定義により失敗することを確認する（実装は後続 programmer-executor 担当）。
//
// 引き渡し契約（programmer-executor への想定シグネチャ）:
//   - createTimelinePlayer({ times, fpsCap=? }) -> player   ※times は昇順 UNIX 秒配列
//       player: { frameIndex, playing, fpsCap, bounds: {min,max},
//                 indexForTime(t), timeForIndex(i), clampIndex(i),
//                 stepNext(), stepPrev(), setPlaying(bool) }
//   - delayMs(fpsCap) -> number（モジュール関数。fpsCap=2 → 500 等）
//   いずれも純関数（外部状態・timer 非依存）。frameIndex 等は更新後の値を返す。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  createTimelinePlayer,
  delayMs,
} from '../js/usecase/timeline_player.js';

// 昇順 UNIX 秒（日足相当・厳密境界の検証に十分な少数本）。
const TIMES = [100, 200, 300, 400, 500];

function newPlayer(over = {}) {
  return createTimelinePlayer({ times: TIMES, fpsCap: 2, ...over });
}

// --------------------------------------------------------------------------- //
// indexForTime / timeForIndex — 二分探索・往復一致・境界
// --------------------------------------------------------------------------- //
test('timeForIndex returns the time at the given index (exact mapping)', () => {
  const p = newPlayer();
  assert.equal(p.timeForIndex(0), 100);
  assert.equal(p.timeForIndex(4), 500);
});

test('indexForTime is the inverse of timeForIndex for existing times (round-trip)', () => {
  const p = newPlayer();
  for (let i = 0; i < TIMES.length; i += 1) {
    const t = p.timeForIndex(i);
    assert.equal(p.indexForTime(t), i); // 往復一致
  }
});

test('indexForTime maps the first time to index 0 (lower boundary)', () => {
  const p = newPlayer();
  assert.equal(p.indexForTime(100), 0);
});

test('indexForTime maps the last time to the last index (upper boundary)', () => {
  const p = newPlayer();
  assert.equal(p.indexForTime(500), TIMES.length - 1);
});

test('indexForTime resolves a non-existent in-between time to the nearest preceding bar (causal floor)', () => {
  // 非存在 time → 近傍規約: 直近確定足（floor＝t 以下で最大の足）の index。
  //   因果再現（その時点で知り得た情報）と整合する floor 規約。
  const p = newPlayer();
  assert.equal(p.indexForTime(250), 1); // 200 と 300 の間 → 200（index 1）
});

test('indexForTime clamps a time before the first bar to index 0', () => {
  const p = newPlayer();
  assert.equal(p.indexForTime(50), 0); // 先頭 time より前 → 先頭へクランプ
});

test('indexForTime clamps a time after the last bar to the last index', () => {
  const p = newPlayer();
  assert.equal(p.indexForTime(9999), TIMES.length - 1); // 末尾より後 → 末尾へ
});

// --------------------------------------------------------------------------- //
// clampIndex / bounds
// --------------------------------------------------------------------------- //
test('bounds exposes the valid index range [0, length-1]', () => {
  const p = newPlayer();
  assert.deepEqual(p.bounds, { min: 0, max: TIMES.length - 1 });
});

test('clampIndex clamps below-range to min and above-range to max', () => {
  const p = newPlayer();
  assert.equal(p.clampIndex(-5), 0);
  assert.equal(p.clampIndex(99), TIMES.length - 1);
  assert.equal(p.clampIndex(2), 2); // 範囲内は据え置き
});

// --------------------------------------------------------------------------- //
// stepNext / stepPrev — 境界クランプ（範囲外に出ない）
// --------------------------------------------------------------------------- //
test('stepNext advances frameIndex by one within bounds', () => {
  const p = newPlayer();
  // 先頭からの前進: 0 -> 1
  assert.equal(p.frameIndex, 0);
  p.stepNext();
  assert.equal(p.frameIndex, 1);
});

test('stepNext at the last frame does not exceed the upper bound', () => {
  const p = newPlayer();
  // 末尾へ移動してから next（範囲外に出ない）。
  for (let i = 0; i < TIMES.length; i += 1) p.stepNext();
  assert.equal(p.frameIndex, TIMES.length - 1); // 末尾でクランプ
});

test('stepPrev at frame 0 does not go below the lower bound', () => {
  const p = newPlayer();
  assert.equal(p.frameIndex, 0);
  p.stepPrev();
  assert.equal(p.frameIndex, 0); // 0 で prev しても負にならない
});

test('stepPrev decrements frameIndex by one within bounds', () => {
  const p = newPlayer();
  p.stepNext();
  p.stepNext();
  assert.equal(p.frameIndex, 2);
  p.stepPrev();
  assert.equal(p.frameIndex, 1);
});

// --------------------------------------------------------------------------- //
// delayMs(fpsCap) — fps 上限 → フレーム間ディレイの換算（契約明示）
// --------------------------------------------------------------------------- //
test('delayMs converts fpsCap to per-frame delay in milliseconds (fpsCap=2 -> 500ms)', () => {
  // 契約: delayMs = 1000 / fpsCap（fps 上限の逆数 * 1000）。
  assert.equal(delayMs(2), 500);
  assert.equal(delayMs(1), 1000);
  assert.equal(delayMs(4), 250);
});

// --------------------------------------------------------------------------- //
// playing トグル等の純遷移
// --------------------------------------------------------------------------- //
test('player starts not playing and setPlaying toggles the playing flag', () => {
  const p = newPlayer();
  assert.equal(p.playing, false); // 既定は停止
  p.setPlaying(true);
  assert.equal(p.playing, true);
  p.setPlaying(false);
  assert.equal(p.playing, false);
});

test('fpsCap is exposed on the player and reflects the constructor value', () => {
  const p = newPlayer({ fpsCap: 4 });
  assert.equal(p.fpsCap, 4);
});
