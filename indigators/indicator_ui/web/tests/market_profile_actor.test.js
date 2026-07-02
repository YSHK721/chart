// market_profile_actor.js のトグル制御ロジック検証。
//
// 設計入力: 依頼「プロファイルを取得して primitive に反映する薄い制御・トグル ON/OFF」。
//   client / primitive / mainSeries は Fake を注入し、副作用（fetch・attach・可視状態）を観測する。
//   実 fetch / 実 lwc / canvas 非依存。構造: Arrange-Act-Assert。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { MarketProfileActor } from '../js/adapter/front/market_profile_actor.js';

const PROFILE = { bins: [{ price: 1, tpo: 1, norm: 1 }], poc: 1, va_low: 1, va_high: 1 };

// fetch 呼び出し回数と受領コンテキストを記録する Fake client。
function fakeClient(result = PROFILE) {
  const calls = [];
  return {
    calls,
    async fetchProfile(ctx) { calls.push(ctx); return result; },
  };
}

// setProfile / setVisible の呼び出しを記録する Fake primitive。
function fakePrimitive() {
  return {
    profiles: [], visibles: [],
    setProfile(p) { this.profiles.push(p); },
    setVisible(v) { this.visibles.push(v); },
  };
}

// attachPrimitive の呼び出し回数を記録する Fake mainSeries。
function fakeMainSeries() {
  return { attached: [], attachPrimitive(p) { this.attached.push(p); } };
}

function makeActor({ client, primitive, mainSeries, ctx = { datasetRef: 'sample', timeframe: '1D', limit: 1500 } } = {}) {
  const c = client ?? fakeClient();
  const p = primitive ?? fakePrimitive();
  const ms = mainSeries ?? fakeMainSeries();
  const actor = new MarketProfileActor({ client: c, primitive: p, mainSeries: ms, getContext: () => ctx });
  return { actor, client: c, primitive: p, mainSeries: ms };
}

test('setEnabled(true) fetches with the current context, applies the profile and shows the primitive', async () => {
  // Arrange
  const { actor, client, primitive } = makeActor();
  // Act
  await actor.setEnabled(true);
  // Assert
  assert.equal(client.calls.length, 1);
  assert.deepEqual(client.calls[0], { datasetRef: 'sample', timeframe: '1D', limit: 1500 });
  assert.deepEqual(primitive.profiles, [PROFILE]);
  assert.deepEqual(primitive.visibles, [true]);
  assert.equal(actor.isEnabled(), true);
});

test('setEnabled(true) attaches the primitive to mainSeries exactly once across repeated enables', async () => {
  // Arrange
  const { actor, mainSeries } = makeActor();
  // Act
  await actor.setEnabled(true);
  await actor.setEnabled(false);
  await actor.setEnabled(true);
  // Assert: 再有効化で二重 attach しない
  assert.equal(mainSeries.attached.length, 1);
});

test('setEnabled(false) hides the primitive and does not fetch', async () => {
  // Arrange
  const { actor, client, primitive } = makeActor();
  // Act
  await actor.setEnabled(false);
  // Assert
  assert.equal(client.calls.length, 0);
  assert.deepEqual(primitive.visibles, [false]);
  assert.equal(actor.isEnabled(), false);
});

test('setEnabled(true) still shows the primitive but skips setProfile when the fetch yields null', async () => {
  // Arrange
  const { actor, primitive } = makeActor({ client: fakeClient(null) });
  // Act
  await actor.setEnabled(true);
  // Assert: null は反映しない（前回描画を保持）が、可視化は行う
  assert.deepEqual(primitive.profiles, []);
  assert.deepEqual(primitive.visibles, [true]);
});

test('refresh() re-fetches and applies the profile only while enabled', async () => {
  // Arrange
  const { actor, client, primitive } = makeActor();
  // Act: 無効時 refresh は no-op
  await actor.refresh();
  assert.equal(client.calls.length, 0);
  // 有効化後 refresh は再取得
  await actor.setEnabled(true);
  await actor.refresh();
  // Assert
  assert.equal(client.calls.length, 2);
  assert.equal(primitive.profiles.length, 2);
});

test('setParams({src}) forwards src to the client on refresh (dwell 切替)', async () => {
  // Arrange
  const { actor, client } = makeActor();
  actor.setParams({ src: 'dwell' });
  // Act: setEnabled(true) は内部で refresh を行う
  await actor.setEnabled(true);
  // Assert: getContext へ src を重畳して client へ渡す
  assert.equal(client.calls[0].src, 'dwell');
});

