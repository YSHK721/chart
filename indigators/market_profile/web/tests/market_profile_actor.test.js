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
    focusCalls: [],       // focusTimeRange(from, to) の [from, to] 記録。
    sessionMPs: [],
    setCandleTransparency(on) { this.transparencies.push(!!on); },
    focusTimeRange(from, to) { this.focusCalls.push([from, to]); },
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

test('sessions ON: 初回反映で被覆セッション日の時間レンジへ自動ズーム（focusTimeRange を1回・全tf対応）', async () => {
  // 時間軸連動タイルが潰れないよう、sessions 有効化の初回だけ被覆日の時間レンジへ寄せる。以後は寄せない。
  //   時間ベース（focusTimeRange）にすることで、1m（1日=1440本）でも日別列（日境界時刻）が画面内に入る。
  const t1 = Date.UTC(2024, 0, 1) / 1000; // 最古セッション日 2024-01-01
  const t2 = Date.UTC(2024, 0, 2) / 1000; // 最新足 2024-01-02
  const candles = [
    { time: t1, open: 1, high: 1, low: 1, close: 1 },
    { time: t2, open: 1, high: 1, low: 1, close: 1 },
  ];
  const { actor, renderer } = makeSessActor(PROFILE_WITH_SESSIONS, () => candles);
  actor.setParams({ mode: 'sessions' });
  await actor.setEnabled(true);
  assert.deepEqual(renderer.focusCalls, [[t1, t2]], '初回のみ [最古セッション日, 最新足] の時間レンジで focusTimeRange');
  // 再 refresh では寄せない（手動ズーム/スクロールを尊重）。
  await actor.refresh();
  assert.deepEqual(renderer.focusCalls, [[t1, t2]], '2回目以降は focus しない');
});

test('sessions ON: 初回オートズームは直近1年に限定（データが1年超なら from = to - 1年・ISSUE-055）', async () => {
  // 1D で全期間（最大3.6年）を初回に映すと tf-period 一括取得で応答肥大（実測87MB）＝初回表示が重い。
  //   初回は直近1年に寄せ、古い範囲はスクロールで（A案デバウンス＋per-day キャッシュで滑らか）。
  const YEAR = 365 * 86400;
  const to = Date.UTC(2024, 0, 2) / 1000;           // 最新足
  const old = Date.UTC(2021, 0, 1) / 1000;          // 3年前（最古足＝最古セッション日）
  const profile = {
    ...PROFILE,
    sessions: [{ date: '2021-01-01', tpo: [1] }, { date: '2024-01-02', tpo: [2] }],
  };
  const candles = [
    { time: old, open: 1, high: 1, low: 1, close: 1 },
    { time: to, open: 1, high: 1, low: 1, close: 1 },
  ];
  const { actor, renderer } = makeSessActor(profile, () => candles);
  actor.setParams({ mode: 'sessions' });
  await actor.setEnabled(true);
  assert.deepEqual(renderer.focusCalls, [[to - YEAR, to]], '初回 focus は [to-1年, to]（全期間ではなく直近1年）');
});

test('sessions ON + sessionsDrawnByTfPeriod=true: 日別タイルを描かず読取欄は供給・candle 透明化は委譲（ISSUE-055 ちらつき防止）', async () => {
  // tf-period 列が日別を描くモードでは、先に届く sessions 応答でタイルを一瞬描いて列へ差し替える
  //   「日別(candle)→(tf-period)」ちらつきを防ぐため、本 actor はタイルを描かず（setSessions(null)）、
  //   candle 透明化も tf-period 側（列描画時）へ委ねる。読取欄（setSessionMP）は維持する。
  const t1 = Date.UTC(2024, 0, 1) / 1000;
  const t2 = Date.UTC(2024, 0, 2) / 1000;
  const candles = [
    { time: t1, open: 1, high: 1, low: 1, close: 1 },
    { time: t2, open: 1, high: 1, low: 1, close: 1 },
  ];
  const primitive = fakeSessPrimitive();
  const renderer = fakeSessRenderer();
  const actor = new MarketProfileActor({
    client: fakeClient(PROFILE_WITH_SESSIONS), primitive, mainSeries: fakeMainSeries(), renderer,
    getContext: () => ({ datasetRef: 'sample', timeframe: '1D' }),
    getCandles: () => candles,
    sessionsDrawnByTfPeriod: () => true,
  });
  actor.setParams({ mode: 'sessions' });
  await actor.setEnabled(true);
  assert.equal(primitive.sessionsCalls.at(-1), null, 'tfDraws 時は setSessions(null)＝日別タイル非描画');
  assert.ok(renderer.sessionMPs.length >= 1, 'setSessionMP は呼ばれる（読取欄は維持）');
  assert.ok(!renderer.transparencies.includes(true), 'tfDraws 時は actor が candle 透明化(true)を行わない（tf-period へ委譲）');
});

