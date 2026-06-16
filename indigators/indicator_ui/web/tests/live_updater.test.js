// live_updater.js（LiveUpdater・60 秒ライブ更新）の仕様検証。
//
// 設計入力: チャート 1 分間隔ライブ更新（served のみ）。
//   - start()/stop() で setInterval/clearInterval を制御（intervalMs 既定 60000）。
//   - 多重 start 防止（稼働中の再 start で二重に setInterval しない）。
//   - tick: controller.isRecomputing() が true ならスキップ。false なら
//     controller 経由の再計算 → /candles 再取得 → renderer.updateLastCandle(最新足)。
//   - 競合ガードは controller.isRecomputing() の単一権威（LiveUpdater 独自フラグなし）。
// 構造: Arrange-Act-Assert（AAA）。実タイマー・実ネット・実 DOM 非依存（全注入）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { LiveUpdater } from '../js/adapter/front/live_updater.js';

// fake timer: setInterval が登録したコールバックを捕捉し、手動 tick で決定論的に駆動する。
function fakeTimers() {
  const intervals = new Map(); // id -> { fn, ms }
  let nextId = 1;
  const setIntervalFake = (fn, ms) => { const id = nextId++; intervals.set(id, { fn, ms }); return id; };
  const clearIntervalFake = (id) => { intervals.delete(id); };
  // 直近に登録された interval のコールバックを 1 回呼ぶ（tick 相当）。
  const tick = async () => {
    const last = [...intervals.values()].at(-1);
    if (last) { await last.fn(); }
  };
  return { setIntervalFake, clearIntervalFake, intervals, tick };
}

// recompute / candles 取得 / updateLastCandle の呼び出しを記録する spy 一式。
function spies({ recomputing = false } = {}) {
  const calls = { recompute: 0, loadCandles: [], updateLast: [] };
  const controller = {
    isRecomputing: () => recomputing,
    recomputeAllApplied: async () => { calls.recompute += 1; },
  };
  const renderer = { updateLastCandle: (c) => calls.updateLast.push(c) };
  const candles = [
    { time: 1, open: 1, high: 2, low: 0, close: 1.5 },
    { time: 2, open: 1.5, high: 3, low: 1, close: 2.5 },
  ];
  const loadCandles = async (ref, tf) => { calls.loadCandles.push([ref, tf]); return candles; };
  return { calls, controller, renderer, loadCandles, candles };
}

function newUpdater(overrides = {}, sp = spies()) {
  const t = fakeTimers();
  const updater = new LiveUpdater({
    controller: sp.controller,
    renderer: sp.renderer,
    loadCandles: sp.loadCandles,
    datasetRef: 'jp225_m1',
    getTimeframe: () => '1D',
    setInterval: t.setIntervalFake,
    clearInterval: t.clearIntervalFake,
    intervalMs: 60000,
    ...overrides,
  });
  return { updater, t, sp };
}

test('start: each tick recomputes, refetches candles, and updates the last candle', async () => {
  // Arrange
  const { updater, t, sp } = newUpdater();
  // Act
  updater.start();
  await t.tick();
  // Assert: 再計算 1 回・candles 再取得（現 datasetRef/timeframe）・最新足を updateLastCandle へ。
  assert.equal(sp.calls.recompute, 1);
  assert.deepEqual(sp.calls.loadCandles.at(-1), ['jp225_m1', '1D']);
  assert.equal(sp.calls.updateLast.length, 1);
  assert.deepEqual(sp.calls.updateLast[0], sp.candles.at(-1)); // 最新足
});

test('start registers exactly one interval at the configured intervalMs', () => {
  const { updater, t } = newUpdater();
  updater.start();
  assert.equal(t.intervals.size, 1);
  assert.equal([...t.intervals.values()][0].ms, 60000);
});

test('stop clears the interval so ticks no longer fire', async () => {
  const { updater, t, sp } = newUpdater();
  updater.start();
  updater.stop();
  assert.equal(t.intervals.size, 0);
  await t.tick(); // 登録解除済み → 何も起きない
  assert.equal(sp.calls.recompute, 0);
});

test('calling start twice does not register a second interval (multi-start guard)', () => {
  const { updater, t } = newUpdater();
  updater.start();
  updater.start();
  // 二重に setInterval しない（稼働中の start は無視）。
  assert.equal(t.intervals.size, 1);
});

test('tick is skipped while controller.isRecomputing() is true (no recompute/fetch)', async () => {
  // Arrange: 再計算中の controller を注入（独自フラグではなく controller 権威を参照）。
  const sp = spies({ recomputing: true });
  const { updater, t } = newUpdater({}, sp);
  // Act
  updater.start();
  await t.tick();
  // Assert: 再計算も candles 取得も updateLastCandle も走らない。
  assert.equal(sp.calls.recompute, 0);
  assert.equal(sp.calls.loadCandles.length, 0);
  assert.equal(sp.calls.updateLast.length, 0);
});

// Latest 増分計算: tick は recomputeAllApplied({mode:'latest'}) を呼ぶ（末尾K差分反映）。
test('tick recomputes with mode "latest" (Latest incremental compute)', async () => {
  // Arrange: recomputeAllApplied の引数を捕捉する controller を注入。
  const captured = [];
  const controller = {
    isRecomputing: () => false,
    recomputeAllApplied: async (opts) => { captured.push(opts); },
  };
  const renderer = { updateLastCandle: () => {} };
  const loadCandles = async () => [{ time: 1, open: 1, high: 2, low: 0, close: 1.5 }];
  const sp = { controller, renderer, loadCandles, candles: [] };
  const { updater, t } = newUpdater({}, sp);
  // Act
  updater.start();
  await t.tick();
  // Assert: ライブ tick は latest モードで再計算する。
  assert.deepEqual(captured.at(-1), { mode: 'latest' });
});