test('setParams without src leaves src absent on the client context (candle 後方互換)', async () => {
  // Arrange
  const { actor, client } = makeActor();
  actor.setParams({ bins: 24 });
  // Act
  await actor.setEnabled(true);
  // Assert: src 未指定時は context に src キーを載せない（サーバ既定 candle）
  assert.ok(!('src' in client.calls[0]));
});

test('setParams({range}) forwards range to the client on refresh (レンジpt)', async () => {
  // Arrange
  const { actor, client } = makeActor();
  actor.setParams({ range: '50' });
  // Act
  await actor.setEnabled(true);
  // Assert: getContext へ range を重畳して client へ渡す（client が barw へ写像する）
  assert.equal(client.calls[0].range, '50');
});

test('setParams without range leaves range absent on the client context (従来 bins)', async () => {
  // Arrange
  const { actor, client } = makeActor();
  actor.setParams({ bins: 24 });
  // Act
  await actor.setEnabled(true);
  // Assert: range 未指定時は context に range キーを載せない
  assert.ok(!('range' in client.calls[0]));
});

test('does not throw when mainSeries lacks attachPrimitive (legacy series fallback)', async () => {
  // Arrange
  const { actor } = makeActor({ mainSeries: {} });
  // Act / Assert
  await assert.doesNotThrow(async () => { await actor.setEnabled(true); });
});

// ===========================================================================
// リプレイ（増分1）: replay トグル → バー表示/非表示・T スクラブ→当時プロファイル（coalesce）・T 縦線
//   移植元 prototype_260630-01（asofIdx / applyAsofView / scrubProfile coalesce・T 縦線）。
// ===========================================================================

// show/hide 呼び出しを記録する Fake replayBar。
function fakeReplayBar() {
  return { shows: [], setVisible(v) { this.shows.push(!!v); } };
}

// setCursorTime（T 縦線）を記録できるよう primitive を拡張。
function fakeReplayPrimitive() {
  const p = fakePrimitive();
  p.cursors = [];
  p.setCursorTime = function (t) { this.cursors.push(t); };
  return p;
}

function makeReplayActor({ replayBar } = {}) {
  const client = fakeClient();
  const primitive = fakeReplayPrimitive();
  const mainSeries = fakeMainSeries();
  const bar = replayBar ?? fakeReplayBar();
  const actor = new MarketProfileActor({
    client, primitive, mainSeries, replayBar: bar,
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1D', limit: 1500 }),
  });
  return { actor, client, primitive, mainSeries, replayBar: bar };
}

test('setParams({replay:true}) shows the replay bar; replay:false hides it and clears the T cursor', async () => {
  // Arrange
  const { actor, replayBar, primitive } = makeReplayActor();
  await actor.setEnabled(true);
  // Act: replay ON
  actor.setParams({ replay: true });
  // Assert: バー表示
  assert.deepEqual(replayBar.shows.at(-1), true);
  // Act: replay OFF
  actor.setParams({ replay: false });
  // Assert: バー非表示 + T 縦線クリア（null）
  assert.deepEqual(replayBar.shows.at(-1), false);
  assert.equal(primitive.cursors.at(-1), null);
});

test('setReplayCursor(T) fetches with to=T and draws the T vertical line', async () => {
  // Arrange
  const { actor, client, primitive } = makeReplayActor();
  await actor.setEnabled(true);
  actor.setParams({ replay: true }); // スクラブは replay ON 時のみ受け付ける。
  const t = 1277856000;
  // Act
  await actor.setReplayCursor(t);
  // Assert: 当時プロファイルを to=T で再取得し、T 縦線を primitive へ設定する
  assert.equal(client.calls.at(-1).to, t);
  assert.equal(primitive.cursors.at(-1), t);
});

// ===========================================================================
// 増分2: ローリング窓（from）・スナップショット（today/トリム/減光）
//   移植元 prototype_260630-01（asofmode: from=T-ROLL_BARS*bar_sec・asoftrim: today/ローソクトリム/DIM）。
// ===========================================================================

const ROLL_BARS = 60;
const DAY = 86400;

// mode/snapshot を返せる Fake replayBar。
function fakeReplayBar2({ mode = 'anchor', snapshot = false } = {}) {
  return {
    shows: [], _mode: mode, _snapshot: snapshot,
    setVisible(v) { this.shows.push(!!v); },
    setCandles() {},
    mode() { return this._mode; },
    isSnapshot() { return this._snapshot; },
  };
}

// setCandleTrim / setUserInteraction を記録する Fake renderer。
function fakeRenderer() {
  return {
    trims: [], interactions: [],
    setCandleTrim(t) { this.trims.push(t); },
    setUserInteraction(on) { this.interactions.push(!!on); },
  };
}

