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
  actor.setParams({ mode: 'replay' });
  // Assert: バー表示
  assert.deepEqual(replayBar.shows.at(-1), true);
  // Act: replay OFF
  actor.setParams({ mode: 'normal' });
  // Assert: バー非表示 + T 縦線クリア（null）
  assert.deepEqual(replayBar.shows.at(-1), false);
  assert.equal(primitive.cursors.at(-1), null);
});

test('setReplayCursor(T) fetches with to=T and draws the T vertical line', async () => {
  // Arrange
  const { actor, client, primitive } = makeReplayActor();
  await actor.setEnabled(true);
  actor.setParams({ mode: 'replay' }); // スクラブは replay ON 時のみ受け付ける。
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

// mode/snapshot を返せる Fake replayBar。currentTime は既定=最新（スライダ右端）を模す。
function fakeReplayBar2({ mode = 'anchor', snapshot = false, currentTime = 1704067200 } = {}) {
  return {
    shows: [], _mode: mode, _snapshot: snapshot, _currentTime: currentTime,
    setVisible(v) { this.shows.push(!!v); },
    setCandles() {},
    mode() { return this._mode; },
    isSnapshot() { return this._snapshot; },
    currentTime() { return this._currentTime; },
  };
}

// setCandleTrim / setUserInteraction を記録する Fake renderer。
function fakeRenderer() {
  return {
    trims: [], interactions: [], margins: [],
    setCandleTrim(t) { this.trims.push(t); },
    setUserInteraction(on) { this.interactions.push(!!on); },
    setRightMarginFraction(f) { this.margins.push(f); },
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

test('replay ON: スクラブ前でも初期カーソル（最新）で T 縦線を即描画する（setCursorTime(最新)）', async () => {
  // 回帰: スライダ右端（最新）から始まるため、スクラブ前でも線が出る（ユーザFB）。
  const { actor, primitive } = makeIncr2Actor();
  actor.setParams({ mode: 'replay' });
  // Assert: primitive.setCursorTime が最新 T（fakeReplayBar2 の currentTime=1704067200）で呼ばれる。
  assert.ok(primitive.cursors.includes(1704067200), '初期カーソル＝最新で T 縦線描画');
});

test('onReplayControlsChange: スクラブ前スナップショットONでも currentTime で当時 T を確定し fetch する', async () => {
  // Arrange: replay ON、スクラブしていない（_replayTo は初期カーソルで最新に入る）。
  const { actor, client } = makeIncr2Actor({ snapshot: true });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'replay' });
  client.calls.length = 0;
  // Act: スナップショット onChange 相当（スクラブ無し）。
  await actor.onReplayControlsChange();
  // Assert: currentTime(=最新) を to にして当時取得する（no-op で終わらない）。
  assert.ok(client.calls.length >= 1, 'fetch が発火する');
  assert.equal(client.calls.at(-1).to, 1704067200, 'to=最新（currentTime）で当時プロファイル取得');
});

test('replay OFF restores chart interaction (setUserInteraction(true)) even mid-swipe', async () => {
  // 回帰: スワイプ捕捉中（setUserInteraction(false)）に gear で replay OFF にしても
  // チャート操作が必ず復元されること（防御・冪等）。
  // Arrange: replay ON → （スワイプ側が interaction を落とした想定）
  const { actor, renderer } = makeIncr2Actor();
  actor.setParams({ mode: 'replay' });
  renderer.interactions.length = 0; // ON までの記録はクリア
  // Act: gear で replay OFF
  actor.setParams({ mode: 'normal' });
  // Assert: OFF 経路で interaction が true へ復元される
  assert.ok(renderer.interactions.includes(true));
});

test('rolling mode: setReplayCursor(T) adds from = T - ROLL_BARS*bar_sec to the fetch', async () => {
  // Arrange: ローリングモード・1D（bar_sec=86400）
  const { actor, client } = makeIncr2Actor({ mode: 'rolling' });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'replay' });
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
  actor.setParams({ mode: 'replay' });
  // Act
  await actor.setReplayCursor(1704067200);
  // Assert: from を載せない（アンカー＝データ先頭..T の累積）
  assert.ok(!('from' in client.calls.at(-1)));
});

