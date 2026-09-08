// replay_plan_generation_abort.test.js — 世代トークンによる旧要求の打ち切り（ISSUE-285）。
//
// 事象（実測 2026-08-08）: モード遷移時、リプレイ層の /intraday 要求がライブ core へ回り 404
//   （PREFETCH_DEPTH=3 の先読みぶん 3 連続）。先読みゲート（playing || wasEnabled）は定常状態しか
//   守れず、**遷移の瞬間に残る in-flight/遅延要求**は生き続けていた。
//
// 本テストが固定する契約（「無効になった要求は必ず死ぬ」構造）:
//   1. invalidate（停止・モード切替・窓替え）で、旧世代の in-flight /intraday は AbortController で
//      打ち切られる。
//   2. 旧世代の遅延要求（/intraday 完了後に続く /compute 一括計算）は**発行されない**。
//   3. 旧世代の計画は invalidate 後のキャッシュへ入らない（陳腐化した値の再流入なし）。
//   4. in-flight の /compute（一括計算）にも打ち切り信号が配線され、invalidate で発火する。
//   5. 計算量テスト（規約 2026-08-28）: 打ち切り後の新規発行は 0。
//      「発行した要求 − （使用した要求 + 打ち切った要求） = 0」を、先読み深さ・窓長を変えた
//      2 点以上で固定する（入力を増やしても旧世代の発行は増えない＝浪費の不在をオーダーで表明）。
//   6. 統合（setupReplay 経由）: disable() が計画の in-flight を打ち切り、以後の発行が 0 になる。
//
// 404 の握り潰し（catch で黙らせる）は応急処置であり、ここでは検査しない。検査するのは
//   「要求そのものが発行されない／中断される」ことである。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { FormingPlanCache } from '../js/replay/forming_plan_cache.js';
import { setupReplay } from '../js/replay.js';
import { fakeChart, fakeEl } from './_fakes.js';

const settle = () => new Promise((r) => setTimeout(r, 20));

// ---- 単体ハーネス（FormingPlanCache 直・発行を Test Spy で数える） ----
function makeHarness({ candleCount = 5, computeHangs = false } = {}) {
  const candles = Array.from({ length: candleCount }, (_, i) => (
    { time: 100 + i * 300, open: 10, high: 12, low: 9, close: 11 }
  ));
  const issued = [];   // 発行した /intraday（Test Spy）: { url, signal, resolved }
  const gates = [];    // 手動解決（in-flight を任意の時点まで保つ）
  const fetchImpl = (url, opts = {}) => {
    const rec = { url: String(url), signal: opts && opts.signal, resolved: false };
    issued.push(rec);
    return new Promise((resolve) => {
      gates.push(() => {
        rec.resolved = true;
        resolve({ async json() { return { ok: true, m1: [], ticks: [{ t: 1, p: 11 }, { t: 2, p: 12 }], tick_secs: [] }; } });
      });
    });
  };
  const computeCalls = [];   // 発行した一括計算（Test Spy）
  const seqClient = {
    computeSeqMulti(req) {
      computeCalls.push(req);
      return computeHangs ? new Promise(() => {}) : Promise.resolve({});
    },
  };
  const controller = {
    formingSeqTargets: () => ([
      { instanceId: 'ma#1', indicatorId: 'moving_averages', variant: 'default', params: {} },
    ]),
  };
  const plans = new FormingPlanCache({
    fetchImpl, datasetRef: 'jp225_tick', seqClient, controller,
    getCandles: () => candles, getTimeframe: () => '5m',
  });
  return { plans, issued, gates, computeCalls };
}

test('invalidate で in-flight の /intraday が打ち切られる（ISSUE-285・契約 1）', async () => {
  const { plans, issued } = makeHarness();
  plans.prefetch(0, 'real_ticks');
  await settle();
  assert.equal(issued.length, 1, '前提: 先読みが /intraday を 1 件発行している');
  plans.invalidate();
  assert.ok(issued[0].signal, 'in-flight 要求に打ち切り手段（AbortSignal）が配線されていない');
  assert.equal(issued[0].signal.aborted, true, 'invalidate しても in-flight /intraday が生きている（モード遷移後に旧要求が届く＝404 の原因）');
});

test('旧世代の遅延要求は /compute を発行しない（ISSUE-285・契約 2）', async () => {
  const { plans, gates, computeCalls } = makeHarness();
  plans.prefetch(0, 'real_ticks');
  await settle();
  plans.invalidate();          // モード切替相当（disable / onModeChange / 窓替え）
  gates.forEach((open) => open());   // 遅延していた /intraday がここで解決する
  await settle();
  assert.equal(computeCalls.length, 0,
    `旧世代の続き（/intraday 解決後）が一括計算を発行している（発行数: ${computeCalls.length}）`);
});

test('旧世代の計画は invalidate 後のキャッシュへ入らない（ISSUE-285・契約 3）', async () => {
  const { plans, gates } = makeHarness();
  plans.prefetch(0, 'real_ticks');
  await settle();
  plans.invalidate();
  gates.forEach((open) => open());
  await settle();
  assert.deepEqual(plans.keys(), [],
    `破棄済み世代の計画がキャッシュへ再流入している（keys: ${plans.keys().join(',')}）`);
});

test('in-flight の一括計算（/compute）にも打ち切り信号が配線される（ISSUE-285・契約 4）', async () => {
  const { plans, gates, computeCalls } = makeHarness({ computeHangs: true });
  plans.prefetch(0, 'real_ticks');
  await settle();
  gates.forEach((open) => open());   // /intraday を通し、一括計算を in-flight にする
  await settle();
  assert.equal(computeCalls.length, 1, '前提: 一括計算が発行されている');
  plans.invalidate();
  assert.ok(computeCalls[0].signal, 'in-flight 一括計算に打ち切り手段（AbortSignal）が配線されていない');
  assert.equal(computeCalls[0].signal.aborted, true, 'invalidate しても in-flight 一括計算が生きている');
});

