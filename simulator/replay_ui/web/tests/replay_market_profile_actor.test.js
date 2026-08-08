// replay_market_profile_actor.js（ReplayMarketProfileActor extends 共有 MarketProfileActor）の検証。
//
// 設計入力: mp_full_modes_design.md「ReplayMarketProfileActor extends 共有 MarketProfileActor」。
//   基底（present full MarketProfileActor）の normal/sessions/replay/setParams/_applyMode/refresh を再利用し、
//   reveal 差（push 駆動 ticklive・因果 as-of）だけを override/追加する:
//     - onLiveTick() override: isTicklive() なら no-op（push=enterBar/feedTick が駆動）／他は super.refresh()
//         （as-of-T＝getContext().to）。pull/push 二重駆動を遮断する。
//     - enterBar(now) 追加（slim 実装移設）: 先頭 self-guard `if(!isTicklive()) return;`。base=1/src=dwell/
//         now=T/from=当日始まり で forming 取得 → accumulator.init（tick 畳まず）→ base 描画・await ready。
//     - feedTick(sec,mid)/settleTick() 追加（slim 移設・throttle）。
//     - _buildFormingArgs override: 基底に無い now(=getContext().to) と from(=当日始まり) を合流。
//   override は subclass インスタンス限定（JS プロトタイプ継承）＝present 別インスタンスに波及しない。
//
// ★この時点で replay_market_profile_actor.js は未実装（Red）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ReplayMarketProfileActor } from '../js/adapter/front/replay_market_profile_actor.js';
import { MarketProfileActor } from '../js/adapter/front/market_profile_actor.js';
import { DwellAccumulator } from '../js/domain/market_profile_dwell_accumulator.js';
import { sessionDayStart } from '../js/domain/session_day.js';
// ISSUE-260: VA 比率の既定は Python 唯一源の生成物（テストも第 2 定義を持たない）。
import { VA_PCT_DEFAULT } from '../js/domain/mp_param_defaults_generated.js';

const BASE_FULL = {
  ok: true, formingStart: 1000, ticks: [],
  baseFine: [0, 0, 0], baseKmin: 100, activeTable: [[1]], priceMin: 1000, priceMax: 1100,
  nBins: 3, gridW: 10, vaPct: VA_PCT_DEFAULT, now: 1030,
};

function fakePrimitive() {
  return {
    profiles: [], visibles: [], cursors: [], snapshots: [], sessions: [],
    setProfile(p) { this.profiles.push(p); },
    setVisible(v) { this.visibles.push(v); },
    setCursorTime(t) { this.cursors.push(t); },
    setSnapshot(v) { this.snapshots.push(v); },
    setSessions(s) { this.sessions.push(s); },
  };
}

function fakeMainSeries() {
  return { attachCount: 0, attachPrimitive() { this.attachCount += 1; } };
}

function fakeFormingClient(responses) {
  const calls = [];
  let i = 0;
  return {
    calls,
    async fetchForming(args) { calls.push(args); const r = responses[Math.min(i, responses.length - 1)]; i += 1; return r; },
  };
}

// fetchProfile（refresh 用）を記録する fake client。
function fakeClient(profile = { bins: [], poc: 1 }) {
  const calls = [];
  return {
    calls,
    async fetchProfile(ctx) { calls.push(ctx); return profile; },
  };
}

function fakeAccumulatorFactory() {
  const created = [];
  const make = () => {
    const acc = {
      init(cfg) { this.cfg = cfg; this.ticks = []; },
      addTick(sec, mid) { this.ticks.push([sec, mid]); },
      snapshot() { return { poc: this.ticks.length, bins: [], _snap: true, ticks: this.ticks.length }; },
    };
    created.push(acc);
    return acc;
  };
  return { make, created };
}

function fakeClock(seq) {
  let i = 0;
  return () => seq[Math.min(i++, seq.length - 1)];
}

function makeActor({
  formingClient, makeAccumulator, primitive, mainSeries, client, now, throttleMs = 120, ctxTo,
  getCandles, ctxTimeframe = '1h',
} = {}) {
  const p = primitive ?? fakePrimitive();
  const ms = mainSeries ?? fakeMainSeries();
  const c = client ?? fakeClient();
  const actor = new ReplayMarketProfileActor({
    client: c,
    primitive: p,
    mainSeries: ms,
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: ctxTimeframe, to: ctxTo }),
    formingClient,
    makeAccumulator,
    getCandles,
    now: now ?? (() => 0),
    throttleMs,
  });
  return { actor, primitive: p, mainSeries: ms, client: c };
}

// --- 継承関係: 基底の全モード駆動を再利用する ---
test('ReplayMarketProfileActor is a subclass of the shared MarketProfileActor', () => {
  const { actor } = makeActor({ formingClient: fakeFormingClient([BASE_FULL]), makeAccumulator: fakeAccumulatorFactory().make });
  assert.ok(actor instanceof MarketProfileActor, '共有 MarketProfileActor を継承する（fork ではない）');
  // 基底の全モード駆動 API を継承している（複製せず再利用）。
  for (const m of ['setParams', 'refresh', 'setReplayCursor', 'setEnabled', '_applyMode', 'isSessions', 'isReplay']) {
    assert.equal(typeof actor[m], 'function', `基底 ${m} を継承`);
  }
});

// --- onLiveTick override: ticklive は no-op（push 駆動）、非ticklive は super.refresh（as-of-T） ---
test('onLiveTick is a no-op when ticklive (push=enterBar/feedTick drives it — no pull double-drive)', async () => {
  const client = fakeClient();
  const { actor } = makeActor({
    formingClient: fakeFormingClient([BASE_FULL]), makeAccumulator: fakeAccumulatorFactory().make,
    client, ctxTo: 1000,
  });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  const before = client.calls.length;
  // Act
  await actor.onLiveTick();
  // Assert: ticklive では onLiveTick は fetch しない（pull を遮断・push が育てる）。
  assert.equal(actor.isTicklive(), true);
  assert.equal(client.calls.length, before, 'ticklive の onLiveTick は fetchProfile を呼ばない（no-op）');
});

test('onLiveTick delegates to refresh (as-of-T) when NOT ticklive (normal/sessions/replay auto-drive)', async () => {
  const client = fakeClient();
  const { actor } = makeActor({
    formingClient: fakeFormingClient([BASE_FULL]), makeAccumulator: fakeAccumulatorFactory().make,
    client, ctxTo: 1704074400,
  });
  await actor.setEnabled(true);       // 既定 normal（非ticklive）
  const before = client.calls.length;
  // Act
  await actor.onLiveTick();
  // Assert: 非ticklive は super.refresh（fetchProfile）を呼び、getContext().to=T が as-of として載る（因果）。
  assert.equal(actor.isTicklive(), false);
  assert.equal(client.calls.length, before + 1, '非ticklive の onLiveTick は refresh 経由で fetchProfile を呼ぶ');
  assert.equal(client.calls[client.calls.length - 1].to, 1704074400, 'as-seen-at-t: getContext().to=T が載る');
});

// --- enterBar 自己ガード: 非ticklive では no-op（既存フック不変で誤駆動しない） ---
test('enterBar is a no-op when NOT ticklive (self-guard — existing render hook stays inert)', async () => {
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor } = makeActor({ formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make, ctxTo: 1000 });
  await actor.setEnabled(true); // normal（非ticklive）
  // Act
  await actor.enterBar(1000);
  // Assert: 自己ガードで forming を取得しない（normal/sessions/replay は refresh 経路が駆動）。
  assert.equal(forming.calls.length, 0, '非ticklive の enterBar は self-guard で no-op');
});

// --- _buildFormingArgs override: now(=getContext().to) と GrowthWindow 委譲（絞った窓・視認性） ---
//   視認性修正: normal 再生=全期間累積だと 1 本ぶんの成長が極小で見えないため、GrowthWindow(normal) の
//   from を「絞った窓」min(当日始まり, formingStart) へ戻す（ユーザー確定）。日中足は当日始端が base 下限。
test('_buildFormingArgs merges now(=getContext().to) and delegates from to GrowthWindow (normal→絞った窓 当日始端)', () => {
  const to = 1782985000; // 日途中
  const daySt = sessionDayStart(to); // 当日始端（セッション日=NY17:00 ET 基準・ISSUE-078）
  const { actor } = makeActor({ formingClient: fakeFormingClient([BASE_FULL]), makeAccumulator: fakeAccumulatorFactory().make, ctxTo: to });
  actor.setParams({ mode: 'ticklive', bins: '30', va: 0.9 });
  // Act
  const args = actor._buildFormingArgs({ base: 1, since: null });
  // Assert: 基底 src=dwell/base/since + now(=to) + params。from は GrowthWindow(normal,tf,to)=
  //   min(当日始まり, formingStart)＝日中足では当日始端（視認性・全期間累積を撤廃）。
  assert.equal(args.src, 'dwell');
  assert.equal(args.base, 1);
  assert.equal(args.now, to, 'now=getContext().to（因果 T）');
  assert.equal(args.from, daySt, 'normal は絞った窓＝当日始端を base 下限（min(当日,formingStart)）');
  assert.equal(args.bins, '30');
  assert.equal(args.va, 0.9);
});

// --- 明示 from（compat）: _buildFormingArgs に from を渡すと GrowthWindow より優先し温存する ---
test('_buildFormingArgs preserves an explicit from over GrowthWindow (backward-compat)', () => {
  const to = 1782985000;
  const { actor } = makeActor({ formingClient: fakeFormingClient([BASE_FULL]), makeAccumulator: fakeAccumulatorFactory().make, ctxTo: to });
  actor.setParams({ mode: 'ticklive' });
  // Act: 明示 from を渡す（呼び出し側が窓を指定するケースの互換温存）。
  const args = actor._buildFormingArgs({ base: 1, since: null, from: 123456 });
  // Assert
  assert.equal(args.from, 123456, '明示 from は GrowthWindow 委譲より優先（compat）');
});