test('snapshot ON: setReplayCursor adds today=true, trims candles to T, marks primitive snapshot', async () => {
  // Arrange
  const { actor, client, primitive, renderer } = makeIncr2Actor({ snapshot: true });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'replay' });
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
  actor.setParams({ mode: 'replay' });
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
  actor.setParams({ mode: 'replay' });
  await actor.setReplayCursor(1704067200);
  // Act: replay OFF
  actor.setParams({ mode: 'normal' });
  // Assert: ローソク全復元 + スナップショット解除
  assert.equal(renderer.trims.at(-1), null);
  assert.equal(primitive.snaps.at(-1), false);
});

test('onModeOrSnapshotChange re-fetches at the current cursor T (mode/snapshot トグル反映)', async () => {
  // Arrange: replayBar.onChange → actor.onReplayControlsChange の配線を actor 経由で検証
  const { actor, client } = makeIncr2Actor({ mode: 'anchor' });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'replay' });
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
  actor.setParams({ mode: 'replay' }); // スクラブは replay ON 時のみ受け付ける。

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
    focusCalls: [],
    sessionMPs: [],
    setCandleTransparency(on) { this.transparencies.push(!!on); },
    focusRecentBars(n) { this.focusCalls.push(n); },
    setSessionMP(map) { this.sessionMPs.push(map); },
  };
}
const PROFILE_WITH_SESSIONS = {
  ...PROFILE,
  sessions: [{ date: '2024-01-01', tpo: [1] }, { date: '2024-01-02', tpo: [2] }],
  sessions_total: 4146, // キャップ前の実日数（parse が profile へ素通し済み）。
};

function makeSessActor(profile = PROFILE_WITH_SESSIONS, getCandles = null) {
  const client = fakeClient(profile);
  const primitive = fakeSessPrimitive();
  const renderer = fakeSessRenderer();
  const actor = new MarketProfileActor({
    client, primitive, mainSeries: fakeMainSeries(), renderer,
    getContext: () => ({ datasetRef: 'sample', timeframe: '1D' }),
    ...(getCandles ? { getCandles } : {}),
  });
  return { actor, client, primitive, renderer };
}

test('sessions ON: 各セッションへ candle の OHLC を付与して primitive へ渡す（date→time 突合）', async () => {
  // date 'YYYY-MM-DD' → UTC 深夜秒で candle.time と突合し o/h/l/c を付与する。
  const t1 = Date.UTC(2024, 0, 1) / 1000; // 2024-01-01
  const t2 = Date.UTC(2024, 0, 2) / 1000; // 2024-01-02
  const candles = [
    { time: t1, open: 100, high: 110, low: 95, close: 108 },
    { time: t2, open: 108, high: 112, low: 104, close: 106 },
  ];
  const { actor, primitive } = makeSessActor(PROFILE_WITH_SESSIONS, () => candles);
  actor.setParams({ mode: 'sessions' });
  await actor.setEnabled(true);
  const lastSess = primitive.sessionsCalls[primitive.sessionsCalls.length - 1];
  assert.deepEqual(
    { open: lastSess[0].open, high: lastSess[0].high, low: lastSess[0].low, close: lastSess[0].close },
    { open: 100, high: 110, low: 95, close: 108 },
    '2024-01-01 のセッションへ当日 OHLC が付与される',
  );
  assert.equal(lastSess[1].close, 106, '2024-01-02 も付与');
});

test('sessions ON: setParams({sessions:true}) makes refresh fetch with sessions:true in context', async () => {
  const { actor, client } = makeSessActor();
  actor.setParams({ mode: 'sessions' });
  await actor.setEnabled(true);
  const last = client.calls[client.calls.length - 1];
  assert.equal(last.sessions, true, 'context に sessions:true が載る');
});

test('sessions ON: primitive.setSessions receives profile.sessions and renderer transparency turns on', async () => {
  const { actor, primitive, renderer } = makeSessActor();
  actor.setParams({ mode: 'sessions' });
  await actor.setEnabled(true);
  const lastSess = primitive.sessionsCalls[primitive.sessionsCalls.length - 1];
  assert.ok(Array.isArray(lastSess) && lastSess.length === 2, 'profile.sessions が primitive へ渡る');
  assert.ok(renderer.transparencies.includes(true), 'ローソク透明化 ON');
});