// setSnapshot を記録できる primitive。
function fakeSnapPrimitive() {
  const p = fakeReplayPrimitive();
  p.snaps = [];
  p.setSnapshot = function (on) { this.snaps.push(!!on); };
  return p;
}

function makeIncr2Actor({ mode = 'anchor', snapshot = false } = {}) {
  const client = fakeClient();
  const primitive = fakeSnapPrimitive();
  const bar = fakeReplayBar2({ mode, snapshot });
  const renderer = fakeRenderer();
  const actor = new MarketProfileActor({
    client, primitive, mainSeries: fakeMainSeries(), replayBar: bar, renderer,
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1D', limit: 1500 }),
  });
  return { actor, client, primitive, replayBar: bar, renderer };
}

test('replay OFF restores chart interaction (setUserInteraction(true)) even mid-swipe', async () => {
  // 回帰: スワイプ捕捉中（setUserInteraction(false)）に gear で replay OFF にしても
  // チャート操作が必ず復元されること（防御・冪等）。
  // Arrange: replay ON → （スワイプ側が interaction を落とした想定）
  const { actor, renderer } = makeIncr2Actor();
  actor.setParams({ replay: true });
  renderer.interactions.length = 0; // ON までの記録はクリア
  // Act: gear で replay OFF
  actor.setParams({ replay: false });
  // Assert: OFF 経路で interaction が true へ復元される
  assert.ok(renderer.interactions.includes(true));
});

test('rolling mode: setReplayCursor(T) adds from = T - ROLL_BARS*bar_sec to the fetch', async () => {
  // Arrange: ローリングモード・1D（bar_sec=86400）
  const { actor, client } = makeIncr2Actor({ mode: 'rolling' });
  await actor.setEnabled(true);
  actor.setParams({ replay: true });
  const T = 1704067200;
  // Act
  await actor.setReplayCursor(T);
  // Assert: from = T - 60*86400、to = T
  const call = client.calls.at(-1);
  assert.equal(call.to, T);
  assert.equal(call.from, T - ROLL_BARS * DAY);
});

test('anchor mode: setReplayCursor(T) omits from (累積・従来)', async () => {
  // Arrange
  const { actor, client } = makeIncr2Actor({ mode: 'anchor' });
  await actor.setEnabled(true);
  actor.setParams({ replay: true });
  // Act
  await actor.setReplayCursor(1704067200);
  // Assert: from を載せない（アンカー＝データ先頭..T の累積）
  assert.ok(!('from' in client.calls.at(-1)));
});

test('snapshot ON: setReplayCursor adds today=true, trims candles to T, marks primitive snapshot', async () => {
  // Arrange
  const { actor, client, primitive, renderer } = makeIncr2Actor({ snapshot: true });
  await actor.setEnabled(true);
  actor.setParams({ replay: true });
  const T = 1704067200;
  // Act
  await actor.setReplayCursor(T);
  // Assert: today=true・ローソクを T までトリム・primitive スナップショット ON
  assert.equal(client.calls.at(-1).today, true);
  assert.equal(renderer.trims.at(-1), T);
  assert.equal(primitive.snaps.at(-1), true);
});

test('snapshot OFF: no today, no candle trim, primitive snapshot false', async () => {
  // Arrange
  const { actor, client, primitive, renderer } = makeIncr2Actor({ snapshot: false });
  await actor.setEnabled(true);
  actor.setParams({ replay: true });
  // Act
  await actor.setReplayCursor(1704067200);
  // Assert
  assert.ok(!('today' in client.calls.at(-1)));
  // トリムは null（全ローソク維持）のみが許される（T トリムはしない）
  assert.ok(!renderer.trims.includes(1704067200));
  assert.equal(primitive.snaps.at(-1), false);
});

test('replay OFF restores candles (setCandleTrim(null)) and clears primitive snapshot', async () => {
  // Arrange
  const { actor, primitive, renderer } = makeIncr2Actor({ snapshot: true });
  await actor.setEnabled(true);
  actor.setParams({ replay: true });
  await actor.setReplayCursor(1704067200);
  // Act: replay OFF
  actor.setParams({ replay: false });
  // Assert: ローソク全復元 + スナップショット解除
  assert.equal(renderer.trims.at(-1), null);
  assert.equal(primitive.snaps.at(-1), false);
});

