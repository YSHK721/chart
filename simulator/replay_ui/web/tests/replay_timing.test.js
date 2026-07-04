// replay/timing.js — 再生テンポ純ロジックの検証（DOM/lwc/timer/fetch 非依存・node:test・AAA）。
//
// 参照実装＝プロト web/js/replay.js（挙動の正解定義）。以下は replay.js の値/式を
//   1つも足さず/削らず抽出したもの:
//     - MIN_SPEED=0.05 / BASE_FRAME_MS=50 / PER_POINT_MS=6 / ANIM_FINE=800 / ANIM_COARSE=200
//     - speed()=Number.isFinite? min(1,max(0,v)) : 1   → clampSpeed
//     - effSpeed()=max(MIN_SPEED, s)
//     - frameMs()=BASE_FRAME_MS/effSpeed
//     - baseStepMs=max(ANIM_MIN_MS,PER_POINT_MS); step=round(baseStepMs/effSpeed) → stepMs
//     - animBaseMs(mode) / estimatePeriodMs() / emaPeriodMs EMA / fmtEta()
//
// ★この時点で web/js/replay/timing.js は未実装（Red）。import 解決失敗で失敗する。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  clampSpeed,
  effSpeed,
  frameMs,
  stepMs,
  animBaseMs,
  estimatePeriodMs,
  emaUpdate,
  periodMs,
  fmtEta,
} from '../js/replay/timing.js';

// --- clampSpeed（0 は falsy でも 0 のまま＝一時停止。NaN/非有限のみ 1） ------------ //
test('clampSpeed keeps 0 as 0 (pause) and does not turn it into 1', () => {
  assert.equal(clampSpeed(0), 0); // ← `||1` バグ（0で停止しない）を禁止
});
test('clampSpeed clamps into [0,1] and defaults non-finite to 1', () => {
  assert.equal(clampSpeed(0.5), 0.5);
  assert.equal(clampSpeed(2), 1);
  assert.equal(clampSpeed(-1), 0);
  assert.equal(clampSpeed(NaN), 1);
});

// --- effSpeed（0 除算/Infinity 回避のため MIN_SPEED でクランプ） ------------------ //
test('effSpeed clamps to MIN_SPEED (0.05) at speed 0 to avoid Infinity', () => {
  assert.equal(effSpeed(0), 0.05);
  assert.equal(effSpeed(0.5), 0.5);
  assert.equal(effSpeed(1), 1);
});

// --- frameMs = BASE_FRAME_MS / effSpeed ------------------------------------------ //
test('frameMs is BASE_FRAME_MS(50)/effSpeed', () => {
  assert.equal(frameMs(1), 50);
  assert.equal(frameMs(0.5), 100);
  assert.equal(frameMs(0), 1000); // 50/0.05
});

// --- stepMs = round(max(ANIM_MIN_MS,PER_POINT_MS)/effSpeed) ----------------------- //
test('stepMs is round(baseStepMs=6 / effSpeed)', () => {
  assert.equal(stepMs(1), 6);
  assert.equal(stepMs(0.5), 12);
  assert.equal(stepMs(0), 120); // round(6/0.05)
});

// --- animBaseMs（想定点数×PER_POINT_MS） ----------------------------------------- //
test('animBaseMs reflects point-count density per mode', () => {
  assert.equal(animBaseMs('math'), 0);
  assert.equal(animBaseMs('open_only'), 6); // 1点×6
  assert.equal(animBaseMs('ohlc_1min'), 1200); // 200×6
  assert.equal(animBaseMs('real_ticks'), 4800); // 800×6
  assert.equal(animBaseMs('every_tick'), 4800); // 既定＝ANIM_FINE
});

// --- estimatePeriodMs = (lastComputeMs??50) + (animBaseMs+BASE_FRAME_MS)/effSpeed - //
test('estimatePeriodMs uses 50 for null lastComputeMs and adds anim+frame over effSpeed', () => {
  assert.equal(estimatePeriodMs(null, 'math', 1), 100); // 50 + (0+50)/1
  assert.equal(estimatePeriodMs(null, 'real_ticks', 1), 4900); // 50 + (4800+50)/1
  assert.equal(estimatePeriodMs(30, 'open_only', 0.5), 142); // 30 + (6+50)/0.5
});

// --- emaUpdate（実測EMA平滑 prev*0.7 + dt*0.3、null は dt） ------------------------ //
test('emaUpdate seeds with dt on null and smooths 0.7/0.3 afterwards', () => {
  assert.equal(emaUpdate(null, 100), 100);
  assert.equal(emaUpdate(100, 200), 130); // 70 + 60
});

// --- periodMs（実測優先・無ければモデル推定） ------------------------------------- //
test('periodMs prefers measured EMA and falls back to model estimate when null', () => {
  assert.equal(periodMs(200, null, 'math', 1), 200); // 実測優先
  assert.equal(periodMs(null, null, 'math', 1), 100); // モデル推定へ
});

// --- fmtEta（0/非有限は「—」、秒/分秒フォーマット） ------------------------------- //
test('fmtEta returns dash for non-positive/non-finite and formats seconds/minutes', () => {
  assert.equal(fmtEta(0), '—');
  assert.equal(fmtEta(-5), '—');
  assert.equal(fmtEta(Infinity), '—');
  assert.equal(fmtEta(5000), '5秒');
  assert.equal(fmtEta(65000), '1分05秒'); // ゼロ埋め
  assert.equal(fmtEta(125000), '2分05秒');
});
