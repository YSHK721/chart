// live_tick_player.js（LiveTickPlayer・12 秒固定遅延のなめらか tick 再生）の仕様検証。
//
// 参照実装: prototype_260707-01/web/index.html の poll/playback 機構（依頼者実機確認済み）。
// 挙動:
//   - 2.5 秒周期の poll: /live_ticks を since カーソル付き取得しキューへ。serverNowMs と now() で
//     clockOffset を維持。cursor を最新 ms へ前進。時間足は**常に**申告する（バー帰属の解決に要る）。
//   - 100ms 周期の playback: serverNow = now()+clockOffset、playUntil = serverNow - 12000。
//     ms <= playUntil の tick を順に適用。適用先は形成中バー（サーバが返した barTime が変われば
//     新バー・同じなら high/low/close/volume 累積）→ renderer.updateLastCandle(bar)。
//   - tf 切替・起動時: loadFormingBar(datasetRef, tf) でシード。
//   - start()/stop() 冪等（FormingBarUpdater と同型）。
//
// **全時間足で同一設計**（ISSUE-253）: プレイヤーは「この tick はどのバーに属するか」を計算しない。
//   バー帰属はサーバの唯一源（marketdata.tf_meta.bar_time_unix）が解決して barTimes として届く。
//   よって日中足・1D・1W/1M の区別がコード上に存在せず、更新粒度は全時間足でティック単位になる。
//   本テストも tf ごとの分岐を持たず、**同じ検証を全時間足へ並べて**同一挙動を固定する。
// 構造: AAA。実タイマー・実ネット・実 DOM 非依存（全注入）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { LiveTickPlayer } from '../js/adapter/front/live_tick_player.js';

// 検証対象の全時間足（台帳と同一集合）。どの足でも同じ検証が通ることが本設計の要件。
const ALL_TF = ['1m', '5m', '15m', '30m', '1h', '4h', '1D', '1W', '1M'];

// fake timers: setInterval を ms で捕捉し、poll(2500)/playback(100) を個別に手動駆動する。
function fakeTimers() {
  const intervals = new Map(); // id -> { fn, ms }
  let nextId = 1;
  const setIntervalFake = (fn, ms) => { const id = nextId++; intervals.set(id, { fn, ms }); return id; };
  const clearIntervalFake = (id) => { intervals.delete(id); };
  const byMs = (ms) => [...intervals.values()].find((iv) => iv.ms === ms);
  const tickPoll = async () => { const iv = byMs(2500); if (iv) await iv.fn(); };
  const tickPlayback = async () => { const iv = byMs(100); if (iv) await iv.fn(); };
  return { setIntervalFake, clearIntervalFake, intervals, tickPoll, tickPlayback };
}

// 可制御 now()（ミリ秒）。
function fakeNow(start = 1_000_000_000) {
  const box = { t: start };
  const now = () => box.t;
  now.advance = (dt) => { box.t += dt; };
  now.set = (v) => { box.t = v; };
  return { now, box };
}

// サーバのバー帰属規則の代役（本番は marketdata.tf_meta.bar_time_unix）。テストは規則の中身を
//   検証しない（それはサーバ側テストの責務）。プレイヤーが「届いた値をそのまま使う」ことだけを見る。
const barRule = (periodSec) => (ms) => Math.floor(ms / 1000 / periodSec) * periodSec;

function spies({
  seedBar = { time: 60, open: 100, high: 100, low: 100, close: 100, volume: 5 },
  ticksResponses = [],
  bars = barRule(60),
} = {}) {
  const calls = { fetchSince: [], req: [], loadForming: [], updateLast: [] };
  let respIdx = 0;
  const fetchLiveTicks = async (since, req) => {
    calls.fetchSince.push(since);
    calls.req.push(req ?? null);
    const r = ticksResponses[respIdx] || { ok: true, ticks: [], serverNowMs: 0 };
    respIdx = Math.min(respIdx + 1, ticksResponses.length);
    // サーバ相当: barTimes / nowBarTime を明示指定が無ければ規則から補う。
    if (r.barTimes === undefined && bars) {
      r.barTimes = (r.ticks || []).map((t) => bars(t[0]));
    }
    if (r.nowBarTime === undefined && bars) {
      r.nowBarTime = bars(r.serverNowMs || 0);
    }
    return r;
  };
  const loadFormingBar = async (ref, tf) => { calls.loadForming.push([ref, tf]); return typeof seedBar === 'function' ? seedBar(tf) : seedBar; };
  const renderer = { updateLastCandle: (b) => calls.updateLast.push({ ...b }) };
  return { calls, fetchLiveTicks, loadFormingBar, renderer };
}