// --- 非退行（present 当日窓の波及遮断）: 基底 MarketProfileActor._buildFormingArgs が present 用に
//   getCandles(最新ローソク time) から from=当日始端 を載せるようになっても、replay subclass の override は
//   super の後に自前 from（GrowthWindow(mode,tf,effNow=getContext().to)）を必ず再設定するため、基底変更が
//   replay の from へ波及しない（override 優先＝実測固定）。getCandles を ctxTo と別日で注入し、subclass 出力が
//   GrowthWindow(ctxTo) 由来（getCandles 由来ではない）であることを検証する。 ---
test('replay override wins: base present-window (getCandles) does NOT leak into replay _buildFormingArgs from', () => {
  const to = 1782985000;                       // 因果 T（replay の getContext().to）
  const daySt = sessionDayStart(to); // GrowthWindow(normal,1h,to) 由来の当日始端（セッション日）
  const candleNow = to - 10 * 86400;            // getCandles 由来（base が使う）を別日に置く
  const candleDaySt = sessionDayStart(candleNow);
  assert.notEqual(candleDaySt, daySt, '前提: getCandles 由来の当日始端は GrowthWindow(ctxTo) と別日');
  const actor = new ReplayMarketProfileActor({
    primitive: fakePrimitive(),
    formingClient: fakeFormingClient([BASE_FULL]),
    makeAccumulator: fakeAccumulatorFactory().make,
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1h', to }),
    getCandles: () => [{ time: candleNow, close: 1 }], // 基底 present 経路が参照する最新ローソク
    now: () => 0,
  });
  actor.setParams({ mode: 'normal' });
  actor.applyGrowthState({ growing: true }); // 基底 present 分岐条件（_growing && !_sessions）を成立させる
  // Act
  const args = actor._buildFormingArgs({ base: 1, since: null });
  // Assert: from は GrowthWindow(ctxTo)=当日始端(to 由来)。getCandles 由来（別日）へは波及しない。
  assert.equal(args.now, to, 'replay override は now=getContext().to を維持');
  assert.equal(args.from, daySt, 'replay override の from は GrowthWindow(ctxTo) 由来（基底 getCandles 由来へ波及しない）');
  assert.notEqual(args.from, candleDaySt, '基底 present 窓（getCandles 由来）は replay へ漏れない');
});

// --- Fix #1（replayStart 累積）: driver 明示 from（=replayStart のバー時刻）を enterBar/growTo が forming へ
//   透過し、当日窓 GrowthWindow フォールバックを上書きする。再生開始点から累積＝日跨ぎでも非リセット。 ---
test('enterBar(now, from) threads explicit from (replayStart) into forming args (overrides today-window fallback)', async () => {
  const now = 1782985000;
  const replayStart = now - 3 * 86400; // 3 日前（replayStart < formingStart）＝再生開始点
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor } = makeActor({ formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make, ctxTo: now });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  // Act: driver が replayStart を明示 from で渡す。
  await actor.enterBar(now, replayStart);
  // Assert: from=replayStart（累積下限）が forming へ載る（当日窓でない＝日跨ぎ非リセット）。
  const call = forming.calls[forming.calls.length - 1];
  assert.equal(call.now, now, 'now=因果 T（未来リークなし）');
  assert.equal(call.from, replayStart, 'from=replayStart（再生開始点から累積・GrowthWindow 当日窓を上書き）');
});

test('growTo(now, from) threads explicit from (replayStart) into forming args', async () => {
  const now = 1782985000;
  const replayStart = now - 5 * 86400;
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor } = makeActor({ formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make, ctxTo: now });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  // Act
  await actor.growTo(now, replayStart);
  // Assert
  assert.equal(forming.calls[forming.calls.length - 1].from, replayStart, 'growTo も replayStart を forming へ透過');
});

test('enterBar clamps from to formingStart when from>formingStart (invariant from<=formingStart, no future-leak)', async () => {
  const now = 1782985000;
  const formingStart = Math.floor(now / 3600) * 3600; // 1h 床（makeActor tf='1h'）
  const from = now + 100; // formingStart より後（未来側）＝クランプ対象
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor } = makeActor({ formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make, ctxTo: now });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  // Act
  await actor.enterBar(now, from);
  // Assert: from>formingStart は formingStart へクランプ（不変条件・未来リーク禁止）。
  assert.equal(forming.calls[forming.calls.length - 1].from, formingStart, 'from>formingStart は formingStart へクランプ');
});

// --- Fix #2（完成足フラッシュ撲滅）: reveal 成長中（growing push）の refresh は全期間 fetchProfile（完成形・
//   未来リーク）を描かず、因果 base 窓（forming・enterBar）で開始する。setEnabled(true) の基底 refresh も同様。 ---
test('refresh during growing push draws causal base via forming (enterBar), NOT all-period fetchProfile (no completed-profile flash)', async () => {
  const now = 1782985000;
  const client = fakeClient();
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor } = makeActor({ formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make, client, ctxTo: now });
  actor.setParams({ mode: 'normal' });
  actor._enabled = true; // setEnabled を経ず enabled 化（基底 refresh を先に走らせない）。
  actor.applyGrowthState({ growing: true });
  assert.equal(actor.isGrowingPush(), true, 'growing push（normal+growing+enabled）');
  const fetchBefore = client.calls.length;
  const formingBefore = forming.calls.length;
  // Act
  await actor.refresh();
  // Assert: 全期間 fetchProfile（完成形フラッシュ）を呼ばず、forming（因果 base）で描く。
  assert.equal(client.calls.length, fetchBefore, 'growing push の refresh は全期間 fetchProfile を呼ばない（完成形フラッシュ無し）');
  assert.equal(forming.calls.length, formingBefore + 1, 'refresh は enterBar 経由で forming（因果 base）を取得する');
  assert.equal(forming.calls[forming.calls.length - 1].now, now, 'base 窓は現在カーソル now=getContext().to（因果・未来リークなし）');
});

test('refresh when NOT growing push delegates to base all-period refresh (sessions/static unchanged)', async () => {
  const now = 1782985000;
  const client = fakeClient();
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor } = makeActor({ formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make, client, ctxTo: now });
  await actor.setEnabled(true); // normal・非 growing → isGrowingPush=false
  const fetchBefore = client.calls.length;
  // Act
  await actor.refresh();
  // Assert: 非 growing push は基底 refresh（全期間 fetchProfile）へ委譲＝回帰なし。
  assert.equal(client.calls.length, fetchBefore + 1, '非 growing push は基底 refresh（全期間 fetchProfile）へ委譲');
});

test('regression: refresh on forming-unsupported tf (1W) delegates to base refresh even when growing push (no blank-profile regression)', async () => {
  const now = 1782985000;
  const c = fakeClient();
  const forming = fakeFormingClient([BASE_FULL]);
  // 1W: forming 非対応（backend 400→null）。growing push でも基底 refresh（全期間 as-of）で描く。
  const actor = new ReplayMarketProfileActor({
    client: c, primitive: fakePrimitive(), mainSeries: fakeMainSeries(),
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1W', to: now }),
    formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make, now: () => 0, throttleMs: 120,
  });
  actor.setParams({ mode: 'normal' });
  actor._enabled = true;
  actor.applyGrowthState({ growing: true });
  assert.equal(actor.isGrowingPush(), true, '1W も growing push（tf ゲートは isGrowingPush ではなく refresh override 内）');
  const fetchBefore = c.calls.length;
  const formingBefore = forming.calls.length;
  // Act
  await actor.refresh();
  // Assert: 1W は forming で描けないため基底 refresh（全期間 fetchProfile）へ委譲＝1W/1M の描画欠落を防ぐ。
  assert.equal(c.calls.length, fetchBefore + 1, '1W(forming 非対応)は growing push でも基底 refresh へ委譲');
  assert.equal(forming.calls.length, formingBefore, '1W は forming(enterBar)を発火しない（描画欠落回避）');
});

test('regression: setEnabled(true) during growing push does NOT fetch all-period profile (no enable-time completed-profile flash); uses forming base', async () => {
  const now = 1782985000;
  const client = fakeClient();
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor } = makeActor({ formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make, client, ctxTo: now });
  actor.setParams({ mode: 'normal', src: 'dwell' }); // 増分 src（ISSUE-120: 既定 src=zp は非増分ゆえ forming 経路対象外）
  actor.applyGrowthState({ growing: true }); // reveal は常時 growing（composition mpGrowthResolver=()=>true 相当）
  const fetchBefore = client.calls.length;
  // Act: MP 有効化（applyIndicator→setEnabled(true)）＋再生 1 フレーム目相当の enterBar。
  //   reveal の pull/push 分離（ISSUE-127/129）: growing push では setEnabled の onLiveTick は no-op で、
  //   最初の描画は enterBar（因果 base）が駆動する（完成形フラッシュを描かない）。
  await actor.setEnabled(true);
  await actor.enterBar(now);
  // Assert: 全期間 fetchProfile（未来リーク・完成形フラッシュ）を一切呼ばない。
  assert.equal(client.calls.length, fetchBefore, 'growing push は全期間 fetchProfile を呼ばない（完成形フラッシュ無し）');
  assert.ok(forming.calls.length >= 1, '最初の描画は enterBar の forming（因果 base）');
});