test('onModeOrSnapshotChange re-fetches at the current cursor T (mode/snapshot トグル反映)', async () => {
  // Arrange: replayBar.onChange → actor.onReplayControlsChange の配線を actor 経由で検証
  const { actor, client } = makeIncr2Actor({ mode: 'anchor' });
  await actor.setEnabled(true);
  actor.setParams({ replay: true });
  const T = 1704067200;
  await actor.setReplayCursor(T);
  const before = client.calls.length;
  // Act: モード/スナップショット変更通知（T は保持）
  await actor.onReplayControlsChange();
  // Assert: 現在 T で再取得する
  assert.equal(client.calls.length, before + 1);
  assert.equal(client.calls.at(-1).to, T);
});

test('setReplayCursor coalesces rapid scrubs: only the last cursor is fetched while in-flight', async () => {
  // Arrange: fetch を「保留 → 明示解決」できる gate client（in-flight を決定論的に作る）。
  //   各 fetch は resolver を配列へ積み、テストが順に解決する（deadlock しない・末尾実行を検証）。
  const calls = [];
  const resolvers = [];
  const client = {
    calls,
    fetchProfile(ctx) {
      calls.push(ctx);
      return new Promise((res) => { resolvers.push(() => res(PROFILE)); });
    },
  };
  const flushNext = async () => { (resolvers.shift() || (() => {}))(); await Promise.resolve(); };
  const primitive = fakeReplayPrimitive();
  const actor = new MarketProfileActor({
    client, primitive, mainSeries: fakeMainSeries(), replayBar: fakeReplayBar(),
    getContext: () => ({ datasetRef: 'jp225_tick' }),
  });
  const enabled = actor.setEnabled(true); // 初回 refresh（to 無し）が pending で積まれる（await しない）。
  await flushNext();            // 初回 refresh を解決。
  await enabled;               // setEnabled 完了（初回 refresh 解決後）。
  actor.setParams({ replay: true }); // スクラブは replay ON 時のみ受け付ける。

  const callsBeforeScrub = calls.length; // 初回 refresh のみ = 1。

  // Act: in-flight 中に 3 連打（10→20→30）。10 が in-flight、20/30 は queue され最後(30)だけ末尾実行。
  actor.setReplayCursor(10);
  actor.setReplayCursor(20);
  actor.setReplayCursor(30);

  // Assert(1): 連打直後（未解決）は 10 の 1 回だけ発火する（20/30 は queue＝coalesce）。
  assert.equal(calls.length, callsBeforeScrub + 1, 'in-flight 中は追加 fetch を発火しない（coalesce）');

  await flushNext();            // 10 の fetch を解決 → queue の末尾（30）が実行される。
  await flushNext();            // 30 の fetch を解決。

  // Assert(2): 解決後に 30（最後）だけ追走する（20 は捨てる）。
  const cursorFetches = calls.filter((c) => c.to != null).map((c) => c.to);
  assert.deepEqual(cursorFetches, [10, 30], 'in-flight 中の連打は最後(30)だけ追走し 20 は捨てる');
});


// ===========================================================================
// sessions（日別プロファイル分割・移植元 prototype_260630-01 drawSessions）
//   setParams({sessions}) を受け、refresh 時に context へ sessions:true を載せる。
//   fetch 後 profile.sessions を primitive.setSessions へ、ローソク透明化を
//   renderer.setCandleTransparency(on) へ委譲する。OFF/無効化で必ず復元する。
// ===========================================================================

// setSessions / setCandleTransparency を記録できる Fake。
function fakeSessPrimitive() {
  const p = fakePrimitive();
  p.sessionsCalls = [];
  p.sessionsTotals = []; // setSessions の第 2 引数（total）を記録する。
  p.setSessions = function (s, total) { this.sessionsCalls.push(s); this.sessionsTotals.push(total); };
  return p;
}
function fakeSessRenderer() {
  return {
    transparencies: [],
    setCandleTransparency(on) { this.transparencies.push(!!on); },
  };
}
const PROFILE_WITH_SESSIONS = {
  ...PROFILE,
  sessions: [{ date: '2024-01-01', tpo: [1] }, { date: '2024-01-02', tpo: [2] }],
  sessions_total: 4146, // キャップ前の実日数（parse が profile へ素通し済み）。
};

function makeSessActor(profile = PROFILE_WITH_SESSIONS) {
  const client = fakeClient(profile);
  const primitive = fakeSessPrimitive();
  const renderer = fakeSessRenderer();
  const actor = new MarketProfileActor({
    client, primitive, mainSeries: fakeMainSeries(), renderer,
    getContext: () => ({ datasetRef: 'sample', timeframe: '1D' }),
  });
  return { actor, client, primitive, renderer };
}

