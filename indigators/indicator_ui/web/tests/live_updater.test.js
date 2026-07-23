// live_updater.js（LiveUpdater・60 秒ライブ更新）の仕様検証。
//
// 設計入力: チャート 1 分間隔ライブ更新（served のみ）。
//   - start()/stop() で setInterval/clearInterval を制御（intervalMs 既定 60000）。
//   - 多重 start 防止（稼働中の再 start で二重に setInterval しない）。
//   - tick: controller.isRecomputing() が true ならスキップ。false なら /candles 再取得 →
//     新確定足（末尾バー time の前進）を検知したときのみ full 再計算（統一設計 2026-07-22:
//     全再計算はバー確定時のみ。足内は tick 粒度の末尾差分が担う）→ updateLastCandle(最新足)。
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
  const calls = { recompute: 0, recomputeMode: null, loadCandles: [], updateLast: [] };
  const controller = {
    isRecomputing: () => recomputing,
    // ISSUE-151: バー確定検知は requestFullRecompute（coalesce/pending 付き）要求へ変更。
    requestFullRecompute: () => { calls.recompute += 1; calls.recomputeMode = 'full'; },
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

test('start: tick refetches candles and updates the last candle; full recompute only on NEW bar', async () => {
  // Arrange
  const { updater, t, sp } = newUpdater();
  // Act: 初回 tick は baseline 取り＝full 再計算しない（統一設計: 全再計算はバー確定時のみ）。
  updater.start();
  await t.tick();
  assert.equal(sp.calls.recompute, 0);
  assert.deepEqual(sp.calls.loadCandles.at(-1), ['jp225_m1', '1D']);
  assert.equal(sp.calls.updateLast.length, 1);
  assert.deepEqual(sp.calls.updateLast[0], sp.candles.at(-1)); // 最新足
  // Act: 同じ末尾バーのままの tick → full 再計算しない。
  await t.tick();
  assert.equal(sp.calls.recompute, 0);
  // Act: 新確定足（末尾バー time 前進）→ full 再計算 1 回。
  sp.candles.push({ time: 3, open: 2.5, high: 4, low: 2, close: 3.5 });
  await t.tick();
  assert.equal(sp.calls.recompute, 1);
  assert.equal(sp.calls.recomputeMode, 'full');
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

test('while isRecomputing: bar-close detection still runs (starvation-proof), only price part skips', async () => {
  // ISSUE-151 追補: 検知は再計算中でも実行（requestFullRecompute は pending 必達）。価格反映のみスキップ。
  const sp = spies({ recomputing: true });
  const { updater, t } = newUpdater({}, sp);
  updater.start();
  await t.tick();                                  // baseline（検知は動く）
  assert.equal(sp.calls.loadCandles.length, 1);    // candles 取得は行う
  assert.equal(sp.calls.updateLast.length, 0);     // 価格は書かない（混線防止）
  assert.equal(sp.calls.recompute, 0);
  sp.candles.push({ time: 3, open: 2.5, high: 4, low: 2, close: 3.5 });
  await t.tick();                                  // 新確定足 → 再計算中でも full 要求が積まれる
  assert.equal(sp.calls.recompute, 1);
});

// suppressPriceUpdate（ISSUE-049）: LiveTickPlayer が価格の唯一の書き手になるとき、LiveUpdater は
//   価格の巻き戻し（12 秒より古い candles 末尾での updateLastCandle）を止める。再計算は従来どおり。
test('suppressPriceUpdate=true skips updateLastCandle (price writer is the player)', async () => {
  const sp = spies();
  const { updater, t } = newUpdater({ suppressPriceUpdate: true }, sp);
  updater.start();
  await t.tick();
  assert.equal(sp.calls.updateLast.length, 0);   // 価格は書かない（player が唯一の書き手）
  // 統一設計: 初回 tick は baseline 取り＝full 再計算なし。新バー到来時のみ full。
  assert.equal(sp.calls.recompute, 0);
  sp.candles.push({ time: 3, open: 2.5, high: 4, low: 2, close: 3.5 });
  await t.tick();
  assert.equal(sp.calls.recompute, 1);
});

test('suppressPriceUpdate default (unset) preserves existing behavior (updateLastCandle called)', async () => {
  const sp = spies();
  const { updater, t } = newUpdater({}, sp); // 既定 false（byte 不変）
  updater.start();
  await t.tick();
  assert.equal(sp.calls.updateLast.length, 1);
  assert.deepEqual(sp.calls.updateLast[0], sp.candles.at(-1));
});

// 統一設計（2026-07-22）: 全再計算はバー確定時のみ（requestFullRecompute 要求・ISSUE-151）。
test('full recompute request fires only when the last bar time advances (bar close)', async () => {
  const captured = [];
  const candles = [{ time: 1, open: 1, high: 2, low: 0, close: 1.5 }];
  const controller = {
    isRecomputing: () => false,
    requestFullRecompute: () => { captured.push('full'); },
  };
  const renderer = { updateLastCandle: () => {} };
  const loadCandles = async () => candles;
  const sp = { controller, renderer, loadCandles, candles };
  const { updater, t } = newUpdater({}, sp);
  updater.start();
  await t.tick();            // baseline
  await t.tick();            // 同一バー → full なし
  assert.equal(captured.length, 0);
  candles.push({ time: 2, open: 1.5, high: 3, low: 1, close: 2.5 });  // 新確定足
  await t.tick();
  assert.equal(captured.length, 1);
});

// ライブ欠落補完（ISSUE-106）: tick は取得 candles を renderer.resyncMissedCandles へ渡し、
//   休止中に取りこぼした確定足の補完を renderer に委ねる。再同期実施時（true）は末尾も反映済み
//   のため updateLastCandle を重ねない。suppressPriceUpdate でも補完は実施する（抑止対象は
//   現在足の価格上書きのみ＝過去確定足の補完は player の書き手責務と競合しない）。
function resyncSpies({ resyncResult } = {}) {
  const sp = spies();
  sp.calls.resync = [];
  sp.renderer.resyncMissedCandles = (candles) => {
    sp.calls.resync.push(candles);
    return resyncResult;
  };
  return sp;
}

test('tick: 取得 candles 全件を resyncMissedCandles へ渡す（欠落補完の起点・ISSUE-106 回帰）', async () => {
  const sp = resyncSpies({ resyncResult: false });
  const { updater, t } = newUpdater({}, sp);
  updater.start();
  await t.tick();
  assert.equal(sp.calls.resync.length, 1);
  assert.deepEqual(sp.calls.resync[0], sp.candles); // 末尾 1 本ではなく全件を渡す
});

test('tick: 再同期実施時（true）は updateLastCandle を重ねない（二重反映防止）', async () => {
  const sp = resyncSpies({ resyncResult: true });
  const { updater, t } = newUpdater({}, sp);
  updater.start();
  await t.tick();
  assert.equal(sp.calls.updateLast.length, 0);
});

test('tick: 再同期不要（false）なら従来どおり最新足を updateLastCandle（挙動不変）', async () => {
  const sp = resyncSpies({ resyncResult: false });
  const { updater, t } = newUpdater({}, sp);
  updater.start();
  await t.tick();
  assert.equal(sp.calls.updateLast.length, 1);
  assert.deepEqual(sp.calls.updateLast[0], sp.candles.at(-1));
});

test('tick: suppressPriceUpdate=true でも resyncMissedCandles は実施される（補完は抑止対象外）', async () => {
  const sp = resyncSpies({ resyncResult: true });
  const { updater, t } = newUpdater({ suppressPriceUpdate: true }, sp);
  updater.start();
  await t.tick();
  assert.equal(sp.calls.resync.length, 1);       // 補完は実施
  assert.equal(sp.calls.updateLast.length, 0);   // 価格の差分上書きは従来どおり抑止
});

test('tick: renderer が resyncMissedCandles 未実装でも従来経路で動く（後方互換）', async () => {
  const sp = spies(); // renderer は updateLastCandle のみ
  const { updater, t } = newUpdater({}, sp);
  updater.start();
  await t.tick();
  assert.equal(sp.calls.updateLast.length, 1);
  assert.deepEqual(sp.calls.updateLast[0], sp.candles.at(-1));
});
