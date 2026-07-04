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

const BASE_FULL = {
  ok: true, formingStart: 1000, ticks: [],
  baseFine: [0, 0, 0], baseKmin: 100, activeTable: [[1]], priceMin: 1000, priceMax: 1100,
  nBins: 3, gridW: 10, now: 1030,
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
} = {}) {
  const p = primitive ?? fakePrimitive();
  const ms = mainSeries ?? fakeMainSeries();
  const c = client ?? fakeClient();
  const actor = new ReplayMarketProfileActor({
    client: c,
    primitive: p,
    mainSeries: ms,
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1h', to: ctxTo }),
    formingClient,
    makeAccumulator,
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

// --- _buildFormingArgs override: now(=getContext().to) と from(=当日始まり) を合流 ---
test('_buildFormingArgs merges now(=getContext().to) and from(=当日始まり) into base args', () => {
  const to = 1782985000; // 日途中
  const { actor } = makeActor({ formingClient: fakeFormingClient([BASE_FULL]), makeAccumulator: fakeAccumulatorFactory().make, ctxTo: to });
  actor.setParams({ mode: 'ticklive', bins: '30', va: 0.9 });
  // Act
  const args = actor._buildFormingArgs({ base: 1, since: null });
  // Assert: 基底の src=dwell/base/since + now(=to) + from(=当日始まり) + params(bins/va)。
  assert.equal(args.src, 'dwell');
  assert.equal(args.base, 1);
  assert.equal(args.now, to, 'now=getContext().to（因果 T）');
  assert.equal(args.from, Math.floor(to / 86400) * 86400, 'from=当日始まり（floor(now,86400)）');
  assert.equal(args.bins, '30');
  assert.equal(args.va, 0.9);
});

// --- enterBar（ticklive）: base=1/src=dwell/now=T/from=当日始まり で forming 取得し base のみ描画 ---
test('enterBar (ticklive) fetches base=1/src=dwell/now=T/from=当日始まり, inits accumulator, draws base only', async () => {
  const now = 1782985000;
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
  assert.equal(call.from, Math.floor(now / 86400) * 86400);
  assert.equal(facc.created.length, 1);
  assert.deepEqual(facc.created[0].ticks, [], 'enterBar は forming tick を畳まない（feedTick が育てる）');
  assert.equal(primitive.profiles.length, drawnBefore + 1, 'base のみ 1 回描画');
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
    priceMin: 1000, priceMax: 1100, nBins: 3, gridW: 10, now,
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