function newPlayer(overrides = {}, sp = spies(), nowObj = fakeNow(), getTf = () => '1m') {
  const t = fakeTimers();
  const player = new LiveTickPlayer({
    renderer: sp.renderer,
    fetchLiveTicks: sp.fetchLiveTicks,
    loadFormingBar: sp.loadFormingBar,
    datasetRef: 'jp225_tick',
    getTimeframe: getTf,
    setInterval: t.setIntervalFake,
    clearInterval: t.clearIntervalFake,
    now: nowObj.now,
    ...overrides,
  });
  return { player, t, sp, nowObj };
}

// --------------------------------------------------------------------------- #
// start/stop 冪等・タイマー登録
// --------------------------------------------------------------------------- #
test('start registers exactly two intervals: poll(2500) and playback(100)', () => {
  const { player, t } = newPlayer();
  player.start();
  const msSet = [...t.intervals.values()].map((iv) => iv.ms).sort((a, b) => a - b);
  assert.deepEqual(msSet, [100, 2500]);
});

test('calling start twice does not register more intervals (idempotent)', () => {
  const { player, t } = newPlayer();
  player.start();
  player.start();
  assert.equal(t.intervals.size, 2);
});

test('stop clears both intervals (idempotent)', () => {
  const { player, t } = newPlayer();
  player.start();
  player.stop();
  assert.equal(t.intervals.size, 0);
  player.stop(); // 二重 stop は no-op
  assert.equal(t.intervals.size, 0);
});

// --------------------------------------------------------------------------- #
// poll: clockOffset 維持・cursor 前進・時間足の常時申告
// --------------------------------------------------------------------------- #
test('poll maintains clockOffset from serverNowMs and advances the since cursor', async () => {
  const nowObj = fakeNow(1_000_000_000);
  const sp = spies({
    ticksResponses: [
      { ok: true, ticks: [[500, 100.0], [800, 101.0]], serverNowMs: 1_000_005_000 },
    ],
  });
  const { player, t } = newPlayer({}, sp, nowObj);
  player.start();
  await t.tickPoll();
  // 初回 poll は since=0。以降のカーソルは最新 tick ms（800）。
  assert.equal(sp.calls.fetchSince[0], 0);
  await t.tickPoll();
  assert.equal(sp.calls.fetchSince[1], 800);
});

// 指標を 1 つも適用していなくても timeframe は申告する（barTimes が無いと 1 本も描けない）。
test('poll always declares the timeframe, even with no indicators applied', async () => {
  const sp = spies();
  const { player, t } = newPlayer({ getComputeSpecs: () => [] }, sp, fakeNow(), () => '1W');
  player.start();
  await t.tickPoll();
  const req = sp.calls.req.at(-1);
  assert.equal(req.timeframe, '1W');
  assert.equal(req.datasetRef, 'jp225_tick');
  assert.equal(req.specs, null);
});

