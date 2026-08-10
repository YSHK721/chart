// replay_timeframe_applier.test.js — 時間足切替が「リプレイ単一経路」で行われることの回帰固定。
//
// ISSUE-231（実測 2026-08-01・リプレイモードの時間足切替）:
//   時間足ボタンには共有ベース bind() が張るライブ経路（controller.setTimeframe）が既に結線されて
//   いるのに、replay.js も同じ [data-timeframe] へ独自リスナ（setTimeout 60ms → loadTimeframe）を
//   追加していた。結果、1 クリックで 2 経路が走り
//     1) ライブ経路が先着してローソクだけ先に差し替え（指標は空 → compute 完了後に描画＝実測 359ms 遅延）
//     2) 約 750ms 後にリプレイ経路が同じ切替をもう一度実行（全再計算の二重実行）
//   となっていた。リプレイの不変条件は「その時点 T のローソクと指標が同時に現れる」であり、1) は違反。
//   恒久対策として replay.js は独自リスナを張らず、controller の反映役スロット
//   （setTimeframeApplier）へ loadTimeframe を登録する＝切替はリプレイの単一経路だけを通る。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { setupReplay } from '../js/replay.js';
import { fakeChart, fakeEl } from './_fakes.js';

// --- fake DOM（replay_eta_wiring.test.js と同型・最小） ---
// 時間足ボタン（共有 TimeframeMenu の項目相当）を持つ document。
function fakeDoc(tfBtns) {
  const els = { 'rp-speed': fakeEl({ value: '1' }), 'rp-mode': fakeEl({ value: 'math' }) };
  return {
    getElementById: (id) => (els[id] || (els[id] = fakeEl())),
    querySelectorAll: (sel) => (sel === '[data-timeframe]' ? tfBtns : []),
    createElement: () => fakeEl(),
    addEventListener() {},
    _els: els,
  };
}

// 反映役スロットを持つ controller（共有ベース IndicatorController の該当面のみ）。
function fakeController(calls) {
  return {
    _timeframe: '5m', _recentBars: 0, _applier: null,
    setUntilTime() {}, isRecomputing() { return false; },
    setTimeframeApplier(fn) { this._applier = fn; calls.push(['setApplier', typeof fn]); },
    async recomputeAllApplied({ preRender } = {}) { if (preRender) preRender(); },
    async recomputeFormingLatest() {},
  };
}

const CANDLES = [
  { time: 100, open: 1, high: 2, low: 0.5, close: 1.5 },
  { time: 200, open: 1.5, high: 2.5, low: 1, close: 2 },
];

async function boot() {
  globalThis.window = globalThis.window || {};
  const calls = [];
  const btn = fakeEl({ dataset: { timeframe: '15m' } });
  const doc = fakeDoc([btn]);
  const controller = fakeController(calls);
  const fetched = [];
  const handle = await setupReplay({
    chart: fakeChart(),
    mainSeries: { attachPrimitive() {}, update() {} },
    controller,
    renderer: { setCandles() {} },
    datasetRef: 'jp225_tick',
    recentBars: 1500,
    document: doc,
    fetchImpl: async (url) => {
      fetched.push(String(url));
      return { ok: true, async json() { return { ok: true, candles: CANDLES, days: [] }; } };
    },
    marketProfile: null,
  });
  return { handle, controller, calls, btn, fetched };
}

test('setupReplay は時間足ボタンへ独自リスナを張らない（ライブ経路との二重実行を作らない・ISSUE-231）', async () => {
  const { btn } = await boot();
  assert.deepEqual(btn._l.click ?? [], [], '[data-timeframe] へ click リスナが追加されている（二重経路の再発）');
});

test('setupReplay は controller へ反映役を登録する（切替はリプレイ単一経路・ISSUE-231）', async () => {
  const { controller } = await boot();
  assert.equal(typeof controller._applier, 'function', '反映役が登録されていない（ライブ経路のローソク先行が残る）');
});

test('反映役の実行はローソクと指標を同一バッチ（preRender 付き recomputeAllApplied）で描く（ISSUE-231）', async () => {
  const { controller } = await boot();
  const order = [];
  controller.recomputeAllApplied = async ({ preRender } = {}) => {
    order.push('recompute-start');
    if (preRender) { preRender(); order.push('preRender'); }
  };
  await controller._applier('15m');
  assert.ok(order.includes('preRender'), 'ローソク差替え（preRender）が再計算バッチの内側で行われていない');
  assert.equal(order[0], 'recompute-start');
});

test('disable/enable は反映役を解除／再登録する（ライブは既定経路のまま・ISSUE-231）', async () => {
  const { handle, controller } = await boot();
  await handle.disable();
  assert.equal(controller._applier, null, 'ライブ復帰時に反映役が残っている（ライブがリプレイ経路を通る）');
  await handle.enable();
  assert.equal(typeof controller._applier, 'function', 'リプレイ復帰時に反映役が再登録されていない');
});