// --- 再発報告（restore 経路）: index.html は controller.restore() を setupReplay()（＝untilTime を設定する
//   唯一の場所）より前に実行する。前回セッションで MP が表示状態のまま永続化されていると、restore →
//   setEnabled(true) → refresh() の時点で getContext().to（カーソル）が undefined になり、`cursor != null`
//   ガードを抜けて基底 refresh（全期間・完成形）が setProfile される＝完成形フラッシュ→再生開始でリセット→成長。
//   growing push＋forming 対応 tf でカーソル未確定なら「何も描かない」（未来リーク禁止・最初の描画は再生
//   1 フレーム目 enterBar の因果 base）を固定する。 ---
test('regression(restore flash): refresh during growing push with cursor unset (page-load restore) draws NOTHING — no all-period completed-profile flash', async () => {
  const ALL_PERIOD = { __allPeriod: true, poc: 12345, bins: [] };
  const client = fakeClient(ALL_PERIOD);
  const forming = fakeFormingClient([BASE_FULL]);
  const primitive = fakePrimitive();
  const { actor } = makeActor({
    formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make,
    client, primitive, ctxTo: undefined, // restore 時: setupReplay 前＝untilTime 未設定。
  });
  actor.setParams({ mode: 'normal' });
  actor.applyGrowthState({ growing: true }); // reveal は常時 growing（mpGrowthResolver=()=>true）。
  // Act: restore() の可視 MP 復元経路（setEnabled(true)→refresh）。
  await actor.setEnabled(true);
  // Assert: 全期間 fetchProfile（未来リーク・完成形フラッシュ）を呼ばず、setProfile も一切描かない。
  assert.equal(client.calls.length, 0, 'cursor 未確定の growing push refresh は全期間 fetchProfile を呼ばない');
  assert.equal(primitive.profiles.length, 0, 'cursor 未確定では何も描かない（最初の描画は再生 1 フレーム目の因果 base）');
  assert.equal(forming.calls.length, 0, 'cursor 未確定では forming も引かない（now 無しの base 窓は定義不能）');
});

// --- Fix #2 回帰（再発防止・観測点は setProfile 描画そのもの）: 開始シーケンス（setEnabled(true)）で
//   「その時間足の完成形＝全期間 as-of プロファイル」を primitive.setProfile で一度も描かないことを固定する。
//   過去にこの完成足フラッシュ→リセット→成長が回帰テスト不在で再発したため、誤シーケンスが起きたら fail する
//   非空テストにする（全期間 profile が描かれた瞬間に fail）。 ---
test('regression(recurring flash): start sequence never draws the all-period (completed) profile; first draw is the causal forming base', async () => {
  const now = 1782985000;
  const daySt = sessionDayStart(now);
  // 全期間 refresh の描画を一意マーカーで識別する（描かれたら完成足フラッシュ＝bug 再発）。
  const ALL_PERIOD = { __allPeriod: true, poc: 12345, bins: [] };
  const client = fakeClient(ALL_PERIOD);
  // forming base は accumulator snapshot（_snap:true）で描かれる（因果 base・空 forming＝再生点の開始形）。
  const forming = fakeFormingClient([{ ...BASE_FULL, ticks: [], formingStart: daySt, now }]);
  const primitive = fakePrimitive();
  const { actor } = makeActor({
    formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make,
    client, primitive, ctxTo: now,
  });
  actor.setParams({ mode: 'normal', src: 'dwell' }); // 増分 src（ISSUE-120: 既定 src=zp は非増分ゆえ forming 経路対象外）
  actor.applyGrowthState({ growing: true }); // reveal は常時 growing。
  // Act: MP 有効化（applyIndicator→setEnabled(true)）＋再生 1 フレーム目相当の enterBar（reveal 駆動点）。
  //   growing push では setEnabled の onLiveTick は no-op（pull/push 分離）ゆえ、開始形は enterBar が描く。
  await actor.setEnabled(true);
  await actor.enterBar(now);
  // Assert 1: 全期間プロファイル（完成足フラッシュ）を一度も描かない（誤シーケンスが起きたら fail）。
  const drewAllPeriod = primitive.profiles.some((p) => p && p.__allPeriod === true);
  assert.equal(drewAllPeriod, false, '開始シーケンスで全期間 as-of プロファイル（完成足）を setProfile しない');
  // Assert 2: 少なくとも 1 回は因果 base（forming snapshot）を描く（無描画の見せかけ緑を排除）。
  assert.ok(primitive.profiles.length >= 1, '因果 base を描く（開始形）');
  assert.ok(primitive.profiles.every((p) => p && p._snap === true),
    '描画は全て forming base（accumulator snapshot）＝完成形フラッシュ無し');
});

// --- enterBar（ticklive・fallback）: driver 未配線 from の enterBar は GrowthWindow 当日窓へフォールバックする
//   （controller seam / gear 経路）。primary は上の explicit-from（replayStart）テストが固定する。 ---
test('enterBar (ticklive, no explicit from) falls back to GrowthWindow today-window: base=1/src=dwell/now=T/from=当日始端', async () => {
  const now = 1782985000;
  const daySt = sessionDayStart(now); // 当日始端（セッション日）
  const forming = fakeFormingClient([BASE_FULL]);
  const facc = fakeAccumulatorFactory();
  const { actor, primitive } = makeActor({ formingClient: forming, makeAccumulator: facc.make, ctxTo: now });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  const drawnBefore = primitive.profiles.length;
  // Act
  await actor.enterBar(now);
  // Assert
  const call = forming.calls[forming.calls.length - 1];
  assert.equal(call.base, 1);
  assert.equal(call.src, 'dwell');
  assert.equal(call.now, now);
  assert.equal(call.from, daySt, '視認性修正: from=絞った窓の当日始端（min(当日,formingStart)）');
  assert.equal(facc.created.length, 1);
  // fold 版 enterBar: forming.ticks を畳み込む（present _enterTicklive 準拠）。BASE_FULL は ticks=[] のため
  //   畳み込み結果も空＝この入力では畳み込み有無に関わらず空（別 test で ticks あり fold を検証）。
  assert.deepEqual(facc.created[0].ticks, [], 'BASE_FULL は forming tick 空＝畳み込み結果も空');
  assert.equal(primitive.profiles.length, drawnBefore + 1, '非縮退レンジは base を 1 回描画');
});

// --- fold 版 enterBar: forming.ticks を畳み込み _lastSec を設定（present _enterTicklive 準拠） ---
test('enterBar folds forming.ticks and sets _lastSec (fold semantics matching present _enterTicklive)', async () => {
  const now = 1782985000;
  const WT = { ...BASE_FULL, ticks: [[1000, 1005], [1010, 1015]], now };
  const forming = fakeFormingClient([WT]);
  const facc = fakeAccumulatorFactory();
  const { actor } = makeActor({ formingClient: forming, makeAccumulator: facc.make, ctxTo: now });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  // Act
  await actor.enterBar(now);
  // Assert: forming.ticks を addTick で畳み込む（present 基底 _enterTicklive L297-300 の fold と一致）。
  assert.deepEqual(facc.created[0].ticks, [[1000, 1005], [1010, 1015]], 'enterBar が forming.ticks を畳み込む');
});

// --- 縮退グリッド（forming tick 0 + [0,1] レンジ）は描画スキップ（潰れ描画を出さない・前回保持） ---
test('enterBar skips draw when grid is degenerate (empty ticks + [0,1] range) — no collapsed profile', async () => {
  const dayStart = 1782950400;
  const DEG = {
    ok: true, formingStart: dayStart, ticks: [], baseFine: [0], baseKmin: 0,
    activeTable: [[1]], priceMin: 0, priceMax: 1, nBins: 1, gridW: 10, vaPct: VA_PCT_DEFAULT, now: dayStart,
  };
  const forming = fakeFormingClient([DEG]);
  const facc = fakeAccumulatorFactory();
  const { actor, primitive } = makeActor({ formingClient: forming, makeAccumulator: facc.make, ctxTo: dayStart });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  const before = primitive.profiles.length; // setEnabled(normal) の refresh 描画分を基準化。
  // Act
  await actor.enterBar(dayStart);
  // Assert: accumulator は init 済み（growTo 用にグリッド準備）だが縮退のため描画を増やさない（[0,1] 潰れなし）。
  assert.equal(facc.created.length, 1, '縮退でも accumulator は init（growTo で作り直す土台）');
  assert.equal(primitive.profiles.length, before, '縮退グリッドは描画スキップ（前回描画保持＝増分なし）');
});

// --- growTo(now): now までの因果窓で forming 再取得→グリッド拡張・forming.ticks 畳み込み・_lastSec 設定 ---
test('growTo re-fetches forming up to now, re-inits expanded grid, folds forming.ticks, sets _lastSec, draws', async () => {
  const dayStart = 1782950400;
  const nowMid = dayStart + 34600;
  const DEG = {
    ok: true, formingStart: dayStart, ticks: [], baseFine: [0], baseKmin: 0,
    activeTable: [[1]], priceMin: 0, priceMax: 1, nBins: 1, gridW: 10, vaPct: VA_PCT_DEFAULT, now: dayStart,
  };
  const GROWN = {
    ok: true, formingStart: dayStart, ticks: [[dayStart + 100, 71000], [dayStart + 200, 71050]],
    baseFine: [0, 0, 0, 0, 0, 0], baseKmin: 7100, activeTable: [[1]],
    priceMin: 71000, priceMax: 71050, nBins: 3, gridW: 10, vaPct: VA_PCT_DEFAULT, now: nowMid,
  };
  const forming = fakeFormingClient([DEG, GROWN]);
  const facc = fakeAccumulatorFactory();
  const { actor, primitive } = makeActor({ formingClient: forming, makeAccumulator: facc.make, ctxTo: nowMid });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  await actor.enterBar(dayStart); // 縮退 → 描画スキップ
  const drawnBefore = primitive.profiles.length;
  // Act
  await actor.growTo(nowMid);
  // Assert: growTo は now までの forming を再取得し、拡張グリッドで作り直し forming.ticks を畳み込む。
  const call = forming.calls[forming.calls.length - 1];
  assert.equal(call.base, 1);
  assert.equal(call.now, nowMid, 'growTo は now(=直近 revealed tick 秒)までの因果窓で再取得');
  assert.equal(facc.created.length, 2, 'accumulator を作り直す（グリッド拡張 init）');
  assert.deepEqual(facc.created[1].ticks, [[dayStart + 100, 71000], [dayStart + 200, 71050]], 'forming.ticks を畳み込む');
  assert.equal(primitive.profiles.length, drawnBefore + 1, 'growTo は拡張グリッドで描画する');
});

