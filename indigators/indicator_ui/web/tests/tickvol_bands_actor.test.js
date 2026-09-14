// tickvol_bands_actor.test.js — 取引密度帯アクターの取得・キャッシュ・時間足ゲートの回帰固定。
//
// 因果性（リプレイ）: 帯は「until が属するセッション日より前の N セッション」から作られるため
//   同一セッション日内では応答が不変＝**日内はバーが何本進んでも再取得しない**。
// 時間足: 帯は時間足に依存しない（サーバは常に 1 分足原子・15 分ビンで集計）ため、足を変えても
//   再取得は起きず「塗るバー」だけを引き直す。1 時間足より上は塗らない。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { TickvolBandsActor } from '../js/adapter/front/tickvol_bands_actor.js';
import { sessionDayStart, nextSessionDayStart } from '../js/domain/session_day.js';

const BANDS = [{ startOff: 10800, endOff: 19800 }];
const DAY = sessionDayStart(Date.UTC(2026, 6, 22, 12) / 1000);

// 呼ばれた URL を記録する fake fetch。
function fakeFetch(log, bands = BANDS) {
  return async (url) => {
    log.push(url);
    return { json: async () => ({ ok: true, binSec: 900, sessions: 20, bands, bins: [], threshold: 1 }) };
  };
}

// setRanges を記録する fake primitive と、それを返す fake renderer。
function fakeRenderer(candles) {
  const primitive = { ranges: null, setRanges(r) { this.ranges = r; } };
  return {
    primitive,
    getCandles: () => candles,
    attachBackgroundPrimitive: () => primitive,
  };
}

function makeActor({ candles = [], tf = '15m', until = null, log = [] } = {}) {
  const renderer = fakeRenderer(candles);
  const actor = new TickvolBandsActor({
    fetch: fakeFetch(log),
    datasetRef: 'jp225_tick',
    renderer,
    getTimeframe: () => tf,
    getUntil: () => until,
  });
  return { actor, renderer, log };
}

const barsAt = (offsets) => offsets.map((o) => ({ time: DAY + o }));

// --- 取得と描画 ---------------------------------------------------------------- //
test('enabling fetches the profile and pushes the painted ranges to the background primitive', async () => {
  const { actor, renderer, log } = makeActor({ candles: barsAt([9900, 10800, 11700]), until: DAY + 40000 });
  actor.setParams({ sessions: 20, pct: 75 });
  await actor.setEnabled(true);
  assert.equal(log.length, 1);
  assert.match(log[0], /^\/tickvol_profile\?datasetRef=jp225_tick&sessions=20&pct=75&until=/);
  assert.deepEqual(renderer.primitive.ranges, [{ from: DAY + 10800, to: DAY + 11700 }]);
});

test('disabling clears the bands without fetching again', async () => {
  const { actor, renderer, log } = makeActor({ candles: barsAt([10800]), until: DAY + 40000 });
  await actor.setEnabled(true);
  await actor.setEnabled(false);
  assert.equal(log.length, 1);
  assert.deepEqual(renderer.primitive.ranges, []);
  assert.equal(actor.isEnabled(), false);
});

test('a fetch failure paints nothing instead of reusing a stale profile', async () => {
  const renderer = fakeRenderer(barsAt([10800]));
  const actor = new TickvolBandsActor({
    fetch: async () => { throw new Error('offline'); },
    datasetRef: 'jp225_tick', renderer,
    getTimeframe: () => '15m', getUntil: () => DAY + 40000,
  });
  await actor.setEnabled(true);
  assert.deepEqual(renderer.primitive.ranges, []);
});

// --- 因果性: 再取得はセッション日が変わったときだけ ------------------------------ //
test('advancing the replay clock inside one session day does not refetch', async () => {
  const log = [];
  let until = DAY + 3600;
  const renderer = fakeRenderer(barsAt([10800]));
  const actor = new TickvolBandsActor({
    fetch: fakeFetch(log), datasetRef: 'jp225_tick', renderer,
    getTimeframe: () => '15m', getUntil: () => until,
  });
  await actor.setEnabled(true);
  assert.equal(log.length, 1);
  for (let i = 0; i < 100; i += 1) {   // 同一セッション日内で 100 バー送る（1m 足・計 100 分）
    until += 60;
    await actor.onClock();
  }
  assert.ok(until < nextSessionDayStart(DAY), '前提: 日をまたいでいない');
  assert.equal(log.length, 1, '日内は応答不変＝再取得しない');
});