// --------------------------------------------------------------------------- #
// playback: 12 秒遅延境界
// --------------------------------------------------------------------------- #
test('playback applies only ticks at or before serverNow-12000 (delay boundary)', async () => {
  const nowObj = fakeNow(1_000_000_000);
  const t0 = 1_000_000_000;
  const sp = spies({
    seedBar: { time: Math.floor((t0 - 12001) / 1000 / 60) * 60, open: 100, high: 100, low: 100, close: 100, volume: 0 },
    ticksResponses: [
      { ok: true, ticks: [
        [t0 - 12001, 200.0], // playUntil 以前 → 適用される
        [t0 - 11999, 300.0], // playUntil より後（12 秒未満）→ 保留
      ], serverNowMs: t0 },
    ],
  });
  const { player, t } = newPlayer({}, sp, nowObj);
  player.start();
  await t.tickPoll();      // キューへ 2 件
  await t.tickPlayback();  // 12 秒より古い 1 件のみ適用
  const last = sp.calls.updateLast.at(-1);
  assert.equal(last.close, 200.0);
  const applied = sp.calls.updateLast.length;
  await t.tickPlayback();
  assert.equal(sp.calls.updateLast.length, applied);
});

test('playback uses serverNow (now+clockOffset) so a skewed client clock still gates by server time', async () => {
  const nowObj = fakeNow(1_000_000_000);
  const t0 = 1_000_000_000;
  const serverNow = t0 - 5000; // clockOffset = serverNow - now = -5000
  const msApply = t0 - 17001;  // server 基準で 12 秒以上前 → 適用
  const msHold = t0 - 12000;   // client 基準では 12 秒だが server 基準では 7 秒 → 保留
  const sp = spies({
    seedBar: { time: Math.floor(msApply / 1000 / 60) * 60, open: 100, high: 100, low: 100, close: 100, volume: 0 },
    ticksResponses: [{ ok: true, ticks: [[msApply, 200.0], [msHold, 300.0]], serverNowMs: serverNow }],
  });
  const { player, t } = newPlayer({}, sp, nowObj);
  player.start();
  await t.tickPoll();
  await t.tickPlayback();
  assert.equal(sp.calls.updateLast.length, 1);
  assert.equal(sp.calls.updateLast.at(-1).close, 200.0);
});

// --------------------------------------------------------------------------- #
// バー帰属はサーバ供給（全時間足で同一）
//   プレイヤーは barTime を比較するだけ。以下 3 本の検証を **全時間足へ同じ形で** 並べる。
//   ここが tf ごとに割れないことが「更新粒度が時間足で変わらない」の実体。
// --------------------------------------------------------------------------- #
for (const tf of ALL_TF) {
  test(`[${tf}] every tick updates the candle at the server-supplied bar time (tick granularity)`, async () => {
    const t0 = 2_000_000_000;
    const ticks = [[t0 - 20_000, 100.0], [t0 - 19_000, 105.0], [t0 - 18_000, 95.0]];
    const BAR = 12_345_678;   // サーバが返すバー time（規則の中身はプレイヤーに関係ない）
    const sp = spies({
      seedBar: null,
      bars: null,
      ticksResponses: [{ ok: true, ticks, barTimes: ticks.map(() => BAR), nowBarTime: BAR, serverNowMs: t0 }],
    });
    const { player, t } = newPlayer({}, sp, fakeNow(t0), () => tf);
    player.start();
    await t.tickPlayback();          // シード
    await t.tickPoll();
    await t.tickPlayback();
    assert.equal(sp.calls.updateLast.length, ticks.length, '1 ティック = 1 更新');
    const bar = sp.calls.updateLast.at(-1);
    assert.equal(bar.time, BAR, 'バー time はサーバ供給値そのもの');
    assert.equal(bar.open, 100.0);   // 最初の tick で固定
    assert.equal(bar.high, 105.0);
    assert.equal(bar.low, 95.0);
    assert.equal(bar.close, 95.0);
    assert.equal(bar.volume, 3);
  });

  test(`[${tf}] a changed bar time opens a new bar and fires onBarClose exactly once`, async () => {
    const t0 = 2_000_000_000;
    const ticks = [[t0 - 20_000, 100.0], [t0 - 19_000, 101.0], [t0 - 18_000, 102.0]];
    const barTimes = [1_000, 1_000, 2_000];   // 3 本目でバーが変わる
    const closes = [];
    const sp = spies({
      seedBar: null,
      bars: null,
      ticksResponses: [{ ok: true, ticks, barTimes, nowBarTime: 1_000, serverNowMs: t0 }],
    });
    const { player, t } = newPlayer({ onBarClose: () => closes.push(1) }, sp, fakeNow(t0), () => tf);
    player.start();
    await t.tickPlayback();
    await t.tickPoll();
    await t.tickPlayback();
    assert.equal(closes.length, 1);
    const bar = sp.calls.updateLast.at(-1);
    assert.equal(bar.time, 2_000);
    assert.equal(bar.open, 102.0, '新バーは open=mid');
    assert.equal(bar.volume, 1);
  });

  test(`[${tf}] a tick belonging to an earlier bar never regresses history`, async () => {
    const t0 = 2_000_000_000;
    const sp = spies({
      seedBar: { time: 5_000, open: 100, high: 100, low: 100, close: 100, volume: 1 },
      bars: null,
      ticksResponses: [{
        ok: true, ticks: [[t0 - 20_000, 999.0]], barTimes: [4_000], nowBarTime: 5_000, serverNowMs: t0,
      }],
    });
    const { player, t } = newPlayer({}, sp, fakeNow(t0), () => tf);
    player.start();
    await t.tickPlayback();
    await t.tickPoll();
    await t.tickPlayback();
    assert.equal(sp.calls.updateLast.length, 0, 'シード済みバーより前の tick は描かない');
  });
}