// --- feedTick de-dup: 畳み込み済み tick（sec <= _lastSec）は二重計上しない ---
test('feedTick de-dups folded ticks (sec <= _lastSec skipped — no double count), advances _lastSec otherwise', async () => {
  const now = 1782985000;
  const WT = { ...BASE_FULL, ticks: [[1000, 1005], [1010, 1015]], now };
  const forming = fakeFormingClient([WT]);
  const facc = fakeAccumulatorFactory();
  const { actor } = makeActor({ formingClient: forming, makeAccumulator: facc.make, ctxTo: now });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  await actor.enterBar(now); // fold [1000,1010] → _lastSec=1010
  assert.deepEqual(facc.created[0].ticks, [[1000, 1005], [1010, 1015]]);
  // Act: 畳み込み済み範囲（sec<=1010）の tick は de-dup で捨てる。
  actor.feedTick(1010, 9999); // dup（同秒）
  actor.feedTick(1005, 8888); // 過去（畳み込み済み）
  assert.deepEqual(facc.created[0].ticks, [[1000, 1005], [1010, 1015]], 'sec<=_lastSec は addTick しない（二重計上防止）');
  // 新規 tick（sec>_lastSec）は addTick し _lastSec を進める。
  actor.feedTick(1020, 1025);
  assert.deepEqual(facc.created[0].ticks, [[1000, 1005], [1010, 1015], [1020, 1025]], 'sec>_lastSec は addTick');
});

// --- isTickInGrid(mid): 直近 forming の priceMin/priceMax による範囲判定（未グリッド時は false） ---
test('isTickInGrid reflects last forming priceMin/priceMax (false before any grid)', async () => {
  const now = 1782985000;
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor } = makeActor({ formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make, ctxTo: now });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  // Assert: グリッド未確定なら false（growTo 発火の起点）。
  assert.equal(actor.isTickInGrid(1050), false, 'グリッド未確定は false');
  // Act
  await actor.enterBar(now);
  // Assert: priceMin/priceMax 包含判定。
  assert.equal(actor.isTickInGrid(1000), true, 'priceMin は範囲内');
  assert.equal(actor.isTickInGrid(1100), true, 'priceMax は範囲内');
  assert.equal(actor.isTickInGrid(1050), true);
  assert.equal(actor.isTickInGrid(999), false, 'priceMin 未満は範囲外');
  assert.equal(actor.isTickInGrid(5000), false, '範囲外');
});

// --- e2e（実 DwellAccumulator）: 縮退 enterBar→growTo で当日レンジへグリッド拡張し当日 tick が育つ ---
test('e2e (real DwellAccumulator): degenerate enterBar then growTo expands grid to day range and grows in-range tpo', async () => {
  const dayStart = 1782950400;
  const nowMid = dayStart + 34600;
  const table = Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => 1));
  const DEG = {
    ok: true, formingStart: dayStart, ticks: [], baseFine: [0], baseKmin: 0,
    activeTable: table, priceMin: 0, priceMax: 1, nBins: 1, gridW: 10, vaPct: VA_PCT_DEFAULT, now: dayStart,
  };
  const lo = 71000; const hi = 71050; const gw = 10;
  const kmin = Math.floor(lo / gw);
  const size = Math.floor(hi / gw) - kmin + 1;
  const GROWN = {
    ok: true, formingStart: dayStart, ticks: [[dayStart + 100, lo], [dayStart + 200, hi]],
    baseFine: new Array(size).fill(0), baseKmin: kmin, activeTable: table,
    priceMin: lo, priceMax: hi, nBins: 3, gridW: gw, vaPct: VA_PCT_DEFAULT, now: nowMid,
  };
  const forming = fakeFormingClient([DEG, GROWN]);
  const { actor, primitive } = makeActor({
    formingClient: forming, makeAccumulator: () => new DwellAccumulator(),
    now: (() => { let t = 0; return () => (t += 1000); })(), throttleMs: 120, ctxTo: nowMid,
  });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  const before = primitive.profiles.length; // setEnabled(normal) の refresh 描画分を基準化。
  await actor.enterBar(dayStart);
  assert.equal(primitive.profiles.length, before, '縮退 enterBar は [0,1] 潰れを描かない（増分なし）');
  assert.equal(actor.isTickInGrid(lo), false, '当日 tick は縮退グリッド外');
  // Act
  await actor.growTo(nowMid);
  // Assert: グリッドが当日レンジへ拡張し、当日 tick が範囲内 tpo を持つ（[0,1] へ潰れない）。
  assert.equal(actor.isTickInGrid(lo), true, 'growTo 後は当日レンジを包含');
  const drawn = primitive.profiles[primitive.profiles.length - 1];
  assert.equal(drawn.n_bins, 3);
  assert.ok(drawn.tpo_units > 0, '当日プロファイルが範囲内 tpo を持つ（clip されない）');
});

// --- present byte golden: 共有 DwellAccumulator.snapshot は byte 不変（present 回帰ゼロの土台） ---
test('present byte golden: shared DwellAccumulator.snapshot is byte-stable (present regression zero)', () => {
  const table = Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => 1));
  const acc = new DwellAccumulator();
  acc.init({ baseFine: [0, 0, 0], baseKmin: 100, activeTable: table, priceMin: 1000, priceMax: 1100, nBins: 3, gridW: 10, vaPct: VA_PCT_DEFAULT, formingStart: 1704074400 });
  acc.addTick(1704074460, 1005);
  acc.addTick(1704074520, 1015);
  acc.addTick(1704074580, 1055);
  // Assert: 共有 domain（present と symlink 共有）の snapshot が golden と厳密一致＝本件で 1byte も変えていない。
  assert.deepEqual(acc.snapshot(), {
    bins: [
      { price: 1016.67, tpo: 120, norm: 1 },
      { price: 1050, tpo: 0, norm: 0 },
      { price: 1083.33, tpo: 0, norm: 0 },
    ],
    poc: 1016.67, va_low: 1016.67, va_high: 1016.67,
    price_min: 1000, price_max: 1100, tpo_units: 120, n_bins: 3,
  });
});

test('enterBar (ticklive) keeps previous drawing when forming is null (non-disruptive)', async () => {
  const now = 1782985000;
  const forming = fakeFormingClient([BASE_FULL, null]);
  const facc = fakeAccumulatorFactory();
  const { actor, primitive } = makeActor({ formingClient: forming, makeAccumulator: facc.make, ctxTo: now });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  await actor.enterBar(now);
  const drawn = primitive.profiles.length;
  await actor.enterBar(now + 3600); // null
  assert.equal(facc.created.length, 1);
  assert.equal(primitive.profiles.length, drawn);
});

test('enterBar (ticklive) keeps previous drawing when base fields missing (no NaN init)', async () => {
  const now = 1782985000;
  const EMPTY = { ok: true, formingStart: 2000, ticks: [], now: 2010 };
  const forming = fakeFormingClient([BASE_FULL, EMPTY]);
  const facc = fakeAccumulatorFactory();
  const { actor, primitive } = makeActor({ formingClient: forming, makeAccumulator: facc.make, ctxTo: now });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  await actor.enterBar(now);
  const drawn = primitive.profiles.length;
  await actor.enterBar(now + 3600);
  assert.equal(facc.created.length, 1, '欠損時は init しない（NaN 回避）');
  assert.equal(primitive.profiles.length, drawn);
});

// --- feedTick / settleTick: ticklive の push 成長 ---
test('feedTick (ticklive) accumulates every tick but throttles snapshots', async () => {
  const now = 1782985000;
  const forming = fakeFormingClient([BASE_FULL]);
  const facc = fakeAccumulatorFactory();
  const clock = fakeClock([0, 0, 50, 120, 121]);
  const { actor, primitive } = makeActor({ formingClient: forming, makeAccumulator: facc.make, now: clock, throttleMs: 120, ctxTo: now });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  await actor.enterBar(now);
  const afterEnter = primitive.profiles.length;
  actor.feedTick(1010, 1005);
  actor.feedTick(1020, 1015);
  actor.feedTick(1030, 1025);
  actor.feedTick(1040, 1035);
  assert.deepEqual(facc.created[0].ticks, [[1010, 1005], [1020, 1015], [1030, 1025], [1040, 1035]]);
  assert.equal(primitive.profiles.length, afterEnter + 1, 'throttle 通過分のみ snapshot');
});

test('feedTick is a no-op when disabled (MP OFF non-interference)', async () => {
  const now = 1782985000;
  const forming = fakeFormingClient([BASE_FULL]);
  const facc = fakeAccumulatorFactory();
  const { actor, primitive } = makeActor({ formingClient: forming, makeAccumulator: facc.make, ctxTo: now });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  await actor.enterBar(now);
  const drawn = primitive.profiles.length;
  await actor.setEnabled(false);
  actor.feedTick(1010, 1005);
  assert.deepEqual(facc.created[0].ticks, []);
  assert.equal(primitive.profiles.length, drawn);
});

test('settleTick (ticklive) forces a final snapshot regardless of throttle', async () => {
  const now = 1782985000;
  const forming = fakeFormingClient([BASE_FULL]);
  const facc = fakeAccumulatorFactory();
  const clock = fakeClock([0, 1, 2, 3, 4]);
  const { actor, primitive } = makeActor({ formingClient: forming, makeAccumulator: facc.make, now: clock, throttleMs: 120, ctxTo: now });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  await actor.enterBar(now);
  actor.feedTick(1010, 1005);
  const before = primitive.profiles.length;
  actor.settleTick();
  assert.equal(primitive.profiles.length, before + 1);
});

// --- 基底 replay モード再利用: setReplayCursor で to=T の as-of fetch（scrub） ---
test('inherited replay mode: setReplayCursor fetches as-of profile with to=T (base reuse)', async () => {
  const client = fakeClient({ bins: [], poc: 5 });
  const { actor, primitive } = makeActor({
    formingClient: fakeFormingClient([BASE_FULL]), makeAccumulator: fakeAccumulatorFactory().make,
    client, ctxTo: null,
  });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'replay' }); // 基底 _applyMode('replay')
  const before = client.calls.length;
  // Act
  await actor.setReplayCursor(1704074400);
  // Assert: 基底 _fetchAt が to=T で as-seen-at-t を取得する（複製せず再利用）。
  assert.ok(client.calls.length > before);
  assert.equal(client.calls[client.calls.length - 1].to, 1704074400);
});