test('sessions ON: backend 提供の POC/VA を time→mp Map で renderer.setSessionMP へ写す（VA は backend 単一定義）', async () => {
  // VA/POC は backend が _value_area で算出済（poc/va_low/va_high）。actor は time で引ける Map へ写すだけ。
  const t1 = Date.UTC(2024, 0, 1) / 1000;
  const profile = {
    bins: [{ price: 100 }], poc: 100, va_low: 100, va_high: 100,
    sessions: [{ date: '2024-01-01', tpo: [1], poc: 101, va_low: 100, va_high: 102 }],
  };
  const { actor, renderer } = makeSessActor(profile);
  actor.setParams({ mode: 'sessions' });
  await actor.setEnabled(true);
  const map = renderer.sessionMPs.at(-1);
  assert.ok(map && typeof map.get === 'function', 'setSessionMP に Map を渡す');
  // backend の va_high→vah / va_low→val へ写す。
  assert.deepEqual(map.get(t1), { poc: 101, vah: 102, val: 100 }, '当日 POC/VAH/VAL（backend 値）');
});

test('sessions ON: セッションに poc/va が無ければ MP Map に載せない（後方互換）', async () => {
  const profile = {
    bins: [{ price: 100 }], poc: 100, va_low: 100, va_high: 100,
    sessions: [{ date: '2024-01-01', tpo: [1] }], // poc/va 無し。
  };
  const { actor, renderer } = makeSessActor(profile);
  actor.setParams({ mode: 'sessions' });
  await actor.setEnabled(true);
  const map = renderer.sessionMPs.at(-1);
  assert.equal(map.size, 0, 'poc/va 無しのセッションは MP に載らない');
});

test('sessions OFF: setSessionMP(null) で当日 MP 読み取りを解除する', async () => {
  const { actor, renderer } = makeSessActor();
  actor.setParams({ mode: 'sessions' });
  await actor.setEnabled(true);
  renderer.sessionMPs.length = 0;
  actor.setParams({ mode: 'normal' });
  await actor.refresh();
  assert.ok(renderer.sessionMPs.includes(null), 'sessions OFF で null 供給（読み取り解除）');
});

test('sessions ON: 初回反映で直近セッション日へ自動ズーム（focusRecentBars(sessions長)を1回）', async () => {
  // 時間軸連動タイルが潰れないよう、sessions 有効化の初回だけ直近 N 日へ寄せる。以後は寄せない。
  const { actor, renderer } = makeSessActor();
  actor.setParams({ mode: 'sessions' });
  await actor.setEnabled(true);
  assert.deepEqual(renderer.focusCalls, [2], '初回のみ sessions.length=2 で focusRecentBars');
  // 再 refresh では寄せない（手動ズーム/スクロールを尊重）。
  await actor.refresh();
  assert.deepEqual(renderer.focusCalls, [2], '2回目以降は focus しない');
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
  actor.setParams({ mode: 'sessions' });
  await actor.setEnabled(true);
  // Act: sessions を false へ切替え → refresh。
  actor.setParams({ mode: 'normal' });
  await actor.refresh();
  const lastSess = primitive.sessionsCalls[primitive.sessionsCalls.length - 1];
  assert.equal(lastSess, null, 'OFF で通常モードへ復帰（setSessions(null)）');
  assert.equal(renderer.transparencies[renderer.transparencies.length - 1], false, '透明化解除');
});

test('setEnabled(false) restores candle transparency and clears sessions', async () => {
  const { actor, primitive, renderer } = makeSessActor();
  actor.setParams({ mode: 'sessions' });
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
  actor.setParams({ mode: 'sessions' });
  await actor.setEnabled(true);
  assert.ok(renderer.transparencies.includes(true), '前提: 透明化 ON になっている');
  // Act: detach() 単独（setEnabled(false) を経由しない）。
  actor.detach();
  // Assert: detach 経路で透明化が false へ復元される（取り残さない）。
  assert.equal(renderer.transparencies[renderer.transparencies.length - 1], false, 'detach で透明化解除');
});