test('crossing into the next session day refetches with the new until', async () => {
  const log = [];
  let until = DAY + 3600;
  const renderer = fakeRenderer(barsAt([10800]));
  const actor = new TickvolBandsActor({
    fetch: fakeFetch(log), datasetRef: 'jp225_tick', renderer,
    getTimeframe: () => '15m', getUntil: () => until,
  });
  await actor.setEnabled(true);
  until = nextSessionDayStart(DAY) + 60;
  await actor.onClock();
  assert.equal(log.length, 2);
  assert.match(log[1], new RegExp(`until=${until}$`));
});

test('changing the parameters refetches (the profile depends on them)', async () => {
  const { actor, log } = makeActor({ candles: barsAt([10800]), until: DAY + 40000 });
  actor.setParams({ sessions: 20, pct: 75 });
  await actor.setEnabled(true);
  actor.setParams({ sessions: 10, pct: 75 });
  await actor.refresh();
  assert.equal(log.length, 2);
  assert.match(log[1], /sessions=10/);
});

// --- 時間足 -------------------------------------------------------------------- //
test('changing the timeframe repaints without refetching (bands are timeframe independent)', async () => {
  const log = [];
  let tf = '15m';
  // 帯は [10800, 19800)。19350 のバーは 15m ではスロットの 50%（450/900）が帯に入り塗られるが、
  //   1h ではわずか 12.5%（450/3600）＝塗られない＝同じ帯でも表示足で塗る範囲が変わる。
  const renderer = fakeRenderer(barsAt([10800, 19350]));
  const actor = new TickvolBandsActor({
    fetch: fakeFetch(log), datasetRef: 'jp225_tick', renderer,
    getTimeframe: () => tf, getUntil: () => DAY + 40000,
  });
  await actor.setEnabled(true);
  assert.deepEqual(renderer.primitive.ranges, [{ from: DAY + 10800, to: DAY + 19350 }]);
  tf = '1h';
  actor.onTimeframeChange();
  assert.equal(log.length, 1, '時間足では再取得しない');
  assert.deepEqual(renderer.primitive.ranges, [{ from: DAY + 10800, to: DAY + 10800 }]);
});

test('timeframes above 1h paint nothing', async () => {
  const log = [];
  let tf = '15m';
  const renderer = fakeRenderer(barsAt([10800]));
  const actor = new TickvolBandsActor({
    fetch: fakeFetch(log), datasetRef: 'jp225_tick', renderer,
    getTimeframe: () => tf, getUntil: () => DAY + 40000,
  });
  await actor.setEnabled(true);
  assert.equal(renderer.primitive.ranges.length, 1);
  for (const upper of ['4h', '1D', '1W', '1M']) {
    tf = upper;
    actor.onTimeframeChange();
    assert.deepEqual(renderer.primitive.ranges, [], `${upper} は塗らない`);
  }
});

// --- 非干渉 -------------------------------------------------------------------- //
test('a renderer without background-primitive support is left alone (no crash)', async () => {
  const actor = new TickvolBandsActor({
    fetch: fakeFetch([]), datasetRef: 'jp225_tick',
    renderer: { getCandles: () => barsAt([10800]) },   // attachBackgroundPrimitive 非提供
    getTimeframe: () => '15m', getUntil: () => DAY + 40000,
  });
  await actor.setEnabled(true);
  assert.equal(actor.isEnabled(), true);
});

test('live mode (no replay clock) omits the until parameter', async () => {
  const log = [];
  const actor = new TickvolBandsActor({
    fetch: fakeFetch(log), datasetRef: 'jp225_tick', renderer: fakeRenderer([]),
    getTimeframe: () => '15m', getUntil: () => null,
  });
  await actor.setEnabled(true);
  assert.equal(log.length, 1);
  assert.equal(/until=/.test(log[0]), false, 'ライブはサーバの現在時刻に委ねる');
});
