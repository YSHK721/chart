// replay_roles.test.js — setupReplay から切り出したロール 3 種の検証（ISSUE-256）。
//
// 対象: PlaybackTempo（テンポ）/ FormingPlanCache（足内計画）/ FormingAnimator（足内アニメーション）。
// ここで固定するのは「ロールが状態を所有し、注入された依存だけで動く」こと。純ロジック（式）の
// 検証は replay/timing.js・stream.js 側の既存テストが担う（二重に持たない）。
//
// **本番の合成形も通す**（ISSUE-275 の型を作らない）: 合成根はタイマーも時計も注入しないため、
// 既定値のまま構築するケースを必ず 1 つ置く。施行は tools/tests/test_composition_root_arg_parity.py。
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { PlaybackTempo } from '../js/replay/playback_tempo.js';
import { FormingPlanCache } from '../js/replay/forming_plan_cache.js';

function fakeView(over = {}) {
  const texts = {};
  return {
    texts,
    setText: (id, v) => { texts[id] = v; },
    readSpeed: () => 1,
    writeSpeed: () => {},
    readMode: () => 'every_tick',
    ...over,
  };
}

const CANDLES = [
  { time: 100, open: 1, high: 2, low: 0, close: 1 },
  { time: 160, open: 1, high: 2, low: 0, close: 1 },
  { time: 220, open: 1, high: 2, low: 0, close: 1 },
];

// ---- PlaybackTempo ----

test('tempo: ETA は残り足数から出す（完了時は「—」）', () => {
  const view = fakeView();
  const tempo = new PlaybackTempo({
    view, getCandles: () => CANDLES, getBar: () => 0, getTimeframe: () => '1m',
  });

  tempo.setEta();
  assert.match(view.texts['rp-eta'], /残り2足/);

  const atEnd = new PlaybackTempo({
    view, getCandles: () => CANDLES, getBar: () => 2, getTimeframe: () => '1m',
  });
  atEnd.setEta();
  assert.equal(view.texts['rp-eta'], '完了予想 —');
});

test('tempo: 実時間再生は 1 足＝時間足長（アンカからの目標時刻が算出できる）', () => {
  const tempo = new PlaybackTempo({
    view: fakeView({ readSpeed: () => 'realtime' }),
    getCandles: () => CANDLES, getBar: () => 0, getTimeframe: () => '1m',
    now: () => 1000,
  });

  assert.equal(tempo.realtime(), true);
  assert.equal(tempo.rtBarMs(), 60000, '1m は 60,000ms');
  tempo.anchorBarStart(1000);
  assert.equal(tempo.targetAtOffset(250), 1250);
  tempo.reanchorFromOffset(400);
  assert.equal(tempo.targetAtOffset(0), 600, '再開時は now - offset をアンカにする');
});

test('tempo: 速度の軸（比↔実時間）が変わったときだけ計画破棄を通知する', () => {
  let axisChanges = 0;
  let speed = 1;
  const tempo = new PlaybackTempo({
    view: fakeView({ readSpeed: () => speed, writeSpeed: (v) => { speed = v; } }),
    getCandles: () => CANDLES, getBar: () => 0, getTimeframe: () => '1m',
    onSpeedAxisChanged: () => { axisChanges += 1; },
  });

  tempo.applySpeed(0.5);
  assert.equal(axisChanges, 0, '比→比 は軸が変わらない');
  tempo.applySpeed('realtime');
  assert.equal(axisChanges, 1, '比→実時間再生 は軸が変わる＝計画は別物');
});

test('tempo: 本番の合成形（タイマー・時計を注入しない）でフレーム待機が解決する', async () => {
  const tempo = new PlaybackTempo({
    view: fakeView({ readSpeed: () => 1 }),
    getCandles: () => CANDLES, getBar: () => 0, getTimeframe: () => '1m',
  });

  // 既定タイマーで待機し、待機中の再スケジュールを挟んでも必ず解決する（ハングしない）。
  const waited = tempo.waitFrame();
  tempo.rescheduleFrameWait();
  await waited;

  tempo.settleFrameWait(); // 二重解決しても例外にならない（停止時の即解除と同じ経路）
});