// --- end-to-end: 実 DwellAccumulator で feedTick が逐次成長を描く（ticklive push） ---
test('end-to-end (ticklive) with real DwellAccumulator: enterBar draws base, feedTick grows tpo', async () => {
  const now = 1704074400;
  const table = Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => 1));
  const payload = {
    ok: true, formingStart: now, ticks: [],
    baseFine: [0, 0, 0], baseKmin: 100, activeTable: table,
    priceMin: 1000, priceMax: 1100, nBins: 3, gridW: 10, vaPct: VA_PCT_DEFAULT, now,
  };
  const forming = fakeFormingClient([payload]);
  const { actor, primitive } = makeActor({
    formingClient: forming, makeAccumulator: () => new DwellAccumulator(),
    now: (() => { let t = 0; return () => (t += 1000); })(), throttleMs: 120, ctxTo: now,
  });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  await actor.enterBar(now);
  actor.feedTick(now + 60, 1005);
  actor.feedTick(now + 120, 1015);
  actor.settleTick();
  const drawn = primitive.profiles[primitive.profiles.length - 1];
  assert.equal(drawn.n_bins, 3);
  assert.equal(drawn.bins[0].tpo, 60);
});

// =====================================================================================
// ISSUE-050: replay MP stale プロファイル・フラッシュ（後方スクラブで await fetchForming 中に
//   旧・広プロファイルが露出する）の回帰テスト。方式は参照実装 prototype_260630-01 fetchProfileOnly の
//   profSeq 世代ガードと、同期クランプ（blank 禁止）の 2 点セット（承認済み）。
// =====================================================================================

// fetchForming を手動解決できる制御可能クライアント（Promise 解決順を制御して世代ガード/クランプ順序を実証）。
//   ネットワーク不使用（全フェイク・CLAUDE.md 準拠）。
function controllableFormingClient() {
  const calls = [];
  const pending = [];
  return {
    calls,
    pending,
    fetchForming(args) {
      calls.push(args);
      let resolveFn;
      const p = new Promise((res) => { resolveFn = res; });
      pending.push(resolveFn);
      return p;
    },
    resolveAt(idx, value) { this.pending[idx](value); },
  };
}

// snapshot が init cfg の priceMin/priceMax から bins（[min, 中点, max]）と __marker(=priceMax) を導く fake
//   accumulator。これによりクランプ（revealed 超 bin 除去）と世代ガード（どの応答が描かれたか）を検証できる。
function markerAccumulatorFactory() {
  const created = [];
  const make = () => {
    const acc = {
      init(cfg) { this.cfg = cfg; this.ticks = []; },
      addTick(sec, mid) { this.ticks.push([sec, mid]); },
      snapshot() {
        const pmin = this.cfg ? Number(this.cfg.priceMin) : 0;
        const pmax = this.cfg ? Number(this.cfg.priceMax) : 0;
        const mid = (pmin + pmax) / 2;
        return {
          bins: [
            { price: pmin, tpo: 5, norm: 1 },
            { price: mid, tpo: 3, norm: 0.6 },
            { price: pmax, tpo: 1, norm: 0.2 },
          ],
          poc: pmin, va_low: pmin, va_high: pmax,
          price_min: pmin, price_max: pmax,
          n_bins: 3, tpo_units: 9, __marker: pmax,
        };
      },
    };
    created.push(acc);
    return acc;
  };
  return { make, created };
}

function makeClampActor({ getCandles, formingClient, makeAccumulator, primitive, ctxTo }) {
  const p = primitive ?? fakePrimitive();
  const actor = new ReplayMarketProfileActor({
    primitive: p,
    mainSeries: fakeMainSeries(),
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1h', to: ctxTo }),
    getCandles,
    formingClient,
    makeAccumulator,
    now: () => 0,
  });
  actor._enabled = true;            // setEnabled を経ず enabled 化（基底 refresh を先に走らせない）。
  actor.setParams({ mode: 'ticklive' }); // isGrowingPush()=true（push 成長）。
  return { actor, primitive: p };
}

// (1) 同期クランプ: 後方スクラブで await fetchForming の解決前に、旧・広プロファイルを revealed 上限へ
//   同期クランプして描画する（revealed 超の bin/VA を除去・blank 禁止）。
test('ISSUE-050 sync clamp: backward scrub clamps stale (wider) profile to revealed max BEFORE fetchForming resolves (no blank)', async () => {
  const now = 1782985000;
  // revealed（_getCandles 末尾までの high 最大）を可変にして後方スクラブを表現する。初期は広い（max high 150）。
  let revealed = [{ time: now, high: 150, low: 80, close: 120 }];
  const client = controllableFormingClient();
  const facc = markerAccumulatorFactory();
  const { actor, primitive } = makeClampActor({
    getCandles: () => revealed, formingClient: client, makeAccumulator: facc.make, ctxTo: now,
  });
  // 1本目 enterBar: 解決して stale accumulator（priceMax=150 の広い snapshot）を確立（この時点は _accumulator 無で clamp 無し）。
  const p1 = actor.enterBar(now);
  client.resolveAt(0, { ...BASE_FULL, priceMin: 80, priceMax: 150, now });
  await p1;
  const afterFirst = primitive.profiles.length;
  // 後方スクラブ: revealed を狭める（max high 100）＝新カーソルの revealed 上限は 100。
  revealed = [{ time: now - 3600, high: 100, low: 80, close: 90 }];
  // 2本目 enterBar（後方）: fetch は保留のまま。
  const p2 = actor.enterBar(now - 3600);
  // Assert（await 解決前）: 同期クランプで setProfile が発火し、revealed max(100) 超の bin/VA を落としている。
  assert.ok(primitive.profiles.length >= afterFirst + 1, '同期クランプが await fetchForming の前に setProfile する');
  const clamped = primitive.profiles[afterFirst]; // 2本目で最初に描かれたクランプ profile
  assert.ok(clamped.bins.length > 0, 'クランプは空描画にしない（ISSUE-049 全消滅フラッシュ回避）');
  assert.ok(clamped.bins.every((b) => b.price <= 100), 'revealed max(100) 超の bin（前カーソルの未来価格）を落とす');
  assert.ok(Number(clamped.price_max) <= 100, 'price_max <= revealed max（実データ範囲を超えない）');
  assert.ok(clamped.va_high == null || clamped.va_high <= 100, 'VA 上端も revealed max を超えない');
  // settle: fetch を解決して as-of-T の正しいプロファイルへ自己修正。
  client.resolveAt(1, { ...BASE_FULL, priceMin: 80, priceMax: 100, now: now - 3600 });
  await p2;
});

// (1b) 完全非重複の大ジャンプ: 旧プロファイルが全て revealed 上限超なら、空プロファイル（bins=[]）を描いて
//   旧 stale 広プロファイルを消す（blank）。keep-stale は ISSUE-050 同クラスの「MP がローソク域外へ浮く」欠陥を
//   ≈180ms 再生するため不可（architecture-executor 観点5）。ISSUE-049（成長中の縮退＝**有効 bin あり**の全消滅
//   禁止）とは保護対象が異なり非矛盾（revealed 域に有効 bin が無いのが正直な状態）。
test('ISSUE-050 sync clamp: fully non-overlapping backward jump BLANKS the stale (wider) profile (bins=[]) not keep-stale', async () => {
  const now = 1782985000;
  let revealed = [{ time: now, high: 150, low: 80, close: 120 }];
  const client = controllableFormingClient();
  const facc = markerAccumulatorFactory();
  const { actor, primitive } = makeClampActor({
    getCandles: () => revealed, formingClient: client, makeAccumulator: facc.make, ctxTo: now,
  });
  // 1本目 enterBar: stale accumulator（bins at 80/115/150）を確立。
  const p1 = actor.enterBar(now);
  client.resolveAt(0, { ...BASE_FULL, priceMin: 80, priceMax: 150, now });
  await p1;
  const afterFirst = primitive.profiles.length;
  // 完全非重複の後方ジャンプ: revealed 上限を全 stale bin(80 以上) 未満(70)へ。
  revealed = [{ time: now - 86400, high: 70, low: 50, close: 60 }];
  const p2 = actor.enterBar(now - 86400); // fetch 保留
  // Assert（await 前）: 空プロファイルを描いて旧 stale を消す（keep-stale でない）。
  assert.ok(primitive.profiles.length >= afterFirst + 1, '完全非重複でも同期クランプが setProfile する（旧 stale 消去）');
  const blanked = primitive.profiles[afterFirst];
  assert.equal(blanked.bins.length, 0, '完全非重複は空 bin（blank）＝revealed 域に有効 bin なし');
  assert.ok(Number(blanked.price_max) <= 70, 'price_max <= revealed max');
  assert.equal(blanked.poc, null, 'POC は revealed 超のため null（水平線を引かない）');
  // settle: fetch 解決で as-of-T の正しいプロファイルへ自己修正。
  client.resolveAt(1, { ...BASE_FULL, priceMin: 50, priceMax: 70, now: now - 86400 });
  await p2;
});