// ---------------------------------------------------------------------------
// MP 右マージン（試作 PROFILE_FRAC・重なり回避）: setEnabled(true)→0.30 適用、
//   setEnabled(false)/detach→null（復元）。
// ---------------------------------------------------------------------------
test('setEnabled(true) applies the profile right margin (0.30) and OFF/detach restores it', async () => {
  const { actor, renderer } = makeIncr2Actor();
  await actor.setEnabled(true);
  assert.equal(renderer.margins.at(-1), 0.30, 'ON で右マージン 0.30');
  await actor.setEnabled(false);
  assert.equal(renderer.margins.at(-1), null, 'OFF で復元(null)');
  await actor.setEnabled(true);
  actor.detach();
  assert.equal(renderer.margins.at(-1), null, 'detach でも復元(null)');
});

// ===========================================================================
// 表示モード統合（mode: normal | replay | sessions）の排他遷移
//   旧 replay(BOOL)/sessions(BOOL) の 2 トグルを 1 つの排他 mode ENUM へ統合。
//   同時 ON が構造的に不可能で、モード切替時に他モードの残留物がゼロになることを検証する。
//   legacy replay/sessions も受理し続ける（mode との競合時は mode 優先）。
// ===========================================================================

// replayBar・snapshot・sessions・renderer の全副作用面を記録する統合 Fake。
function fakeModePrimitive() {
  const p = fakeSessPrimitive();
  p.cursors = [];
  p.snaps = [];
  p.setCursorTime = function (t) { this.cursors.push(t); };
  p.setSnapshot = function (on) { this.snaps.push(!!on); };
  return p;
}
function fakeModeReplayBar() {
  return {
    shows: [], _mode: 'anchor', _snapshot: false,
    setVisible(v) { this.shows.push(!!v); },
    setCandles() {},
    mode() { return this._mode; },
    isSnapshot() { return this._snapshot; },
  };
}
function fakeModeRenderer() {
  return {
    trims: [], interactions: [], margins: [], transparencies: [],
    setCandleTrim(t) { this.trims.push(t); },
    setUserInteraction(on) { this.interactions.push(!!on); },
    setRightMarginFraction(f) { this.margins.push(f); },
    setCandleTransparency(on) { this.transparencies.push(!!on); },
  };
}
function makeModeActor(profile = PROFILE_WITH_SESSIONS) {
  const client = fakeClient(profile);
  const primitive = fakeModePrimitive();
  const replayBar = fakeModeReplayBar();
  const renderer = fakeModeRenderer();
  const actor = new MarketProfileActor({
    client, primitive, mainSeries: fakeMainSeries(), replayBar, renderer,
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1D', limit: 1500 }),
  });
  return { actor, client, primitive, replayBar, renderer };
}

test("mode='replay' shows the replay bar and turns sessions OFF", async () => {
  const { actor, replayBar, renderer } = makeModeActor();
  await actor.setEnabled(true);
  // Act
  actor.setParams({ mode: 'replay' });
  // Assert: リプレイバー表示・sessions は OFF（透明化しない）
  assert.equal(replayBar.shows.at(-1), true, 'リプレイバー表示');
  assert.equal(actor.isReplay(), true, 'replay ON');
  assert.equal(actor.isSessions(), false, 'sessions は OFF');
  assert.equal(renderer.transparencies.at(-1) ?? false, false, 'ローソク透明化は ON にならない');
});

test("mode='sessions' turns sessions ON and clears all replay residue (bar/cursor/trim)", async () => {
  const { actor, client, primitive, replayBar, renderer } = makeModeActor();
  await actor.setEnabled(true);
  // Arrange: 一度 replay に入れてカーソル/トリムを残す
  actor.setParams({ mode: 'replay' });
  await actor.setReplayCursor(1704067200);
  // Act: sessions へ切替（排他）
  actor.setParams({ mode: 'sessions' });
  await actor.refresh();
  // Assert: sessions ON
  assert.equal(actor.isSessions(), true, 'sessions ON');
  // replay 一式解除: バー非表示・カーソル null・トリム null・snapshot off・replay=false
  assert.equal(actor.isReplay(), false, 'replay OFF');
  assert.equal(replayBar.shows.at(-1), false, 'リプレイバー非表示');
  assert.equal(primitive.cursors.at(-1), null, 'T 縦線カーソルは null');
  assert.equal(renderer.trims.at(-1), null, 'ローソクトリムは null');
  assert.equal(primitive.snaps.at(-1), false, 'snapshot は解除');
});