// --------------------------------------------------------------------------- #
// tf 切替: シード再取得
// --------------------------------------------------------------------------- #
test('changing the timeframe reseeds via loadFormingBar for the new tf', async () => {
  let tf = '1m';
  const sp = spies({ ticksResponses: [{ ok: true, ticks: [], serverNowMs: 1_000_000_000 }] });
  const { player, t } = newPlayer({}, sp, fakeNow(1_000_000_000), () => tf);
  player.start();
  await t.tickPlayback(); // 初回シード（1m）
  assert.deepEqual(sp.calls.loadForming.at(-1), ['jp225_tick', '1m']);
  tf = '1W';
  await t.tickPlayback(); // tf 変化 → 再シード（1W・暦周期でも同じ手順）
  assert.deepEqual(sp.calls.loadForming.at(-1), ['jp225_tick', '1W']);
});

// 足の切替後に届いた「旧足で解決された」データは描かない（別足の値を混ぜない）。
test('ticks resolved for a previous timeframe are discarded after a tf switch', async () => {
  const t0 = 2_000_000_000;
  let tf = '1m';
  const sp = spies({
    seedBar: null,
    bars: null,
    ticksResponses: [{
      ok: true, ticks: [[t0 - 20_000, 200.0]], barTimes: [1_000], nowBarTime: 1_000, serverNowMs: t0,
    }],
  });
  const { player, t } = newPlayer({}, sp, fakeNow(t0), () => tf);
  player.start();
  await t.tickPoll();      // 1m として解決されたデータを受領
  tf = '1M';               // 適用前に足を切り替える
  await t.tickPlayback();  // 新足でシード
  await t.tickPlayback();  // 旧足のデータは捨てる
  assert.equal(sp.calls.updateLast.length, 0);
});

// --------------------------------------------------------------------------- #
// 自己シード（参照実装復帰）: /forming_bar seed=null でも現在のバーの tick から起こす
// --------------------------------------------------------------------------- #
test('seed=null self-seeds a forming bar from a current-bar tick (no freeze)', async () => {
  const t0 = 2_000_000_000;
  const sp = spies({
    seedBar: null,
    bars: null,
    ticksResponses: [{
      ok: true, ticks: [[t0 - 20_000, 200.0]], barTimes: [7_000], nowBarTime: 7_000, serverNowMs: t0,
    }],
  });
  const { player, t } = newPlayer({}, sp, fakeNow(t0), () => '5m');
  player.start();
  await t.tickPlayback(); // seed('5m') → null
  await t.tickPoll();
  await t.tickPlayback(); // 自己シード
  assert.equal(sp.calls.updateLast.length, 1, 'self-seed draws instead of freezing');
  const b = sp.calls.updateLast.at(-1);
  assert.equal(b.open, 200.0);
  assert.equal(b.time, 7_000, 'バー time はサーバ供給値');
});