// (2) 世代ガード: 先に発行・後に解決した古い応答が、新しい応答の描画を上書きしない（profSeq 破棄）。
test('ISSUE-050 generation guard: a stale (earlier) fetchForming response does NOT overwrite the newer draw (profSeq discard)', async () => {
  const now = 1782985000;
  const revealed = [{ time: now, high: 2000, low: 80, close: 1500 }]; // revealed 上限は広く固定（clamp 影響を排除）
  const client = controllableFormingClient();
  const facc = markerAccumulatorFactory();
  const { actor, primitive } = makeClampActor({
    getCandles: () => revealed, formingClient: client, makeAccumulator: facc.make, ctxTo: now,
  });
  // 2 連続の後方スクラブ enterBar（両方 fetch 保留・_accumulator 未確立ゆえ clamp は発火しない）。
  const pOld = actor.enterBar(now);        // seq=1（先発）
  const pNew = actor.enterBar(now - 3600); // seq=2（後発・最新）
  const drawnBefore = primitive.profiles.length;
  // 後発（最新 seq=2）を先に解決 → 描画される。
  client.resolveAt(1, { ...BASE_FULL, priceMin: 80, priceMax: 120, now: now - 3600 });
  await pNew;
  assert.equal(primitive.profiles.length, drawnBefore + 1, '最新応答は 1 回描画される');
  assert.equal(primitive.profiles[primitive.profiles.length - 1].__marker, 120, '最新応答（priceMax=120）が描かれる');
  const lenAfterNew = primitive.profiles.length;
  // 先発（stale seq=1）を後から解決 → 世代ガードで破棄され、最新描画を上書きしない。
  client.resolveAt(0, { ...BASE_FULL, priceMin: 80, priceMax: 900, now });
  await pOld;
  assert.equal(primitive.profiles.length, lenAfterNew, 'stale 応答は setProfile を追加しない（世代ガード破棄）');
  assert.equal(primitive.profiles[primitive.profiles.length - 1].__marker, 120, '最新描画（120）は stale(900) に上書きされない');
});

// (3a) present 非波及（前方回帰ガード・特性化テスト＝旧コードでも Green）: getCandles 未注入（revealed 上限
//   不明）では同期クランプを発火しない（既存挙動不変・clamp が present 経路へ漏れないことを固定）。
test('ISSUE-050 no clamp draw when candles unavailable (revealed max unknown) — present non-propagation', async () => {
  const now = 1782985000;
  const forming = fakeFormingClient([BASE_FULL, BASE_FULL]);
  const facc = fakeAccumulatorFactory();
  const { actor, primitive } = makeActor({ formingClient: forming, makeAccumulator: facc.make, ctxTo: now });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  await actor.enterBar(now);              // acc 確立
  const afterFirst = primitive.profiles.length;
  await actor.enterBar(now - 3600);        // getCandles 未注入＝revealed 上限 null → 同期クランプ発火せず
  assert.equal(primitive.profiles.length, afterFirst + 1, 'getCandles 無しでは同期クランプを発火しない（fetch 解決後の 1 回のみ）');
});

// (3b) enterBar 限定（前方回帰ガード・特性化テスト＝旧コードでも Green）: growTo（グリッド拡張・同一カーソル）は
//   同期クランプしない（成長中 tick を誤 clip しない・clamp が growTo へ漏れないことを固定）。
test('ISSUE-050 growTo (grid expansion, same cursor) does NOT sync-clamp — only enterBar (bar change) clamps', async () => {
  const now = 1782985000;
  const revealed = [{ time: now, high: 200, low: 80, close: 150 }];
  const client = controllableFormingClient();
  const facc = markerAccumulatorFactory();
  const { actor, primitive } = makeClampActor({
    getCandles: () => revealed, formingClient: client, makeAccumulator: facc.make, ctxTo: now,
  });
  const p1 = actor.enterBar(now);
  client.resolveAt(0, { ...BASE_FULL, priceMin: 80, priceMax: 150, now });
  await p1;
  const afterFirst = primitive.profiles.length;
  const pg = actor.growTo(now); // fetch 保留
  // Assert（await 前）: growTo は同期クランプ描画を追加しない（バー変更でなくグリッド拡張）。
  assert.equal(primitive.profiles.length, afterFirst, 'growTo は await 前に同期クランプ描画しない（enterBar 限定）');
  client.resolveAt(1, { ...BASE_FULL, priceMin: 80, priceMax: 160, now });
  await pg;
});

// --- ISSUE-047（再生中のバースケール変動の回帰禁止）: 成長 push 中の bins モードは、enterBar/growTo の
//   たびに backend が binw=(累積窓レンジ/bins) を再導出するため、レンジ拡大のたびにプロファイル全体が
//   再スケールしていた。修正: from 直前の因果履歴レンジから barw を 1 回だけ導出して固定し、
//   resmode=range（barw 固定・bin 数可変）として forming へ送る＝再生中のバー高さ/スケールが安定する。 ---

// from 直前 24 本（1h・レンジ 120pt）の因果履歴。lockedBarw=120/60=2。
function lockHistory(from) {
  const candles = [];
  for (let i = 0; i < 24; i += 1) {
    candles.push({ time: from - 3600 * (24 - i), low: 100, high: 220 });
  }
  return candles;
}

test('growing push + bins mode locks barw from causal history: forming args carry resmode=range with a stable range across bars (ISSUE-047)', async () => {
  const from = 1782900000 - (1782900000 % 86400); // 再生開始点（日境界に整列）
  const now1 = from + 3600 * 5;                   // 1 本目のバー
  const now2 = from + 3600 * 6;                   // 2 本目のバー（窓レンジが伸びた後でも）
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor } = makeActor({
    formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make,
    ctxTo: now1, getCandles: () => lockHistory(from),
  });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive', bins: 60 }); // bins モード（resmode 未指定）
  // Act: 連続する 2 バーの enterBar（driver from=replayStart）。
  await actor.enterBar(now1, from);
  await actor.enterBar(now2, from);
  // Assert: 両呼び出しとも resmode=range・range=履歴レンジ(120)/bins(60)=2 で固定（bins 送信へ戻らない）。
  assert.equal(forming.calls.length, 2);
  for (const call of forming.calls) {
    assert.equal(call.resmode, 'range', '成長 push の bins モードは barw 固定（resmode=range）で送る');
    assert.equal(call.range, 2, 'barw=因果履歴レンジ/bins（成長開始時に確定・以降固定）');
  }
});

test('growing push keeps an explicit user resmode=range untouched (user barw wins)', async () => {
  const from = 1782900000 - (1782900000 % 86400);
  const now = from + 3600 * 5;
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor } = makeActor({
    formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make,
    ctxTo: now, getCandles: () => lockHistory(from),
  });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive', resmode: 'range', range: 50 });
  // Act
  await actor.enterBar(now, from);
  // Assert: ユーザー明示の barw（range=50）を温存（ロックで上書きしない）。
  const call = forming.calls[forming.calls.length - 1];
  assert.equal(call.resmode, 'range');
  assert.equal(call.range, 50, 'ユーザー明示 range はロック導出より優先');
});

test('growing push falls back to bins mode when no causal history exists before from (lock unavailable)', async () => {
  const from = 1782900000 - (1782900000 % 86400);
  const now = from + 3600 * 5;
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor } = makeActor({
    formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make,
    ctxTo: now, getCandles: () => [], // 履歴なし（全期間プリセット等）
  });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive', bins: 60 });
  // Act
  await actor.enterBar(now, from);
  // Assert: ロック不能時は従来どおり bins を送る（挙動フォールバック・非破壊）。
  const call = forming.calls[forming.calls.length - 1];
  assert.notEqual(call.resmode, 'range', '履歴なしは range へ切り替えない');
  assert.equal(call.bins, 60, 'bins モードのまま（従来挙動）');
});

test('lock recomputes when the replay start (from) changes (new replay session)', async () => {
  const fromA = 1782900000 - (1782900000 % 86400);
  const fromB = fromA + 86400; // 別の再生開始点
  const forming = fakeFormingClient([BASE_FULL]);
  // fromA 直前はレンジ 120（barw=2）、fromB 直前はレンジ 240（barw=4）になる履歴。
  const candles = [];
  for (let i = 0; i < 24; i += 1) {
    candles.push({ time: fromA - 3600 * (24 - i), low: 100, high: 220 });
  }
  for (let i = 0; i < 24; i += 1) {
    candles.push({ time: fromB - 3600 * (24 - i), low: 100, high: 340 });
  }
  const { actor } = makeActor({
    formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make,
    ctxTo: fromB + 3600, getCandles: () => candles,
  });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive', bins: 60 });
  // Act: fromA → fromB（再生開始点の変更＝新セッション）。
  await actor.enterBar(fromA + 3600 * 5, fromA);
  await actor.enterBar(fromB + 3600 * 5, fromB);
  // Assert: from ごとにロックを再導出する（stale ロックを引き回さない）。
  assert.equal(forming.calls[0].range, 2, 'fromA セッションのロック（120/60）');
  assert.equal(forming.calls[1].range, 4, 'fromB セッションのロック（240/60）');
});

test('lock retries when causal history was unavailable at first call (no permanent null memoization)', async () => {
  const from = 1782900000 - (1782900000 % 86400);
  const now1 = from + 3600 * 5;
  const now2 = from + 3600 * 6;
  const forming = fakeFormingClient([BASE_FULL]);
  // 1 回目は履歴未ロード（[]）→ 2 回目以降に履歴が揃う（読み込み順の競合を模す）。
  let loaded = false;
  const { actor } = makeActor({
    formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make,
    ctxTo: now1, getCandles: () => (loaded ? lockHistory(from) : []),
  });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive', bins: 60 });
  // Act: 履歴なしで 1 バー目（bins フォールバック）→ 履歴ロード後の 2 バー目。
  await actor.enterBar(now1, from);
  loaded = true;
  await actor.enterBar(now2, from);
  // Assert: 失敗（null）を恒久メモ化せず、履歴が揃った時点でロックが復帰する（レビュー🟡）。
  assert.notEqual(forming.calls[0].resmode, 'range', '履歴未ロードの初回は bins フォールバック');
  assert.equal(forming.calls[1].resmode, 'range', '履歴が揃った次回はロックへ復帰（再試行）');
  assert.equal(forming.calls[1].range, 2, '復帰後の barw は因果履歴レンジ/bins');
});