test('sessions 再適用（既に sessions のまま _applyMode(sessions)）は focus を再発火しない（自動FOLLOW復帰での手動ズームリセット防止）', async () => {
  // 実機バグ: 価格更新→自動 FOLLOW 復帰→reapplyMarketProfileMode→setParams(mode:sessions) が pending を
  //   再セットし focusTimeRange が再発火して手動ズームが「全体が初期表示」へリセットされる。再適用では寄せない。
  const t1 = Date.UTC(2024, 0, 1) / 1000;
  const t2 = Date.UTC(2024, 0, 2) / 1000;
  const candles = [
    { time: t1, open: 1, high: 1, low: 1, close: 1 },
    { time: t2, open: 1, high: 1, low: 1, close: 1 },
  ];
  const { actor, renderer } = makeSessActor(PROFILE_WITH_SESSIONS, () => candles);
  actor.setParams({ mode: 'sessions' });
  await actor.setEnabled(true);
  assert.equal(renderer.focusCalls.length, 1, '新規入場で focus 1 回');
  // 既に sessions のまま再適用（reapplyMarketProfileMode 相当）→ 再 refresh でも focus を増やさない。
  actor.setParams({ mode: 'sessions' });
  await actor.refresh();
  assert.equal(renderer.focusCalls.length, 1, '再適用では focus を再発火しない（手動ズーム尊重）');
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

// ---------------------------------------------------------------------------
// 期間パラメータ period（ISSUE-071 (b)案）: 'day' × zp × 通常のとき refresh が from=当日始端を載せる。
// ---------------------------------------------------------------------------

// 当日窓テスト用 actor（getCandles 注入・最新ローソク 1783936560＝2026-07-13 09:56 UTC →
//   セッション日始端 1783890000＝2026-07-12 21:00 UTC・ISSUE-078）。
function makePeriodActor({ candles = [{ time: 1783936560 }] } = {}) {
  const client = fakeClient();
  const primitive = fakePrimitive();
  const actor = new MarketProfileActor({
    client, primitive, mainSeries: fakeMainSeries(),
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1m' }),
    getCandles: () => candles,
  });
  return { actor, client, primitive };
}

test('refresh adds from=当日始端 when period=day and src=zp (通常モード)', async () => {
  // Arrange
  const { actor, client } = makePeriodActor();
  actor.setParams({ src: 'zp', period: 'day' });
  // Act
  await actor.setEnabled(true);
  // Assert: from = sessionDayStart(1783936560) = 1783890000（2026-07-12 21:00 UTC・夏境界）。
  assert.equal(client.calls.at(-1).from, 1783890000);
  assert.equal(client.calls.at(-1).src, 'zp');
});

test('refresh omits from when period=all / period 未設定 (従来 URL 不変)', async () => {
  // Arrange
  const { actor, client } = makePeriodActor();
  actor.setParams({ src: 'zp', period: 'all' });
  // Act
  await actor.setEnabled(true);
  await actor.refresh();
  // Assert: いずれの fetch にも from が無い
  for (const c of client.calls) {
    assert.equal('from' in c, false, 'period=all は from を載せない');
  }
});

test('refresh omits from for src=dwell even when period=day (dwell は対象外)', async () => {
  // Arrange
  const { actor, client } = makePeriodActor();
  actor.setParams({ src: 'dwell', period: 'day' });
  // Act
  await actor.setEnabled(true);
  // Assert
  assert.equal('from' in client.calls.at(-1), false, 'dwell は period を適用しない');
});

test('refresh omits from when candles are unavailable (窓を成さず全期間へ縮退)', async () => {
  // Arrange
  const { actor, client } = makePeriodActor({ candles: [] });
  actor.setParams({ src: 'zp', period: 'day' });
  // Act
  await actor.setEnabled(true);
  // Assert
  assert.equal('from' in client.calls.at(-1), false, 'ローソク未取得は from 無し（非破壊）');
});

// ---------------------------------------------------------------------------
// sessions ビューの日中足対応（ISSUE-072）: UTC 日集計 OHLC ＋ tFirst/tLast 付与。
// ---------------------------------------------------------------------------

test('sessions ON: 日中足 candles はセッション日集計 OHLC と tFirst/tLast を付与する（ISSUE-072/078）', async () => {
  const day = 1704067200; // 2024-01-01 00:00 UTC（冬・セッション境界は 22:00 UTC）
  // 深夜バー無し（01:00/12:00/23:00 の 3 本）。23:00 UTC の足は**翌セッション**（1/2）へ帰属する
  //   （ISSUE-078: 冬境界 22:00 UTC）＝'2024-01-01' セッションには前 2 本のみが束なる。
  const candles = [
    { time: day + 3600, open: 100, high: 105, low: 99, close: 104 },
    { time: day + 43200, open: 104, high: 110, low: 103, close: 108 },
    { time: day + 82800, open: 108, high: 109, low: 95, close: 96 },
  ];
  const client = fakeClient({ ...PROFILE, sessions: [{ date: '2024-01-01', tpo: [1, 2, 0] }] });
  const primitive = {
    profiles: [], visibles: [], sessions: [],
    setProfile(p) { this.profiles.push(p); },
    setVisible(v) { this.visibles.push(v); },
    setSessions(s) { this.sessions.push(s); },
  };
  const actor = new MarketProfileActor({
    client, primitive, mainSeries: fakeMainSeries(),
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1m' }),
    getCandles: () => candles,
  });
  actor.setParams({ mode: 'sessions' });
  await actor.setEnabled(true);
  const lastSess = primitive.sessions.at(-1);
  assert.ok(Array.isArray(lastSess) && lastSess.length === 1);
  // セッション日集計 OHLC: 23:00 UTC の足は翌セッション＝除外（open=初/close=終/high=max/low=min）。
  assert.deepEqual(
    { open: lastSess[0].open, high: lastSess[0].high, low: lastSess[0].low, close: lastSess[0].close },
    { open: 100, high: 110, low: 99, close: 108 },
  );
  // 当日実在バー範囲（primitive の日スパン整列アンカー）＝セッション内の 2 本。
  assert.equal(lastSess[0].tFirst, day + 3600);
  assert.equal(lastSess[0].tLast, day + 43200);
});

// ---------------------------------------------------------------------------
// 表示幅(bp)→barw(pt) 写像（ISSUE-079 二層構造: 計算=1bp固定・見せ方=自由）。
// ---------------------------------------------------------------------------

test('dispbp は最新終値から barw(pt) へ写像され resmode=range として fetch に載る', async () => {
  const client = fakeClient();
  const actor = new MarketProfileActor({
    client, primitive: fakePrimitive(), mainSeries: fakeMainSeries(),
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1h' }),
    getCandles: () => [{ time: 1783890000, close: 67000 }],
  });
  actor.setParams({ dispbp: 3 });
  await actor.setEnabled(true);
  const call = client.calls.at(-1);
  assert.equal(call.resmode, 'range');
  assert.equal(call.range, '20.1'); // 67000 × 3bp/1e4 = 20.1pt。
});

test('legacy 保存の resmode/range があるときは dispbp より優先（後方互換）', async () => {
  const client = fakeClient();
  const actor = new MarketProfileActor({
    client, primitive: fakePrimitive(), mainSeries: fakeMainSeries(),
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1h' }),
    getCandles: () => [{ time: 1783890000, close: 67000 }],
  });
  actor.setParams({ dispbp: 3, resmode: 'range', range: '50' });
  await actor.setEnabled(true);
  const call = client.calls.at(-1);
  assert.equal(call.range, '50'); // legacy 明示値を尊重（dispbp 写像しない）。
});

test('ローソク未取得時は dispbp を写像しない（サーバ既定へ縮退＝非破壊）', async () => {
  const client = fakeClient();
  const actor = new MarketProfileActor({
    client, primitive: fakePrimitive(), mainSeries: fakeMainSeries(),
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1h' }),
    getCandles: () => [],
  });
  actor.setParams({ dispbp: 3 });
  await actor.setEnabled(true);
  const call = client.calls.at(-1);
  assert.equal('resmode' in call, false);
  assert.equal('range' in call, false);
});


// ---------------------------------------------------------------------------
// ISSUE-080: 日別×1m/5m×zp は非対応（代替粒度を出さない・依頼者裁定 2026-07-15）。
// ---------------------------------------------------------------------------

test('sessions×zp×1m は fetch せず表示をクリアする（日タイルへのフォールバック廃止）', async () => {
  const client = fakeClient();
  const primitive = {
    profiles: [], visibles: [], sessions: [],
    setProfile(p) { this.profiles.push(p); },
    setVisible(v) { this.visibles.push(v); },
    setSessions(s) { this.sessions.push(s); },
  };
  const transparencies = [];
  const renderer = {
    setCandleTransparency(on) { transparencies.push(on); },
    setSessionMP() {},
  };
  const actor = new MarketProfileActor({
    client, primitive, mainSeries: fakeMainSeries(), renderer,
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1m' }),
    getCandles: () => [{ time: 1783890000, close: 67000 }],
  });
  actor.setParams({ src: 'zp', mode: 'sessions' });
  await actor.setEnabled(true);
  assert.equal(client.calls.length, 0, '非対応組合せは /market_profile を叩かない');
  assert.equal(primitive.sessions.at(-1), null, '日別タイルは描かない（代替なし）');
  assert.equal(transparencies.at(-1), false, 'ローソクは可視のまま');
});

// ISSUE-083（日別プロファイルのライブ育成）: 日別×tf-period 描画×growing（FOLLOW）の refresh は
//   onSessionsLiveGrow フックを発火し、tf-period 側が当日列を再取得して育てる。static（ANALYSIS＝
//   growing=false）・通常モードでは発火しない（既存の成長軸と整合）。
test('sessions×tfDraws×growing: refresh が onSessionsLiveGrow を発火する（ISSUE-083）', async () => {
  const t1 = Date.UTC(2024, 0, 1) / 1000;
  const candles = [{ time: t1, open: 1, high: 1, low: 1, close: 1 }];
  let grows = 0;
  const actor = new MarketProfileActor({
    client: fakeClient(PROFILE_WITH_SESSIONS), primitive: fakeSessPrimitive(),
    mainSeries: fakeMainSeries(), renderer: fakeSessRenderer(),
    getContext: () => ({ datasetRef: 'sample', timeframe: '1D' }),
    getCandles: () => candles,
    sessionsDrawnByTfPeriod: () => true,
    onSessionsLiveGrow: () => { grows += 1; },
  });
  actor.setParams({ mode: 'sessions' });
  await actor.setEnabled(true);
  // static（growing=false）では発火しない。
  assert.equal(grows, 0, 'static（ANALYSIS）では育成フックを発火しない');
  // growing（FOLLOW）へ遷移 → live tick 経路（onLiveTick→refresh）で発火する。
  actor.applyGrowthState({ growing: true });
  await actor.onLiveTick();
  assert.equal(grows, 1, 'growing の sessions×tfDraws refresh で発火');
  await actor.onLiveTick();
  assert.equal(grows, 2, 'live tick ごとに発火（throttle は tf-period 側の責務）');
});

test('onSessionsLiveGrow: 通常モード・tfDraws=false では発火しない（ISSUE-083）', async () => {
  const t1 = Date.UTC(2024, 0, 1) / 1000;
  const candles = [{ time: t1, open: 1, high: 1, low: 1, close: 1 }];
  let grows = 0;
  let tfDraws = false;
  const actor = new MarketProfileActor({
    client: fakeClient(PROFILE_WITH_SESSIONS), primitive: fakeSessPrimitive(),
    mainSeries: fakeMainSeries(), renderer: fakeSessRenderer(),
    getContext: () => ({ datasetRef: 'sample', timeframe: '1D' }),
    getCandles: () => candles,
    sessionsDrawnByTfPeriod: () => tfDraws,
    onSessionsLiveGrow: () => { grows += 1; },
  });
  // 通常モード×growing → 発火しない（通常の成長は forming/refresh 経路が担う）。
  actor.setParams({ mode: 'normal' });
  await actor.setEnabled(true);
  actor.applyGrowthState({ growing: true });
  await actor.onLiveTick();
  assert.equal(grows, 0, '通常モードでは発火しない');
  // sessions だが tfDraws=false（タイル自前描画フォールバック）→ 発火しない（列アクター不在）。
  actor.setParams({ mode: 'sessions' });
  await actor.refresh();
  assert.equal(grows, 0, 'tfDraws=false では発火しない');
});
