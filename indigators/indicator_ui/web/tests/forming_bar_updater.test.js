// forming_bar_updater.js（FormingBarUpdater・形成中バーの5秒ライブ更新）の仕様検証。
//
// 設計入力: 最新足の足内更新（served のみ・既定 5000ms）。LiveUpdater とは別系統で、
//   - tick: controller.isRecomputing() が true ならスキップ。false なら /forming_bar 取得 →
//     bar があれば renderer.updateLastCandle(bar)。**インジ再計算しない**（負荷分離）。
//   - bar=null（対象外 tf / ティック無し）は無視（updateLastCandle を呼ばない）。
//   - start()/stop()/多重start防止・競合ガードは controller.isRecomputing() 権威。
// 構造: AAA。実タイマー・実ネット・実 DOM 非依存（全注入）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { FormingBarUpdater } from '../js/adapter/front/forming_bar_updater.js';

function fakeTimers() {
  const intervals = new Map();
  let nextId = 1;
  const setIntervalFake = (fn, ms) => { const id = nextId++; intervals.set(id, { fn, ms }); return id; };
  const clearIntervalFake = (id) => { intervals.delete(id); };
  const tick = async () => {
    const last = [...intervals.values()].at(-1);
    if (last) { await last.fn(); }
  };
  return { setIntervalFake, clearIntervalFake, intervals, tick };
}

function spies({ recomputing = false, bar = { time: 100, open: 1, high: 2, low: 0, close: 1.5, volume: 9 } } = {}) {
  const calls = { recompute: 0, recomputeOpts: null, loadForming: [], updateLast: [] };
  const controller = {
    isRecomputing: () => recomputing,
    // 指標の最新点再計算（mode:'latest'）。呼び出し回数と引数を記録する。
    recomputeAllApplied: async (opts) => { calls.recompute += 1; calls.recomputeOpts = opts; },
  };
  const renderer = { updateLastCandle: (c) => calls.updateLast.push(c) };
  const loadFormingBar = async (ref, tf) => { calls.loadForming.push([ref, tf]); return bar; };
  return { calls, controller, renderer, loadFormingBar, bar };
}

function newUpdater(overrides = {}, sp = spies()) {
  const t = fakeTimers();
  const updater = new FormingBarUpdater({
    controller: sp.controller,
    renderer: sp.renderer,
    loadFormingBar: sp.loadFormingBar,
    datasetRef: 'jp225_tick',
    getTimeframe: () => '1D',
    setInterval: t.setIntervalFake,
    clearInterval: t.clearIntervalFake,
    intervalMs: 5000,
    ...overrides,
  });
  return { updater, t, sp };
}

test('start: each tick updates last candle AND recomputes indicators latest (tick-driven)', async () => {
  const { updater, t, sp } = newUpdater();
  updater.start();
  await t.tick();
  assert.deepEqual(sp.calls.loadForming.at(-1), ['jp225_tick', '1D']);
  assert.equal(sp.calls.updateLast.length, 1);
  assert.deepEqual(sp.calls.updateLast[0], sp.bar);
  assert.equal(sp.calls.recompute, 1);                       // 指標も最新点を再計算。
  assert.deepEqual(sp.calls.recomputeOpts, { mode: 'latest' }); // mode=latest（頻度分離・最新点のみ）。
});

test('start registers exactly one interval at the configured intervalMs (5000)', () => {
  const { updater, t } = newUpdater();
  updater.start();
  assert.equal(t.intervals.size, 1);
  assert.equal([...t.intervals.values()][0].ms, 5000);
});

test('stop clears the interval so ticks no longer fire', async () => {
  const { updater, t, sp } = newUpdater();
  updater.start();
  updater.stop();
  assert.equal(t.intervals.size, 0);
  await t.tick();
  assert.equal(sp.calls.loadForming.length, 0);
});

test('calling start twice does not register a second interval (multi-start guard)', () => {
  const { updater, t } = newUpdater();
  updater.start();
  updater.start();
  assert.equal(t.intervals.size, 1);
});

test('tick is skipped while controller.isRecomputing() is true (no fetch/update)', async () => {
  const sp = spies({ recomputing: true });
  const { updater, t } = newUpdater({}, sp);
  updater.start();
  await t.tick();
  assert.equal(sp.calls.loadForming.length, 0);
  assert.equal(sp.calls.updateLast.length, 0);
});

test('bar=null (no forming bar) is a full no-op (no candle update, no recompute)', async () => {
  const sp = spies({ bar: null });
  const { updater, t } = newUpdater({}, sp);
  updater.start();
  await t.tick();
  assert.equal(sp.calls.loadForming.length, 1); // 取得は試みる
  assert.equal(sp.calls.updateLast.length, 0);   // が価格は更新しない
  assert.equal(sp.calls.recompute, 0);           // 指標も再計算しない（材料なし＝完全 no-op）
});

// suppressPriceUpdate（ISSUE-049）: LiveTickPlayer が価格の唯一の書き手になるとき、FormingBarUpdater は
//   価格の巻き戻し（12 秒より古いデータでの updateLastCandle）を止める。指標の最新点再計算は従来どおり。
test('suppressPriceUpdate=true skips updateLastCandle but still recomputes indicators (latest)', async () => {
  const sp = spies();
  const { updater, t } = newUpdater({ suppressPriceUpdate: true }, sp);
  updater.start();
  await t.tick();
  assert.equal(sp.calls.loadForming.length, 1);  // 形成中バー取得は行う（指標材料）
  assert.equal(sp.calls.updateLast.length, 0);   // が価格は書かない（player が唯一の書き手）
  assert.equal(sp.calls.recompute, 1);           // 指標の最新点再計算は従来どおり
  assert.deepEqual(sp.calls.recomputeOpts, { mode: 'latest' });
});

test('suppressPriceUpdate default (unset) preserves existing behavior (updateLastCandle called)', async () => {
  const sp = spies();
  const { updater, t } = newUpdater({}, sp); // 既定 false（byte 不変）
  updater.start();
  await t.tick();
  assert.equal(sp.calls.updateLast.length, 1);   // 従来どおり価格を更新
  assert.deepEqual(sp.calls.updateLast[0], sp.bar);
});

// suppressPriceUpdate が関数のとき、tick ごとに評価する（1W/1M は player 非対応＝FormingBarUpdater が
//   価格の書き手になるため、tf に応じて抑止可否を切り替える配線を支える）。
test('suppressPriceUpdate as a function is evaluated each tick (draw when it returns false)', async () => {
  let suppress = true;
  const sp = spies();
  const { updater, t } = newUpdater({ suppressPriceUpdate: () => suppress }, sp);
  updater.start();
  await t.tick();
  assert.equal(sp.calls.updateLast.length, 0);   // 関数が true → 価格を書かない
  assert.equal(sp.calls.recompute, 1);           // 指標は従来どおり再計算
  suppress = false;
  await t.tick();
  assert.equal(sp.calls.updateLast.length, 1);   // 関数が false → 価格を書く
});