// --- ISSUE-049（縮退グリッド中のブランク描画フラッシュの回帰禁止）: enterBar の skipDegenerateDraw は
//   自身の描画だけをスキップし、直後の feedTick/settleTick の throttle 描画には縮退ガードが無かったため、
//   縮退 accumulator（[0,1]・全 bin ゼロ）の空 snapshot が setProfile され MP バーが毎バー全消滅していた。
//   修正: 縮退状態（_gridDegenerate）を状態化し、縮退中は feedTick/settleTick も描画抑止（前回描画保持）。
//   growTo の実グリッド確定で解除して描画再開する。 ---

const DEG_RESP = {
  ok: true, formingStart: 1782950400, ticks: [], baseFine: [0], baseKmin: 0,
  activeTable: [[1]], priceMin: 0, priceMax: 1, nBins: 1, gridW: 10, vaPct: VA_PCT_DEFAULT, now: 1782950400,
};

test('feedTick does NOT draw while grid is degenerate (blank [0,1] snapshot never rendered — ISSUE-049)', async () => {
  const dayStart = 1782950400;
  const forming = fakeFormingClient([DEG_RESP]);
  const facc = fakeAccumulatorFactory();
  const clock = fakeClock([0, 1000, 2000, 3000]);
  const { actor, primitive } = makeActor({
    formingClient: forming, makeAccumulator: facc.make, ctxTo: dayStart, now: clock, throttleMs: 100,
  });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  await actor.enterBar(dayStart); // 縮退 → enterBar は描画スキップ（既存）
  const before = primitive.profiles.length;
  // Act: throttle 間隔を超えて tick を供給（従来はここで空 snapshot が描かれていた＝Red）。
  actor.feedTick(dayStart + 10, 71000);
  actor.feedTick(dayStart + 20, 71010);
  actor.feedTick(dayStart + 30, 71020);
  // Assert: 縮退グリッド中は feedTick も描画しない（前回描画保持＝バー消滅フラッシュを出さない）。
  assert.equal(primitive.profiles.length, before, '縮退中の feedTick は空 snapshot を描かない');
});

test('settleTick does NOT draw while grid is degenerate (ISSUE-049)', async () => {
  const dayStart = 1782950400;
  const forming = fakeFormingClient([DEG_RESP]);
  const { actor, primitive } = makeActor({
    formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make, ctxTo: dayStart,
  });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  await actor.enterBar(dayStart); // 縮退
  const before = primitive.profiles.length;
  // Act
  actor.settleTick();
  // Assert
  assert.equal(primitive.profiles.length, before, '縮退中の settleTick は空 snapshot を描かない');
});

test('growTo that stays degenerate does NOT draw; real grid re-enables drawing (feedTick resumes — ISSUE-049)', async () => {
  const dayStart = 1782950400;
  const GROWN = {
    ok: true, formingStart: dayStart, ticks: [[dayStart + 100, 71000]],
    baseFine: [0, 0, 0], baseKmin: 7100, activeTable: [[1]],
    priceMin: 71000, priceMax: 71050, nBins: 3, gridW: 10, vaPct: VA_PCT_DEFAULT, now: dayStart + 100,
  };
  const forming = fakeFormingClient([DEG_RESP, DEG_RESP, GROWN]);
  const clock = fakeClock([0, 1000, 2000, 3000, 4000]);
  const { actor, primitive } = makeActor({
    formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make, ctxTo: dayStart,
    now: clock, throttleMs: 100,
  });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  await actor.enterBar(dayStart);          // 縮退 #1
  const before = primitive.profiles.length;
  await actor.growTo(dayStart + 50);       // まだ縮退（データ無バー）→ 描かない（従来は空描画＝Red）
  assert.equal(primitive.profiles.length, before, '縮退のままの growTo は描かない（前回描画保持）');
  await actor.growTo(dayStart + 100);      // 実グリッド確定 → 描画再開
  assert.equal(primitive.profiles.length, before + 1, '実グリッド確定の growTo は描画する');
  actor.feedTick(dayStart + 110, 71010);   // throttle 経過後の feedTick も描画する（解除確認）
  assert.equal(primitive.profiles.length, before + 2, '実グリッド確定後は feedTick 描画が再開する');
});

// --- ISSUE-120: 非増分 src（zp）は再生中（growing push）でも forming へ倒さず as-of-T 全期間 refresh ---
//   present の _isIncremental と同じ能力ゲート（mp_source_capability.incremental）を replay の push 分岐にも
//   適用する（ゲート規則の対称化）。forming は src='dwell' 強制＋当日窓のため、zp のまま forming へ倒すと
//   「選択 src と異なる原子の当日窓」に黙って差し替わる（ユーザー報告「zp 全期間が当日しか出ない」の実体）。

test('ISSUE-120 refresh: src=zp は growing push 中でも forming せず基底 refresh（as-of-T・src 維持）', async () => {
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor, client } = makeActor({
    formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make, ctxTo: 5000,
  });
  actor.setParams({ mode: 'normal', src: 'zp' });
  actor._enabled = true;
  actor.applyGrowthState({ growing: true });
  assert.equal(actor.isGrowingPush(), true, '成長 push 状態（ゲートは src 能力で判定される）');
  // Act
  await actor.refresh();
  // Assert
  assert.equal(forming.calls.length, 0, 'forming（dwell 原子・当日窓）を駆動しない');
  assert.ok(client.calls.length >= 1, '基底 refresh の fetchProfile が走る');
  const last = client.calls.at(-1);
  assert.equal(last.to, 5000, 'as-of-T（to=カーソル）で取得＝因果は維持');
  assert.equal(last.src, 'zp', '選択 src=zp を維持（dwell へ差し替えない）');
  assert.equal(last.from, undefined, 'from（当日窓）を載せない＝全期間');
});

test('ISSUE-120/124 enterBar/growTo: src=zp は forming せず、非ブロッキング＋最新 coalesce で基底 refresh', async () => {
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor, client } = makeActor({
    formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make, ctxTo: 7000,
  });
  actor.setParams({ mode: 'normal', src: 'zp' });
  actor._enabled = true;
  actor.applyGrowthState({ growing: true });
  // Act: driver がバー入場/グリッド拡張で直接呼ぶ経路（ISSUE-124: await は即時解放される）。
  //   in-flight 中の連打は busy+pending の coalesce で fetch 最大 2 回（最新カーソル勝ち）に畳まれる
  //   （await を挟まず同期連打＝再生中の高速バー送りを模擬）。
  actor.enterBar(6900);
  actor.growTo(6950);
  actor.enterBar(6960);
  await new Promise((r) => setTimeout(r, 0)); // fire-and-forget の消化を待つ
  await new Promise((r) => setTimeout(r, 0));
  // Assert
  assert.equal(forming.calls.length, 0, 'enterBar/growTo とも forming を駆動しない');
  assert.ok(client.calls.length >= 1 && client.calls.length <= 2,
    `coalesce（busy+pending）で fetch は 1〜2 回に畳まれる（実測 ${client.calls.length}）`);
  assert.equal(client.calls.at(-1).src, 'zp');
  assert.equal(client.calls.at(-1).to, 6960,
    'ISSUE-129: to は単一時計＝実行時点の最新リビール秒（enterBar(6960)）で as-of 取得');
});

test('ISSUE-120 対称性: 増分 src（dwell）は従来どおり forming 経路（回帰なし）', async () => {
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor, client } = makeActor({
    formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make, ctxTo: 5000,
  });
  actor.setParams({ mode: 'normal', src: 'dwell' });
  actor._enabled = true;
  actor.applyGrowthState({ growing: true });
  const fetchBefore = client.calls.length;
  // Act
  await actor.refresh();
  // Assert
  assert.equal(forming.calls.length, 1, 'dwell は forming（因果 base）で描く（従来どおり）');
  assert.equal(client.calls.length, fetchBefore, '全期間 fetchProfile は呼ばない（従来どおり）');
});

// --- ISSUE-129（単一時計）: 非増分 src（zp）×成長 push 中は fetch の to をリビール秒へ細粒度化する ---
//   to はリプレイの現在時刻そのもの（as-seen-at-t の T）。backend は now=to で境界日をライブと同一の
//   経過分クランプ＝1D でも当日 z が日内で成長する。静止は ctx.to がそのまま時計（上書き不要）。
//   増分 src・時計未確定は上書きしない（従来 URL 不変・present は基底 _clockExtra()={} のまま非波及）。

test('ISSUE-129 単一時計: enterBar の now がリビール秒として fetch context の to に載る（ctx.to を上書き）', async () => {
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor, client } = makeActor({
    formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make, ctxTo: 7000,
  });
  actor.setParams({ mode: 'normal', src: 'zp' });
  actor._enabled = true;
  actor.applyGrowthState({ growing: true });
  actor.enterBar(6900);
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  assert.ok(client.calls.length >= 1, '基底 refresh の fetchProfile が走る');
  assert.equal(client.calls.at(-1).to, 6900, 'enterBar(now)＝リビール秒が to（単一時計）として載る');
  assert.equal(client.calls.at(-1).asof, undefined, '旧 asof パラメータは送らない（廃止）');
});

test('ISSUE-129 単一時計: feedTick のリビール秒で to が前進し、順不同 tick は巻き戻さない（単調）', async () => {
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor, client } = makeActor({
    formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make, ctxTo: 7000,
  });
  actor.setParams({ mode: 'normal', src: 'zp' });
  actor._enabled = true;
  actor.applyGrowthState({ growing: true });
  actor.enterBar(6900);
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  actor.feedTick(6910, 71000);
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(client.calls.at(-1).to, 6910, 'feedTick(sec) が単一時計 to を前進させ再計算を発火する');
  actor.feedTick(6905, 71000); // 順不同（過去秒）
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(client.calls.at(-1).to, 6910, '過去秒 tick では時計を巻き戻さない（単調）');
});

test('ISSUE-129 単一時計: 後退スクラブは enterBar(now=旧カーソル) が時計を巻き戻す（stale 未来時計防止）', async () => {
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor, client } = makeActor({
    formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make, ctxTo: 7000,
  });
  actor.setParams({ mode: 'normal', src: 'zp' });
  actor._enabled = true;
  actor.applyGrowthState({ growing: true });
  actor.enterBar(6900);
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  actor.enterBar(5000); // 後退
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(client.calls.at(-1).to, 5000, 'enterBar は単一時計を直接更新（後退も反映）');
});