test('seed=null self-seed ignores a tick from a bar older than the current one', async () => {
  const t0 = 2_000_000_000;
  const sp = spies({
    seedBar: null,
    bars: null,
    ticksResponses: [{
      ok: true, ticks: [[t0 - 700_000, 999.0]], barTimes: [6_000], nowBarTime: 7_000, serverNowMs: t0,
    }],
  });
  const { player, t } = newPlayer({}, sp, fakeNow(t0), () => '5m');
  player.start();
  await t.tickPlayback();
  await t.tickPoll();
  await t.tickPlayback();
  assert.equal(sp.calls.updateLast.length, 0, '現在より前のバーの tick は自己シードしない');
});

// --------------------------------------------------------------------------- #
// tf 切替の再シード直列化: 切替 seed の await 中に再入した playback が旧 tf のバーへ描かない（🟡4）
// --------------------------------------------------------------------------- #
test('a re-entrant playback during a tf-switch seed does not draw against the stale old-tf bar', async () => {
  const nowObj = fakeNow(2_000_000_000);
  const t0 = 2_000_000_000;
  let tf = '1m';
  let pendingResolve = null;
  const seedFor = () => ({ time: 1_000, open: 100, high: 100, low: 100, close: 100, volume: 0 });
  const loadFormingBar = (ref, which) => {
    if (which === '1m') return Promise.resolve(seedFor());
    return new Promise((res) => { pendingResolve = () => res(seedFor()); });
  };
  const fetchLiveTicks = async () => ({
    ok: true, ticks: [[t0 - 20_000, 200.0]], barTimes: [1_000], nowBarTime: 1_000, serverNowMs: t0,
  });
  const calls = { updateLast: [] };
  const renderer = { updateLastCandle: (b) => calls.updateLast.push({ ...b }) };
  const timers = fakeTimers();
  const player = new LiveTickPlayer({
    renderer, fetchLiveTicks, loadFormingBar, datasetRef: 'jp225_tick',
    getTimeframe: () => tf,
    setInterval: timers.setIntervalFake, clearInterval: timers.clearIntervalFake, now: nowObj.now,
  });
  player.start();
  await timers.tickPoll();
  await timers.tickPlayback();
  assert.ok(calls.updateLast.length >= 1, 'setup: 1m bar established');
  const drawnAfter1m = calls.updateLast.length;

  tf = '5m';
  const seeding = timers.tickPlayback(); // _seed('5m') 開始 → 保留で suspend
  await timers.tickPoll();
  await timers.tickPlayback();           // 再入 playback: _bar=null で描かない
  assert.equal(calls.updateLast.length, drawnAfter1m, 'no draw against stale old-tf bar during the switch seed');
  pendingResolve();
  await seeding;
});

// --------------------------------------------------------------------------- #
// ISSUE-250 Phase 1: 指標末尾値（tails）の同梱・同期適用
//   poll で適用中インスタンスを申告し、応答に同梱された「各ティック時点の末尾値」を
//   tick 適用（updateLastCandle）と**同一同期ブロック**で描く。tick 路に HTTP 往復が無いため
//   「指標更新回数 == ローソク更新回数」が構成上成立する。
// --------------------------------------------------------------------------- #
test('poll declares the applied specs with datasetRef/timeframe/limit (tails request)', async () => {
  const specs = [{ instanceId: 'profit_rsi#1', indicatorId: 'profit_rsi', variant: 'default', params: { length: 14 } }];
  const sp = spies();
  const { player, t } = newPlayer({
    getComputeSpecs: () => specs,
    getLimit: () => 1386,
  }, sp, fakeNow(), () => '15m');
  player.start();
  await t.tickPoll();
  assert.deepEqual(sp.calls.req.at(-1), {
    // tailsWithinMs は「末尾値が要る区間」の申告（ISSUE-257）。specs と同じく毎 poll 添える。
    specs, datasetRef: 'jp225_tick', timeframe: '15m', limit: 1386, tailsWithinMs: 14500,
  });
});