// ---- FormingPlanCache ----

function planCache(over = {}) {
  return new FormingPlanCache({
    fetchImpl: async () => ({ json: async () => ({ m1: [], ticks: [], tick_secs: [] }) }),
    datasetRef: 'jp225_tick',
    seqClient: { computeSeq: async () => [] },
    controller: {},          // formingSeqTargets 非対応＝従来経路（署名なし）
    getCandles: () => CANDLES,
    getTimeframe: () => '1m',
    ...over,
  });
}

test('plans: math / open_only は fetch せずに短絡する（窓も取得もしない）', async () => {
  let fetched = 0;
  const plans = planCache({ fetchImpl: async () => { fetched += 1; return { json: async () => ({}) }; } });

  await plans.buildStream(0, 'open_only');
  assert.equal(fetched, 0);
});

test('plans: 先読みは同一 idx を二重発行しない・take はモード不一致を破棄する', async () => {
  let builds = 0;
  const plans = planCache({
    fetchImpl: async () => { builds += 1; return { json: async () => ({ m1: [], ticks: [], tick_secs: [] }) }; },
  });

  plans.prefetch(0, 'every_tick');
  plans.prefetch(0, 'every_tick');   // 在飛行中の二重発行はしない
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(builds, 1);

  assert.equal(plans.take(0, 'real_ticks'), null, 'モードが違えば採用しない');
  assert.ok(plans.take(0, 'every_tick'), '同一モードなら採用する');

  plans.drop(0);
  assert.equal(plans.take(0, 'every_tick'), null, '破棄後は採用しない');
});

test('plans: invalidate は計画と在飛行を捨てる（設定変更＝別物）', async () => {
  const plans = planCache();
  plans.prefetch(0, 'every_tick');
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(plans.keys().length, 1);

  plans.invalidate();
  assert.deepEqual(plans.keys(), []);
});

// ---- FormingAnimator（状態所有の検証。描画本体は既存の replay 統合テストが覆う） ----

test('animator: 停止足の続きと世代は animator が所有する', async () => {
  const { FormingAnimator } = await import('../js/replay/forming_animator.js');
  const animator = new FormingAnimator({
    view: fakeView({ el: () => null, updateForming: () => {} }),
    controller: { isRecomputing: () => false, recomputeFormingLatest: async () => {} },
    getCandles: () => [], getBar: () => 0, getTimeframe: () => '1m',
    tempo: null, plans: planCache(), sleepMs: async () => {},
  });

  assert.equal(animator.pausedForm(), null);
  animator.clearPausedForm();
  assert.equal(animator.pausedForm(), null);

  // supersede は世代を進める（in-flight を破棄させる）。ローソク 0 本なら animate は即 return。
  animator.supersede();
  await animator.animate(() => true, null);
});

// ---- ReplayCursor（再生対象と現在位置の所有者・ISSUE-256） ----

test('cursor: 位置は必ず範囲内へ丸める（旧 clampBar と同一）', async () => {
  const { ReplayCursor } = await import('../js/replay/replay_cursor.js');
  const cursor = new ReplayCursor({ fetchImpl: async () => ({ json: async () => ({}) }), datasetRef: 'x', recentBars: 10, preBars: 3 });
  cursor.setCandles(CANDLES);

  assert.equal(cursor.setBar(99), 2, '末尾を超えたら末尾へ');
  assert.equal(cursor.setBar(-5), 0, '負値は先頭へ');
  assert.equal(cursor.atEnd(), false);
  cursor.setBar(2);
  assert.equal(cursor.atEnd(), true, '末尾＝未来足なし（再生不可）');
  assert.equal(cursor.current().time, 220);
});