// ---- 計算量テスト（規約 2026-08-28: 時間でなく回数。浪費の不在を 2 点以上で固定） ----
test('計算量: 打ち切り後の新規発行は 0・発行−(使用+打ち切り)=0（入力 2 点・ISSUE-285・契約 5）', async () => {
  // 入力（先読み深さ・窓長）を増やしても、invalidate 後の発行は増えない（常に 0）。
  for (const { depth, candleCount } of [{ depth: 1, candleCount: 5 }, { depth: 3, candleCount: 50 }]) {
    const { plans, issued, gates, computeCalls } = makeHarness({ candleCount });
    plans.prefetch(0, 'real_ticks', depth);
    await settle();
    const issuedBefore = issued.length;
    assert.equal(issuedBefore, depth, `前提: 深さ ${depth} の先読みが ${depth} 件発行している`);
    plans.invalidate();
    gates.forEach((open) => open());
    await settle();
    // 新規発行 0（旧世代の続きが fetch を発行しない）。
    assert.equal(issued.length, issuedBefore,
      `invalidate 後に新規 /intraday が発行された（depth=${depth}: ${issuedBefore} → ${issued.length}）`);
    assert.equal(computeCalls.length, 0,
      `invalidate 後に一括計算が発行された（depth=${depth}: ${computeCalls.length} 件）`);
    // 発行した要求 − 打ち切った要求 = 0（使用 0 の状況では全数が打ち切られる＝浪費が残らない）。
    const aborted = issued.filter((r) => r.signal && r.signal.aborted).length;
    assert.equal(issued.length - aborted, 0,
      `打ち切られずに残った旧世代要求がある（depth=${depth}: 発行 ${issued.length} / 打ち切り ${aborted}）`);
  }
});

// ---- 統合（setupReplay 経由）: disable() が「モード切替」として計画の要求を殺す（契約 6） ----
function fakeDoc(mode) {
  const els = { 'rp-speed': fakeEl({ value: '1' }), 'rp-mode': fakeEl({ value: mode }) };
  return {
    getElementById: (id) => (els[id] || (els[id] = fakeEl())),
    querySelectorAll: () => [],
    createElement: () => fakeEl(),
    addEventListener() {},
    _els: els,
  };
}

test('disable() は in-flight の先読みを打ち切り、以後の計画発行を 0 にする（ISSUE-285・契約 6）', async () => {
  globalThis.window = globalThis.window || {};
  const CANDLES = [
    { time: 100, open: 10, high: 12, low: 9, close: 11 },
    { time: 400, open: 11, high: 14, low: 10, close: 13 },
    { time: 700, open: 13, high: 15, low: 12, close: 14 },
  ];
  const intraday = [];   // { url, signal, resolved }
  const intradayGates = [];
  const computeLog = [];
  const fetchImpl = async (url, opts = {}) => {
    const u = String(url);
    if (u.startsWith('/intraday')) {
      const rec = { url: u, signal: opts && opts.signal, resolved: false };
      intraday.push(rec);
      return new Promise((resolve) => {
        intradayGates.push(() => {
          rec.resolved = true;
          resolve({ async json() { return { ok: true, m1: [], ticks: [{ t: 101, p: 11 }], tick_secs: [] }; } });
        });
      });
    }
    if (u === '/compute') {
      computeLog.push(JSON.parse(opts.body).mode);
      return { ok: true, async json() { return { ok: true, generation: 0, results: {} }; } };
    }
    return {
      ok: true,
      async json() {
        if (u.startsWith('/candles')) return { ok: true, candles: CANDLES };
        return { ok: true, days: [] };
      },
    };
  };
  const controller = {
    _timeframe: '5m', _recentBars: 0,
    setUntilTime() {}, isRecomputing() { return false; },
    async recomputeAllApplied({ preRender } = {}) { if (preRender) preRender(); },
    async recomputeFormingLatest() {},
    formingSeqTargets: () => ([
      { instanceId: 'ma#1', indicatorId: 'moving_averages', variant: 'default', params: {} },
    ]),
    applyFormingStep() {},
  };
  const handle = await setupReplay({
    chart: fakeChart(),
    mainSeries: { attachPrimitive() {}, update() {} },
    controller,
    renderer: { setCandles() {}, updateLastCandle() {} },
    datasetRef: 'jp225_tick',
    recentBars: 1500,
    document: fakeDoc('real_ticks'),
    fetchImpl,
    marketProfile: null,
  });
  await handle.enable();     // リプレイモード＝present の計画先読みが発火する
  await settle();
  assert.ok(intraday.length >= 1, '前提: リプレイ有効化で /intraday 先読みが発行されている');
  const before = intraday.length;
  await handle.disable();    // モード切替（ライブへ）
  // 旧世代の in-flight は打ち切られている（モード遷移後にライブ core へ届く要求を残さない）。
  for (const rec of intraday.filter((r) => !r.resolved)) {
    assert.ok(rec.signal, 'disable() 時点の in-flight /intraday に打ち切り手段が無い');
    assert.equal(rec.signal.aborted, true, 'disable() が in-flight /intraday を打ち切っていない');
  }
  computeLog.length = 0;
  intradayGates.forEach((open) => open());   // 遅延していた応答がモード切替後に届く
  await settle();
  assert.equal(intraday.length, before, 'disable() 後に新規 /intraday が発行された（ライブ core へ回り 404 になる要求）');
  assert.equal(computeLog.filter((m) => String(m).startsWith('latest_seq')).length, 0,
    'disable() 後に旧世代の続きが一括計算を発行した');
});
