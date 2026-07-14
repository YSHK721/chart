// live_tick_player.js（LiveTickPlayer・12 秒固定遅延のなめらか tick 再生）の仕様検証。
//
// 参照実装: prototype_260707-01/web/index.html の poll/playback 機構（依頼者実機確認済み）。
// 挙動:
//   - 2.5 秒周期の poll: /live_ticks を since カーソル付き取得しキューへ。serverNowMs と now() で
//     clockOffset を維持。cursor を最新 ms へ前進。
//   - 100ms 周期の playback: serverNow = now()+clockOffset、playUntil = serverNow - 12000。
//     ms <= playUntil の tick を順に適用。適用先は現在 tf の形成中バー（floor(ms, tf) が変われば
//     新バー・同期間は high/low/close/volume 累積）→ renderer.updateLastCandle(bar)。
//   - tf 切替・起動時: loadFormingBar(datasetRef, tf) でシード。bar=null（1W/1M 等）は当該 tf で
//     何も描かない（no-op）。
//   - start()/stop() 冪等（FormingBarUpdater と同型）。
// 構造: AAA。実タイマー・実ネット・実 DOM 非依存（全注入）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { LiveTickPlayer, isPlayerTimeframe } from '../js/adapter/front/live_tick_player.js';

// isPlayerTimeframe: プレイヤー（floor ベース tick 累積）が扱う固定周期 tf の判定。
//   1W/1M・未知は false（＝FormingBarUpdater/forming_bar ポーリングへ委譲する側）。
test('isPlayerTimeframe: fixed-period tf are true, 1W/1M/unknown are false', () => {
  for (const tf of ['1m', '5m', '15m', '30m', '1h', '4h', '1D']) {
    assert.equal(isPlayerTimeframe(tf), true, `${tf} should be player-driven`);
  }
  for (const tf of ['1W', '1M', '9z', null, undefined]) {
    assert.equal(isPlayerTimeframe(tf), false, `${String(tf)} should not be player-driven`);
  }
});

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