test('cursor: 世代は単調増加し、in-flight の破棄判定に使える', async () => {
  const { ReplayCursor } = await import('../js/replay/replay_cursor.js');
  const { isStale } = await import('../js/replay/state.js');
  const cursor = new ReplayCursor({ fetchImpl: async () => ({ json: async () => ({}) }), datasetRef: 'x', recentBars: 10, preBars: 3 });

  const g = cursor.bumpGeneration();
  assert.equal(isStale(g, cursor.generation()), false, '最新の世代は破棄しない');
  cursor.bumpGeneration();
  assert.equal(isStale(g, cursor.generation()), true, '後発が来たら旧世代は破棄する');
});

test('cursor: 期間選択は解除できる／開始位置は時刻から決まる', async () => {
  const { ReplayCursor } = await import('../js/replay/replay_cursor.js');
  const cursor = new ReplayCursor({ fetchImpl: async () => ({ json: async () => ({}) }), datasetRef: 'x', recentBars: 10, preBars: 3 });
  cursor.setCandles(CANDLES);

  cursor.setActivePeriod({ secs: 3600, bars: 60 });
  assert.equal(cursor.activeSecs(), 3600);
  assert.equal(cursor.activePeriodBars(), 60);
  cursor.clearActivePeriod();
  assert.equal(cursor.activeSecs(), null);
  assert.equal(cursor.activePeriodBars(), null);

  assert.equal(cursor.setReplayStartAtTime(160), 1, '時刻 160 の足は index 1');
});

test('cursor: /candles と /available_days の取得（from 指定で pre が付く）', async () => {
  const { ReplayCursor } = await import('../js/replay/replay_cursor.js');
  const urls = [];
  const cursor = new ReplayCursor({
    fetchImpl: async (url) => { urls.push(url); return { json: async () => ({ ok: true, candles: CANDLES, days: ['2026-08-07'] }) }; },
    datasetRef: 'jp225_tick', recentBars: 1500, preBars: 300,
  });

  assert.equal((await cursor.fetchCandles('5m')).length, 3);
  assert.match(urls[0], /timeframe=5m&limit=1500$/, '既定は from/pre を付けない');

  await cursor.fetchCandles('5m', 12345);
  assert.match(urls[1], /&from=12345&pre=300$/, 'カレンダー起点では前方バー数を添える');

  assert.deepEqual(await cursor.fetchDays('5m'), ['2026-08-07']);
});

// ISSUE-291: 計画は controller の申告した計算.時間足をそのままクライアントへ運ぶ。
//   ここで落とすと、サーバは（H 形成足の計算経路を持っていても）チャート足で計算する
//   ＝足内だけリビール値と食い違う（実 UI 実測: 5m×1D EMA5 で 1128 の段差）。
test('plans: 計算.時間足を足内一括計算の要求まで運ぶ', async () => {
  const sent = [];
  const plans = planCache({
    fetchImpl: async () => ({
      json: async () => ({ m1: [{ time: 100, open: 1, high: 2, low: 0, close: 1 }], ticks: [], tick_secs: [] }),
    }),
    // [ISSUE-300] 足内一括計算は 1 要求（specs 配列）へ集約された。計算.時間足は spec ごとに運ぶ。
    seqClient: { computeSeqMulti: async (req) => { sent.push(req); return {}; } },
    controller: {
      formingSeqTargets: () => [
        { instanceId: 'mtf#1', indicatorId: 'moving_averages', variant: 'default',
          params: { length: 9 }, computeTimeframe: '1D' },
        { instanceId: 'chart#1', indicatorId: 'moving_averages', variant: 'default',
          params: { length: 9 }, computeTimeframe: undefined },
      ],
    },
  });

  await plans.build(0, 'ohlc_1min');

  assert.equal(sent.length, 1, `指標ごとに発行している（要求 ${sent.length} 本・1 本であるべき）`);
  assert.deepEqual(sent[0].specs.map((s) => s.computeTimeframe), ['1D', undefined]);
});
