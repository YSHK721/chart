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

// --- ISSUE-044: real_ticks（cap 廃止＝間引かない・絶対仕様）の ETA -------------------- //
//   参照実装（プロト replay.js）は cap 廃止時に ETA モデル（animBaseMs=ANIM_FINE 前提）を更新して
//   おらず「cap 廃止後の正しい ETA」の定義が無い（月足×実ティックで 53 秒 vs 実測が桁違いに乖離）。
//   依頼者承認（2026-07-06・バックエンド拡張で正確化）に基づく拡張:
//   残り足の実 tick 総数（/candles tickvol）×ステップ間隔 + 足あたり固定費(compute+足送り)。
import { etaRealTicksMs, remainingTickvol } from '../js/replay/timing.js';

test('etaRealTicksMs = 実tick総数×stepMs(s) + 残り足数×(compute + BASE_FRAME_MS/effSpeed)', () => {
  // s=1: stepMs=6ms・足送り 50ms・compute null→50ms（estimatePeriodMs と同じ既定）
  assert.equal(etaRealTicksMs(100000, 11, null, 1), 100000 * 6 + 11 * (50 + 50));
  // 実測 compute（lastComputeMs）優先
  assert.equal(etaRealTicksMs(1000, 2, 100, 1), 1000 * 6 + 2 * (100 + 50));
  // s=0.5: stepMs=round(6/0.5)=12・足送り 50/0.5=100
  assert.equal(etaRealTicksMs(1000, 1, null, 0.5), 1000 * 12 + 1 * (50 + 100));
});

test('remainingTickvol sums tickvol of bars AFTER current, null when any bar lacks it (model fallback)', () => {
  const cs = [{ tickvol: 5 }, { tickvol: 7 }, { tickvol: 9 }];
  assert.equal(remainingTickvol(cs, 0), 16); // 現在足は含めない（remain と同一範囲）
  assert.equal(remainingTickvol(cs, 1), 9);
  assert.equal(remainingTickvol(cs, 2), 0);  // 残り 0 足
  assert.equal(remainingTickvol([{ tickvol: 5 }, {}, { tickvol: 9 }], 0), null); // 欠損→モデルへフォールバック
  assert.equal(remainingTickvol([{ tickvol: 5 }, { tickvol: NaN }], 0), null);   // 非有限→フォールバック
});

// --- ISSUE-044 追補（依頼者承認 2026-07-06）: 長時間 ETA の時間単位表示 -------------------- //
//   real_ticks ETA 正確化で完了予想が数時間規模になり得る（月足×実ティック≒28時間）。
//   参照実装 fmtEta は分までだが「1687分00秒」は規模把握が困難のため、60 分以上は「H時間MM分」へ。
test('fmtEta formats >=1 hour as H時間MM分 (approved extension; minutes/seconds unchanged below)', () => {
  assert.equal(fmtEta(3600 * 1000), '1時間00分');
  assert.equal(fmtEta(101220 * 1000), '28時間07分'); // 月足×実ティック実測規模
  assert.equal(fmtEta(59 * 1000), '59秒');           // 既存挙動不変
  assert.equal(fmtEta(90 * 1000), '1分30秒');        // 既存挙動不変
  assert.equal(fmtEta(3599 * 1000), '59分59秒');     // 分ブランチ最大値（境界固定・review 🟡）
  assert.equal(fmtEta(3690 * 1000), '1時間01分');    // 剰余秒（30秒）切り捨ての明示検証（review 🟡）
});