// 回数一致の本体（全時間足で同一）: 適用した tick と同数だけ applyFormingTails が呼ばれ、
//   その時刻は直前に描いた形成中バーの time と一致する。
for (const tf of ALL_TF) {
  test(`[${tf}] every applied tick draws its indicator tails in the same synchronous block`, async () => {
    const t0 = 1_000_000_000;
    const ticks = [[t0 - 20_000, 200.0], [t0 - 19_000, 201.0], [t0 - 18_000, 202.0]];
    const tails = ticks.map(([ms], i) => ({ tickMs: ms, tails: { 'profit_rsi#1': { RSI: 50 + i } } }));
    const order = [];
    const sp = spies({
      seedBar: null,
      bars: null,
      ticksResponses: [{
        ok: true, ticks, tails, barTimes: ticks.map(() => 9_000), nowBarTime: 9_000, serverNowMs: t0,
      }],
    });
    sp.renderer = { updateLastCandle: (b) => order.push(['candle', b.time]) };
    const { player, t } = newPlayer({
      renderer: sp.renderer,
      getComputeSpecs: () => [{ instanceId: 'profit_rsi#1', indicatorId: 'profit_rsi', variant: 'default', params: {} }],
      applyFormingTails: (map, barTime) => order.push(['tails', barTime, map]),
    }, sp, fakeNow(t0), () => tf);
    player.start();
    await t.tickPlayback();
    await t.tickPoll();
    await t.tickPlayback();
    const candles = order.filter((o) => o[0] === 'candle');
    const applied = order.filter((o) => o[0] === 'tails');
    assert.equal(candles.length, 3, 'ローソク更新は tick 数と同数');
    assert.equal(applied.length, 3, '指標更新回数 == ローソク更新回数');
    assert.deepEqual(order.map((o) => o[0]), ['candle', 'tails', 'candle', 'tails', 'candle', 'tails']);
    for (let i = 0; i < 3; i += 1) {
      assert.equal(applied[i][1], candles[i][1]);
    }
    assert.deepEqual(applied[2][2], { 'profit_rsi#1': { RSI: 52 } });
    assert.equal(player.stats().tailsApplied, 3);
  });
}

test('ticks without tails still update the candle (indicator draw is simply skipped)', async () => {
  const t0 = 1_000_000_000;
  const sp = spies({
    seedBar: null,
    bars: null,
    ticksResponses: [{
      ok: true, ticks: [[t0 - 20_000, 200.0]], barTimes: [9_000], nowBarTime: 9_000, serverNowMs: t0,
    }],
  });
  let tailCalls = 0;
  const { player, t } = newPlayer({
    applyFormingTails: () => { tailCalls += 1; },
    getComputeSpecs: () => [{ instanceId: 'x#1', indicatorId: 'profit_rsi', variant: 'default', params: {} }],
  }, sp, fakeNow(t0), () => '1m');
  player.start();
  await t.tickPlayback();
  await t.tickPoll();
  await t.tickPlayback();
  assert.equal(sp.calls.updateLast.length, 1);
  assert.equal(tailCalls, 0);
});

test('a tails entry whose tickMs does not match its tick is dropped', async () => {
  const t0 = 1_000_000_000;
  const sp = spies({
    seedBar: null,
    bars: null,
    ticksResponses: [{
      ok: true,
      ticks: [[t0 - 20_000, 200.0]],
      tails: [{ tickMs: t0 - 99_999, tails: { 'x#1': { v: 1 } } }],
      barTimes: [9_000], nowBarTime: 9_000, serverNowMs: t0,
    }],
  });
  let tailCalls = 0;
  const { player, t } = newPlayer({
    applyFormingTails: () => { tailCalls += 1; },
    getComputeSpecs: () => [{ instanceId: 'x#1', indicatorId: 'profit_rsi', variant: 'default', params: {} }],
  }, sp, fakeNow(t0), () => '1m');
  player.start();
  await t.tickPlayback();
  await t.tickPoll();
  await t.tickPlayback();
  assert.equal(sp.calls.updateLast.length, 1);
  assert.equal(tailCalls, 0);
});