test("mode='normal' turns both replay and sessions OFF (両 OFF 復元)", async () => {
  const { actor, primitive, replayBar, renderer } = makeModeActor();
  await actor.setEnabled(true);
  actor.setParams({ mode: 'sessions' });
  await actor.refresh();
  // Act
  actor.setParams({ mode: 'normal' });
  await actor.refresh();
  // Assert: 両 OFF・残留物ゼロ
  assert.equal(actor.isReplay(), false, 'replay OFF');
  assert.equal(actor.isSessions(), false, 'sessions OFF');
  assert.equal(replayBar.shows.at(-1), false, 'リプレイバー非表示');
  assert.equal(primitive.sessionsCalls.at(-1), null, 'sessions は null（通常モード）');
  assert.equal(renderer.transparencies.at(-1), false, 'ローソク透明化解除');
});

test("switching replay → sessions leaves zero replay residue (トリム/透明化の復元)", async () => {
  const { actor, primitive, renderer } = makeModeActor();
  await actor.setEnabled(true);
  // Arrange: replay 中でカーソル・トリムを残す
  actor.setParams({ mode: 'replay' });
  await actor.setReplayCursor(1704067200);
  // Act: sessions へ切替 → 残留ゼロを確認
  actor.setParams({ mode: 'sessions' });
  await actor.refresh();
  // Assert: リプレイ側の残留がない
  assert.equal(primitive.cursors.at(-1), null, 'T 縦線カーソル残留なし');
  assert.equal(renderer.trims.at(-1), null, 'トリム残留なし');
  // Assert: sessions 側は正常に ON（透明化 ON・sessions リスト反映）
  assert.ok(renderer.transparencies.includes(true), 'sessions で透明化 ON');
  const lastSess = primitive.sessionsCalls.at(-1);
  assert.ok(Array.isArray(lastSess), 'sessions リストが primitive へ反映');
});

test("switching sessions → replay clears sessions residue (setSessions(null)・透明化解除)", async () => {
  const { actor, primitive, renderer } = makeModeActor();
  await actor.setEnabled(true);
  // Arrange: sessions 中
  actor.setParams({ mode: 'sessions' });
  await actor.refresh();
  // Act: replay へ切替（排他）
  actor.setParams({ mode: 'replay' });
  // Assert: sessions 一式解除
  assert.equal(primitive.sessionsCalls.at(-1), null, 'setSessions(null)');
  assert.equal(renderer.transparencies.at(-1), false, '透明化解除');
  assert.equal(actor.isReplay(), true, 'replay ON');
});

// legacy 受理: 旧 replay:true / sessions:true も引き続き受理する（mode 前提移行の後方互換・1 本）。
test('legacy setParams({replay:true}) still shows the replay bar (後方互換受理)', async () => {
  const { actor, replayBar } = makeModeActor();
  await actor.setEnabled(true);
  // Act: legacy キー（mode ではなく replay）
  actor.setParams({ replay: true });
  // Assert
  assert.equal(replayBar.shows.at(-1), true, 'legacy replay:true でバー表示');
  assert.equal(actor.isReplay(), true, 'legacy でも replay ON');
});

test('mode wins over conflicting legacy flags in setParams (mode 優先)', async () => {
  const { actor } = makeModeActor();
  await actor.setEnabled(true);
  // Act: mode='replay' と legacy sessions:true が競合
  actor.setParams({ mode: 'replay', sessions: true });
  // Assert: mode 優先で replay ON・sessions OFF
  assert.equal(actor.isReplay(), true, 'mode=replay 優先');
  assert.equal(actor.isSessions(), false, 'legacy sessions は無視される');
});