test('sessions ON: setParams({sessions:true}) makes refresh fetch with sessions:true in context', async () => {
  const { actor, client } = makeSessActor();
  actor.setParams({ sessions: true });
  await actor.setEnabled(true);
  const last = client.calls[client.calls.length - 1];
  assert.equal(last.sessions, true, 'context に sessions:true が載る');
});

test('sessions ON: primitive.setSessions receives profile.sessions and renderer transparency turns on', async () => {
  const { actor, primitive, renderer } = makeSessActor();
  actor.setParams({ sessions: true });
  await actor.setEnabled(true);
  const lastSess = primitive.sessionsCalls[primitive.sessionsCalls.length - 1];
  assert.ok(Array.isArray(lastSess) && lastSess.length === 2, 'profile.sessions が primitive へ渡る');
  assert.ok(renderer.transparencies.includes(true), 'ローソク透明化 ON');
});

test('sessions ON: primitive.setSessions receives sessions_total (pre-cap day count) as 2nd arg', async () => {
  // 修正1: actor は profile.sessions_total を primitive.setSessions(list, total) の total へ渡す。
  //   primitive 注記「直近N/全M日」の M をキャップ後長でなく実日数にするため。
  const { actor, primitive } = makeSessActor();
  actor.setParams({ sessions: true });
  await actor.setEnabled(true);
  const lastTotal = primitive.sessionsTotals[primitive.sessionsTotals.length - 1];
  assert.equal(lastTotal, 4146, 'profile.sessions_total が total 引数として primitive へ渡る');
});

test('sessions OFF (default): setSessions(null) and transparency stays off (後方互換)', async () => {
  const { actor, primitive, renderer } = makeSessActor(PROFILE); // sessions 無し profile
  // sessions param を載せない（既定 OFF）。
  await actor.setEnabled(true);
  // primitive へは null（通常モード）が渡る・透明化は ON にしない。
  assert.ok(!primitive.sessionsCalls.includes(undefined));
  assert.ok(primitive.sessionsCalls.every((s) => s === null), 'OFF 時は setSessions(null)');
  assert.ok(!renderer.transparencies.includes(true), 'OFF 時は透明化しない');
});

test('sessions OFF via setParams restores: setSessions(null) + transparency off', async () => {
  const { actor, primitive, renderer } = makeSessActor();
  actor.setParams({ sessions: true });
  await actor.setEnabled(true);
  // Act: sessions を false へ切替え → refresh。
  actor.setParams({ sessions: false });
  await actor.refresh();
  const lastSess = primitive.sessionsCalls[primitive.sessionsCalls.length - 1];
  assert.equal(lastSess, null, 'OFF で通常モードへ復帰（setSessions(null)）');
  assert.equal(renderer.transparencies[renderer.transparencies.length - 1], false, '透明化解除');
});

test('setEnabled(false) restores candle transparency and clears sessions', async () => {
  const { actor, primitive, renderer } = makeSessActor();
  actor.setParams({ sessions: true });
  await actor.setEnabled(true);
  // Act: 無効化（凡例 OFF）。
  await actor.setEnabled(false);
  assert.equal(renderer.transparencies[renderer.transparencies.length - 1], false, 'OFF で透明化解除');
  assert.equal(primitive.sessionsCalls[primitive.sessionsCalls.length - 1], null, 'OFF で sessions クリア');
});

// detach()（凡例からの削除）で sessions のローソク透明化を必ず復元する（防御の明示化・修正3）。
//   setEnabled(false) を経ずに detach() 単独で呼ばれても、ローソクを不透明へ戻して取り残さない。
test('detach() alone restores candle transparency (setCandleTransparency(false))', async () => {
  // Arrange: sessions ON でローソク透明化 ON にしてから detach 単独呼び出し。
  const client = fakeClient(PROFILE_WITH_SESSIONS);
  const primitive = fakeSessPrimitive();
  const renderer = fakeSessRenderer();
  const mainSeries = { attached: [], detached: [], attachPrimitive(p) { this.attached.push(p); }, detachPrimitive(p) { this.detached.push(p); } };
  const actor = new MarketProfileActor({
    client, primitive, mainSeries, renderer, getContext: () => ({ datasetRef: 'sample', timeframe: '1D' }),
  });
  actor.setParams({ sessions: true });
  await actor.setEnabled(true);
  assert.ok(renderer.transparencies.includes(true), '前提: 透明化 ON になっている');
  // Act: detach() 単独（setEnabled(false) を経由しない）。
  actor.detach();
  // Assert: detach 経路で透明化が false へ復元される（取り残さない）。
  assert.equal(renderer.transparencies[renderer.transparencies.length - 1], false, 'detach で透明化解除');
});