function spies({ seedBar = { time: 60, open: 100, high: 100, low: 100, close: 100, volume: 5 }, ticksResponses = [] } = {}) {
  const calls = { fetchSince: [], loadForming: [], updateLast: [] };
  let respIdx = 0;
  const fetchLiveTicks = async (since) => {
    calls.fetchSince.push(since);
    const r = ticksResponses[respIdx] || { ok: true, ticks: [], serverNowMs: 0 };
    respIdx = Math.min(respIdx + 1, ticksResponses.length);
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
// poll: clockOffset 維持・cursor 前進
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

// --------------------------------------------------------------------------- #
// playback: 12 秒遅延境界
// --------------------------------------------------------------------------- #
test('playback applies only ticks at or before serverNow-12000 (delay boundary)', async () => {
  const nowObj = fakeNow(1_000_000_000);
  // serverNowMs === client now → clockOffset 0。playUntil = now - 12000 = 999_988_000。
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
  // 適用は 1 件（close=200）。11999ms のものは delay 未達で未適用。
  const last = sp.calls.updateLast.at(-1);
  assert.equal(last.close, 200.0);
  const applied = sp.calls.updateLast.length;
  // もう一度 playback しても now 不変なら追加適用されない（境界の厳密性）。
  await t.tickPlayback();
  assert.equal(sp.calls.updateLast.length, applied);
});

// --------------------------------------------------------------------------- #
// バー境界: floor(ms, tf) が変われば新バー
// --------------------------------------------------------------------------- #
test('playback opens a new bar when the tick crosses the timeframe period boundary', async () => {
  const nowObj = fakeNow(2_000_000_000);
  const t0 = 2_000_000_000;
  // 1m tf。period = 60s。2 つの tick を別々の分に置き、両方 delay 境界より前にする。
  const msA = t0 - 130_000; // 2 分以上前
  const msB = t0 - 70_000;  // 1 分ちょっと前（別の分バケット・両方 playUntil 以前）
  const seedPeriod = Math.floor(msA / 1000 / 60) * 60;
  const sp = spies({
    seedBar: { time: seedPeriod, open: 100, high: 100, low: 100, close: 100, volume: 0 },
    ticksResponses: [
      { ok: true, ticks: [[msA, 150.0], [msB, 250.0]], serverNowMs: t0 },
    ],
  });
  const { player, t } = newPlayer({}, sp, nowObj);
  player.start();
  await t.tickPoll();
  await t.tickPlayback();
  // 2 本の updateLastCandle が別 time（別分）で呼ばれる（新バーが開く）。
  const times = sp.calls.updateLast.map((b) => b.time);
  assert.ok(new Set(times).size >= 2, 'new bar should open on period boundary crossing');
  assert.equal(sp.calls.updateLast.at(-1).open, 250.0); // 新バーは open=mid
});

// --------------------------------------------------------------------------- #
// tf 切替: シード再取得
// --------------------------------------------------------------------------- #
test('changing the timeframe reseeds via loadFormingBar for the new tf', async () => {
  const nowObj = fakeNow(1_000_000_000);
  let tf = '1m';
  const sp = spies({ ticksResponses: [{ ok: true, ticks: [], serverNowMs: 1_000_000_000 }] });
  const { player, t } = newPlayer({}, sp, nowObj, () => tf);
  player.start();
  await t.tickPlayback(); // 初回シード（1m）
  assert.deepEqual(sp.calls.loadForming.at(-1), ['jp225_tick', '1m']);
  tf = '1h';
  await t.tickPlayback(); // tf 変化 → 再シード（1h）
  assert.deepEqual(sp.calls.loadForming.at(-1), ['jp225_tick', '1h']);
});

// --------------------------------------------------------------------------- #
// シード null（1W/1M 等）: 当該 tf で何も描かない
// --------------------------------------------------------------------------- #
test('seed=null (unsupported tf like 1W/1M) makes the player a no-op for that tf', async () => {
  const nowObj = fakeNow(1_000_000_000);
  const t0 = 1_000_000_000;
  const sp = spies({
    seedBar: null, // /forming_bar が null（非対応 tf）
    ticksResponses: [{ ok: true, ticks: [[t0 - 20000, 200.0]], serverNowMs: t0 }],
  });
  const { player, t } = newPlayer({}, sp, nowObj, () => '1W');
  player.start();
  await t.tickPoll();
  await t.tickPlayback();
  // シード null → 価格を描かない（updateLastCandle 不呼出）。
  assert.equal(sp.calls.updateLast.length, 0);
});

// --------------------------------------------------------------------------- #
// 自己シード（参照実装復帰）: /forming_bar seed=null（短周期で当日 parquet 窓が空）でも、
//   現周期の live tick が来れば自力で形成中バーを起こして描く（1m/5m/15m の固着解消）。
//   参照 prototype_260707-01/web/index.html:63-66（!bar で open=mid の新バー）。
// --------------------------------------------------------------------------- #
test('seed=null on a supported short tf self-seeds a forming bar from a current-period tick (no freeze)', async () => {
  const nowObj = fakeNow(2_000_000_000);
  const t0 = 2_000_000_000;
  const tickMs = t0 - 20_000; // 20s 前（12s 遅延境界より前＝適用対象・現 5m 周期内）。
  const sp = spies({
    seedBar: null, // /forming_bar が null（当日 parquet フロンティア遅延で現周期窓が空）。
    ticksResponses: [{ ok: true, ticks: [[tickMs, 200.0]], serverNowMs: t0 }],
  });
  const { player, t } = newPlayer({}, sp, nowObj, () => '5m'); // 対応する短周期 tf。
  player.start();
  await t.tickPlayback(); // seed('5m') → null（_seeding 解除・_bar=null）。
  await t.tickPoll();     // 現周期 tick を enqueue。
  await t.tickPlayback(); // 自己シード: 最初の tick でバーを起こして描く。
  assert.equal(sp.calls.updateLast.length, 1, 'self-seed draws instead of freezing');
  const b = sp.calls.updateLast.at(-1);
  assert.equal(b.open, 200.0);
  assert.equal(b.close, 200.0);
  assert.equal(b.time, Math.floor(tickMs / 1000 / 300) * 300, 'bar time = floor(tickMs, 5m)');
});

test('seed=null self-seed ignores a tick older than the current live period (protect /candles history)', async () => {
  const nowObj = fakeNow(2_000_000_000);
  const t0 = 2_000_000_000;
  const oldMs = t0 - 700_000; // 700s 前＝現 5m 周期より 2 期間以上前（履歴側・後退禁止）。
  const sp = spies({
    seedBar: null,
    ticksResponses: [{ ok: true, ticks: [[oldMs, 999.0]], serverNowMs: t0 }],
  });
  const { player, t } = newPlayer({}, sp, nowObj, () => '5m');
  player.start();
  await t.tickPlayback(); // seed null。
  await t.tickPoll();     // 過去周期の tick を enqueue。
  await t.tickPlayback(); // 現周期より前の tick は自己シードしない（/candles 履歴を後退させない）。
  assert.equal(sp.calls.updateLast.length, 0, 'older-than-current-period tick must not self-seed');
});

// --------------------------------------------------------------------------- #
// 後退ガード: シード期間より前の tick は無視（履歴＝/candles 済を後退させない・🟡3）
// --------------------------------------------------------------------------- #
test('applyTick ignores a tick from a period before the seed bar (no history regression)', async () => {
  const nowObj = fakeNow(2_000_000_000);
  const t0 = 2_000_000_000;
  const recentMs = t0 - 70_000;                          // seed 基準の分。
  const seedPeriod = Math.floor(recentMs / 1000 / 60) * 60;
  const earlyMs = t0 - 600_000;                          // 10 分前（seed period より前・playUntil 以前）。
  assert.ok(Math.floor(earlyMs / 1000 / 60) * 60 < seedPeriod, 'test setup: early tick must precede seed period');
  const sp = spies({
    seedBar: { time: seedPeriod, open: 100, high: 100, low: 100, close: 100, volume: 0 },
    ticksResponses: [{ ok: true, ticks: [[earlyMs, 999.0]], serverNowMs: t0 }],
  });
  const { player, t } = newPlayer({}, sp, nowObj);
  player.start();
  await t.tickPlayback(); // 初回シード（queue 空）。
  await t.tickPoll();     // 過去期間の tick を enqueue。
  await t.tickPlayback(); // 適用を試みる。
  // periodSec < seedBar.time の tick は無視＝updateLastCandle を呼ばない（lwc 時刻単調性を守る）。
  assert.equal(sp.calls.updateLast.length, 0);
});

// --------------------------------------------------------------------------- #
// tf 切替の再シード直列化: 切替 seed の await 中に再入した playback が旧 tf のバーへ
//   誤って描かない（🟡4）。初回 1m でバーを確立 → 5m へ切替（seed 保留）→ 保留中の再入で
//   「新 tfSec(300) × 旧 1m bar」による不正描画が起きないことを固定する。
// --------------------------------------------------------------------------- #
test('a re-entrant playback during a tf-switch seed does not draw against the stale old-tf bar', async () => {
  const nowObj = fakeNow(2_000_000_000);
  const t0 = 2_000_000_000;
  let tf = '1m';
  let pendingResolve = null; // 非 null の間は 2 回目以降のシードを保留する。
  const seedFor = (which) => ({ time: Math.floor((t0 - 70_000) / 1000 / (which === '1m' ? 60 : 300)) * (which === '1m' ? 60 : 300), open: 100, high: 100, low: 100, close: 100, volume: 0 });
  const loadFormingBar = (ref, which) => {
    if (which === '1m') return Promise.resolve(seedFor('1m')); // 初回は即解決。
    return new Promise((res) => { pendingResolve = () => res(seedFor('5m')); }); // 5m は保留。
  };
  // 常に「12 秒より前・現在付近」の tick を 1 件返す（適用対象）。
  const fetchLiveTicks = async () => ({ ok: true, ticks: [[t0 - 20_000, 200.0]], serverNowMs: t0 });
  const calls = { updateLast: [] };
  const renderer = { updateLastCandle: (b) => calls.updateLast.push({ ...b }) };
  const timers = fakeTimers();
  const player = new LiveTickPlayer({
    renderer, fetchLiveTicks, loadFormingBar, datasetRef: 'jp225_tick',
    getTimeframe: () => tf,
    setInterval: timers.setIntervalFake, clearInterval: timers.clearIntervalFake, now: nowObj.now,
  });
  player.start();
  await timers.tickPoll();       // clockOffset 確立＋tick を enqueue。
  await timers.tickPlayback();   // 初回 1m シード（即解決）→ バー確立・tick 適用。
  assert.ok(calls.updateLast.length >= 1, 'setup: 1m bar established');
  const drawnAfter1m = calls.updateLast.length;

  tf = '5m';                     // tf 切替。次 playback で 5m シードが走る（保留）。
  const seeding = timers.tickPlayback(); // _seed('5m') 開始 → 保留で suspend。
  await timers.tickPoll();       // 保留中に新しい tick を enqueue。
  await timers.tickPlayback();   // 再入 playback: FIX は _bar=null で描かない。
  //   旧実装は _tfSec=300（5m）× 旧 1m bar.time で誤バーを描く（この assert が回帰を捕える）。
  assert.equal(calls.updateLast.length, drawnAfter1m, 'no draw against stale old-tf bar during the switch seed');
  pendingResolve();              // 5m seed 解決。
  await seeding;
});

// --------------------------------------------------------------------------- #
// clockOffset がずれた時計を補正する
// --------------------------------------------------------------------------- #
test('playback uses serverNow (now+clockOffset) so a skewed client clock still gates by server time', async () => {
  // client now を server より 5 秒進める。serverNowMs = now - 5000。
  const nowObj = fakeNow(1_000_000_000);
  const t0 = 1_000_000_000;
  const serverNow = t0 - 5000; // clockOffset = serverNow - now = -5000
  // playUntil(server 基準) = serverNow - 12000 = t0 - 17000。
  const msApply = t0 - 17001;   // server 基準で 12 秒以上前 → 適用
  const msHold = t0 - 12000;    // client 基準では 12 秒だが server 基準では 7 秒 → 保留
  const sp = spies({
    seedBar: { time: Math.floor(msApply / 1000 / 60) * 60, open: 100, high: 100, low: 100, close: 100, volume: 0 },
    ticksResponses: [{ ok: true, ticks: [[msApply, 200.0], [msHold, 300.0]], serverNowMs: serverNow }],
  });
  const { player, t } = newPlayer({}, sp, nowObj);
  player.start();
  await t.tickPoll();     // clockOffset = -5000 を確立
  await t.tickPlayback();
  // server 基準の delay 境界で 1 件だけ適用（clockOffset を無視すると 2 件適用され落ちる）。
  assert.equal(sp.calls.updateLast.length, 1);
  assert.equal(sp.calls.updateLast.at(-1).close, 200.0);
});


// --------------------------------------------------------------------------- #
// セッション日 1D（ISSUE-078）: 日曜夜 UTC の tick が月曜セッションの 1D バー（ラベル深夜 time）へ累積する。
// --------------------------------------------------------------------------- #
test('1D: 日曜夜 UTC の tick はセッション 1D バー（ラベル深夜 time）へ累積しフリーズしない', async () => {
  const calls = [];
  const renderer = { updateLastCandle: (b) => calls.push({ ...b }) };
  // 2026-07-12 22:03 UTC（日曜夜＝月曜セッション）。seed は /forming_bar が新規約 time（7/13 深夜）を返す。
  const t0ms = 1783893824000;
  const seedBar = { time: 1783900800, open: 100, high: 101, low: 99, close: 100.5, volume: 3 };
  const player = new LiveTickPlayer({
    renderer,
    fetchLiveTicks: async () => ({ ok: true, ticks: [], serverNowMs: t0ms }),
    loadFormingBar: async () => seedBar,
    datasetRef: 'jp225_tick',
    getTimeframe: () => '1D',
    setInterval: () => 1,
    clearInterval: () => {},
    now: () => t0ms,
    delayMs: 0,
  });
  await player._seed('1D');
  player._queue.push([t0ms - 1000, 105.0]); // 日曜 22:03 UTC の tick。
  await player._playback();
  assert.equal(calls.length, 1, '日曜夜 tick が適用される（旧 UTC floor では過去期間扱いで無視されていた）');
  assert.equal(calls[0].time, 1783900800, 'バー time は 1D 規約（セッション日ラベルの UTC 深夜）');
  assert.equal(calls[0].high, 105.0);
});