// barTimes が無い応答（未知 tf 等でサーバが解決できない）はローソクも指標も描かない
//   ＝バー帰属を推測で埋めない（誤ったバーへ描くより描かないほうが正しい）。
test('a response without barTimes draws nothing (no client-side guessing of bar attribution)', async () => {
  const t0 = 1_000_000_000;
  const sp = spies({
    seedBar: null,
    bars: null,
    ticksResponses: [{ ok: true, ticks: [[t0 - 20_000, 200.0]], serverNowMs: t0 }],
  });
  const { player, t } = newPlayer({}, sp, fakeNow(t0), () => '1m');
  player.start();
  await t.tickPlayback();
  await t.tickPoll();
  await t.tickPlayback();
  assert.equal(sp.calls.updateLast.length, 0);
});

// --------------------------------------------------------------------------- #
// ISSUE-257: 同時要求を構成上 1 本に固定し、末尾値が要る区間を申告する
// --------------------------------------------------------------------------- #

test('a poll fired while the previous one is still in flight issues no request', async () => {
  // 応答が poll 間隔より遅い状況を再現する（解決を手で握る）。旧実装は setInterval が
  //   前回完了を待たないため、要求が撃つたびに積み上がった（サーバ側スレッド枯渇の起点）。
  const t0 = 1_000_000_000;
  let release;
  const gate = new Promise((res) => { release = res; });
  const calls = [];
  const fetchLiveTicks = async (since, req) => {
    calls.push(since);
    await gate;
    return { ok: true, ticks: [], serverNowMs: t0 };
  };
  const sp = spies();
  const { player, t } = newPlayer(
    { fetchLiveTicks }, sp, fakeNow(t0), () => '1m');
  player.start();

  // 追加の poll は await しない（旧コードでは gate に捕まって永久に返らず、ハングと
  //   区別がつかなくなる。await せずに発火だけさせれば「要求が出たか」で即座に判定できる）。
  const first = t.tickPoll();          // 1 本目（gate で滞留させる）
  await Promise.resolve();
  const second = t.tickPoll();         // 2 本目・3 本目は出てはならない
  const third = t.tickPoll();
  await Promise.resolve();
  assert.equal(calls.length, 1, '未完了中に追加の要求を出さない');

  release();
  await Promise.all([first, second, third]);
  await t.tickPoll();                  // 完了後は通常どおり再開する
  assert.equal(calls.length, 2);
});

test('the in-flight guard is released even when the poll fails', async () => {
  const t0 = 1_000_000_000;
  let n = 0;
  const fetchLiveTicks = async () => { n += 1; throw new Error('boom'); };
  const { player, t } = newPlayer({ fetchLiveTicks }, spies(), fakeNow(t0), () => '1m');
  player.start();
  await t.tickPoll();
  await t.tickPoll();
  assert.equal(n, 2, '失敗しても次 poll で回復する（ガードが残らない）');
});

test('the poll declares the span that actually needs per-tick tails', async () => {
  // 末尾値は「個別に描かれる tick」だけ要る。playback は serverNow-delayMs 以前を 1 ループで
  //   一気に適用するため、その区間の末尾値は最後の 1 点しか画面に出ない。申告する区間長は
  //   delayMs（地平）＋ pollMs（次 poll までに地平が進むぶん）。
  const t0 = 1_000_000_000;
  const sp = spies({ ticksResponses: [{ ok: true, ticks: [], serverNowMs: t0 }] });
  const { player, t } = newPlayer({
    getComputeSpecs: () => [{ instanceId: 'x#1', indicatorId: 'profit_rsi', variant: 'default', params: {} }],
  }, sp, fakeNow(t0), () => '1m');
  player.start();
  await t.tickPoll();
  assert.equal(sp.calls.req[0].tailsWithinMs, 12000 + 2500);
});