test('ISSUE-129 単一時計: 非成長（static）refresh は ctx.to がそのまま時計（stale _clockSec で上書きしない）', async () => {
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor, client } = makeActor({
    formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make, ctxTo: 5000,
  });
  actor.setParams({ mode: 'normal', src: 'zp' });
  actor._enabled = true;
  actor._clockSec = 4900; // 直前の成長の残骸（stale）があっても
  await actor.refresh(); // growing=false（static）
  assert.ok(client.calls.length >= 1);
  assert.equal(client.calls.at(-1).to, 5000,
    'static の to は ctx.to（＝untilTime＝T）のまま＝backend が now=to で部分集計（stale _clockSec は参照しない）');
  assert.equal(client.calls.at(-1).asof, undefined, '旧 asof パラメータは送らない（廃止）');
});

test('ISSUE-129 単一時計: カーソル未確定（to=null・restore 前）は to を載せない（実時計＝従来 URL 不変）', async () => {
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor, client } = makeActor({
    formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make, ctxTo: undefined,
  });
  actor.setParams({ mode: 'normal', src: 'zp' });
  actor._enabled = true;
  await actor.refresh(); // growing=false（static）・cursor なし
  if (client.calls.length >= 1) {
    assert.equal(client.calls.at(-1).to, undefined, 'cursor 不在では to なし＝backend は実時計（ライブ同一）');
  }
});

test('ISSUE-127/129: 小数秒の tick（1分OHLC 合成等）でも to は整数秒で送る（backend 契約 UNIX 秒）', async () => {
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor, client } = makeActor({
    formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make, ctxTo: 7000,
  });
  actor.setParams({ mode: 'normal', src: 'zp' });
  actor._enabled = true;
  actor.applyGrowthState({ growing: true });
  actor.feedTick(6910.6237624, 71000); // 合成 tick の小数秒
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(client.calls.at(-1).to, 6910,
    '小数秒は floor した整数を送る（backend _parse_to は小数文字列を None へ落とすため）');
});

test('ISSUE-130 単一時計×1D: enterBar（バー入場）はラベルでなくセッション始端を時計にする（3h 先出し防止）', async () => {
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor, client } = makeActor({
    formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make,
    ctxTo: 1777248000, ctxTimeframe: '1D',
  });
  actor.setParams({ mode: 'normal', src: 'zp' });
  actor._enabled = true;
  actor.applyGrowthState({ growing: true });
  actor.enterBar(1777248000); // 2026-04-27(月) ラベル＝セッション 3h 経過点
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(client.calls.at(-1).to, 1777237200,
    'バー入場の時計＝セッション始端（日曜 21:00 UTC）＝未リビール分（窓先頭 3h）を先出ししない');
  actor.growTo(1777240000, undefined); // 足内 tick 前進（実リビール秒）
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(client.calls.at(-1).to, 1777240000, 'growTo は実リビール秒のまま（写像しない）');
});

test('ISSUE-129 単一時計: 増分 src（dwell）は時計上書きの対象外（to=ctx.to のまま・従来 URL 不変）', async () => {
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor, client } = makeActor({
    formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make, ctxTo: 5000,
  });
  actor.setParams({ mode: 'normal', src: 'dwell' });
  actor._enabled = true;
  actor._clockSec = 4900; // 時計が残っていても dwell へは波及しない
  await actor.refresh(); // growing=false（static）
  assert.ok(client.calls.length >= 1);
  assert.equal(client.calls.at(-1).to, 5000, 'dwell の to は ctx.to のまま（上書きなし）');
  assert.equal(client.calls.at(-1).asof, undefined, '旧 asof パラメータは送らない（廃止）');
});

test('ISSUE-129 対称性: 増分 src（dwell）の feedTick は as-of 再計算を発火しない（従来経路のみ）', async () => {
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor, client } = makeActor({
    formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make, ctxTo: 5000,
  });
  actor.setParams({ mode: 'normal', src: 'dwell' });
  actor._enabled = true;
  actor.applyGrowthState({ growing: true });
  const before = client.calls.length;
  actor.feedTick(4900, 71000); // accumulator 未確立 → 従来どおり no-op
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(client.calls.length, before, 'dwell は fetchProfile を発火しない（回帰なし）');
});

// --- ISSUE-138（日別 sessions の足内育成）: リプレイの日別（sessions）プロファイルを、ライブ同一の
//   「受信（リビール）した価格ティック毎に当日プロファイルが再計算される」挙動へ是正する。sessions は
//   accumulator/forming を使わず as-of refresh（機構A）で育てるため、非増分 as-of coalesce 戦略（zp 系）へ
//   合流させ、単一時計 _clockSec を feedTick で足内前進・onLiveTick（バー入場 reveal seam）で巻き戻す。
//   非 sessions 経路（normal/replay/ticklive）の feedTick/_clockExtra は 1 バイトも変えない（回帰ガード）。

test('ISSUE-138 sessions 単一時計: feedTick のリビール秒で _clockExtra().to が tick 秒へ前進する（単調）', async () => {
  const { actor } = makeActor({ ctxTo: 7000, ctxTimeframe: '1h' });
  actor.setParams({ mode: 'sessions', src: 'zp' }); // 日別・非増分（isGrowingPush=false／!_sessions で従来ゲート外）
  actor._enabled = true;
  // Act: 足内リビール tick を供給する（バー時刻 7000 に対し実リビール秒 6910）。
  actor.feedTick(6910, 71000);
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  // Assert: sessions でも単一時計が前進し _clockExtra().to（fetch の to seam）に tick 秒が載る。
  assert.equal(actor._clockExtra().to, 6910, 'sessions の feedTick(sec) が単一時計 to を tick 秒へ前進させる');
  actor.feedTick(6905, 71000); // 順不同（過去秒）は巻き戻さない（単調）
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(actor._clockExtra().to, 6910, 'sessions でも過去秒 tick は時計を巻き戻さない（単調）');
});

test('ISSUE-138 sessions 足内育成: feedTick が coalesce 経由で refresh を発火し to=tick 秒・sessions=1 で fetch する', async () => {
  const { actor, client } = makeActor({ ctxTo: 7000, ctxTimeframe: '1h' });
  actor.setParams({ mode: 'sessions', src: 'zp' });
  actor._enabled = true;
  const before = client.calls.length;
  // Act
  actor.feedTick(6910, 71000);
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  // Assert: 足内 tick 毎に as-of coalesce refresh が発火し、バー粒度でなくリビール秒で日別を再集計する。
  assert.ok(client.calls.length > before, 'sessions の feedTick が as-of coalesce refresh（fetchProfile）を発火する');
  assert.equal(client.calls.at(-1).to, 6910, 'refresh の fetch to はリビール tick 秒（足内・バー粒度でない）');
  assert.equal(client.calls.at(-1).sessions, true, 'sessions=1 を維持したまま足内 to で当日を再集計する');
});

test('ISSUE-138 sessions 逆スクラブ: バー入場（onLiveTick reveal seam）が単一時計を後退フロンティアへ巻き戻し to が後退に追随する', async () => {
  // getContext().to を可変にしてスクラブ（カーソル後退）を模す。
  const ctx = { datasetRef: 'jp225_tick', timeframe: '1h', to: 7000 };
  const client = fakeClient();
  const actor = new ReplayMarketProfileActor({
    client, primitive: fakePrimitive(), mainSeries: fakeMainSeries(),
    getContext: () => ctx, now: () => 0,
  });
  actor.setParams({ mode: 'sessions', src: 'zp' });
  actor._enabled = true;
  // 足内で単一時計を未来（6950）へ前進させる。
  actor.feedTick(6950, 71000);
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(actor._clockExtra().to, 6950, '前提: 足内 tick で時計が 6950 へ前進している');
  // Act: 後退スクラブ（カーソルを 5000 へ）。reveal seam（recomputeAllApplied→onLiveRecompute）が毎バー
  //   onLiveTick を呼ぶ＝バー入場でリビールフロンティア（getContext().to）へ時計を巻き戻す。
  ctx.to = 5000;
  await actor.onLiveTick();
  // Assert: stale な未来時計（6950）を引き回さず to が後退に追随する（未来リーク防止）。
  assert.equal(actor._clockExtra().to, 5000, 'バー入場で単一時計が後退フロンティア（5000）へ巻き戻る');
  assert.equal(client.calls.at(-1).to, 5000, 'onLiveTick の as-of refresh も to=5000（後退追随・未来リークなし）');
  assert.equal(client.calls.at(-1).sessions, true, 'sessions=1 を維持する');
});

test('ISSUE-138 回帰ガード: 非 sessions（normal ticklive/dwell 増分 push）は _clockExtra()={} 不変・feedTick は増分 accumulator 経路（as-of 時計に載らない）', async () => {
  const forming = fakeFormingClient([BASE_FULL]);
  const facc = fakeAccumulatorFactory();
  const { actor, client } = makeActor({ formingClient: forming, makeAccumulator: facc.make, ctxTo: 1030 });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' }); // 非 sessions・増分 push（isGrowingPush=true・src=dwell）
  await actor.enterBar(1030);
  const fetchBefore = client.calls.length;
  // Act
  actor.feedTick(1040, 1005);
  await new Promise((r) => setTimeout(r, 0));
  // Assert: 非 sessions は as-of 時計に載らない（_clockExtra は空＝URL byte 不変）。
  assert.deepEqual(actor._clockExtra(), {}, '非 sessions ticklive は _clockExtra()={}（従来不変）');
  // 増分 push の feedTick は accumulator を進めるのみで as-of refresh（fetchProfile）を発火しない。
  assert.equal(client.calls.length, fetchBefore, '非 sessions 増分 push の feedTick は as-of refresh を発火しない（従来経路）');
});
