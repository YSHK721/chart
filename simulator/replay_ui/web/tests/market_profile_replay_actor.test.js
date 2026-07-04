// market_profile_replay_actor.js（slim MarketProfileReplayActor）の検証。
//
// 設計入力: Phase2 arch 確定「slim MarketProfileReplayActor（present actor 非移植）」。
//   公開 API は enterBar(now)/feedTick(sec,mid)/settleTick()/setEnabled(on)/isEnabled() のみ。
//   - enterBar(now): fetchForming({...getContext(), src:'dwell', base:1, now}) → _hasBaseFields 欠損は
//       null 扱い前回保持 → accumulator.init → primitive.setProfile(base のみ)。await で ready 保証。rollover 兼。
//   - feedTick(sec,mid): _enabled && _accumulator のとき addTick→throttle(120ms)で snapshot→primitive。HTTP 無。
//   - settleTick(): 確定時 最終 snapshot 強制。
//   - setEnabled(on): 初回のみ mainSeries.attachPrimitive（非提供時 skip）→ primitive.setVisible(on)。
//   client/primitive/formingClient/accumulator/mainSeries は Fake 注入。now（clock）を注入し throttle を決定論化。
//   構造: Arrange-Act-Assert。
//
// ★この時点で market_profile_replay_actor.js は未実装（Red）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { MarketProfileReplayActor } from '../js/adapter/front/market_profile_replay_actor.js';
import { DwellAccumulator } from '../js/domain/market_profile_dwell_accumulator.js';

const BASE_FULL = {
  ok: true, formingStart: 1000, ticks: [],
  baseFine: [0, 0, 0], baseKmin: 100, activeTable: [[1]], priceMin: 1000, priceMax: 1100,
  nBins: 3, gridW: 10, now: 1030,
};

function fakePrimitive() {
  return {
    profiles: [], visibles: [], attached: 0,
    setProfile(p) { this.profiles.push(p); },
    setVisible(v) { this.visibles.push(v); },
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

// clock: 呼ぶたびに指定配列を順に返す（throttle を決定論化）。
function fakeClock(seq) {
  let i = 0;
  return () => seq[Math.min(i++, seq.length - 1)];
}

function makeActor({ formingClient, makeAccumulator, primitive, mainSeries, now, throttleMs = 120 } = {}) {
  const p = primitive ?? fakePrimitive();
  const ms = mainSeries ?? fakeMainSeries();
  const actor = new MarketProfileReplayActor({
    primitive: p,
    mainSeries: ms,
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1h', limit: 1500 }),
    formingClient,
    makeAccumulator,
    now: now ?? (() => 0),
    throttleMs,
  });
  return { actor, primitive: p, mainSeries: ms };
}

// --- setEnabled / isEnabled / attach once ---
test('isEnabled defaults false and setEnabled(true) attaches primitive once and shows it', () => {
  // Arrange
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor, primitive, mainSeries } = makeActor({ formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make });
  assert.equal(actor.isEnabled(), false);
  // Act
  actor.setEnabled(true);
  actor.setEnabled(true); // 冪等: attach は 1 回のみ。
  // Assert
  assert.equal(actor.isEnabled(), true);
  assert.equal(mainSeries.attachCount, 1, 'attachPrimitive は初回のみ');
  assert.equal(primitive.visibles[primitive.visibles.length - 1], true);
});

test('setEnabled(false) hides the primitive', () => {
  const { actor, primitive } = makeActor({ formingClient: fakeFormingClient([BASE_FULL]), makeAccumulator: fakeAccumulatorFactory().make });
  actor.setEnabled(true);
  actor.setEnabled(false);
  assert.equal(actor.isEnabled(), false);
  assert.equal(primitive.visibles[primitive.visibles.length - 1], false);
});

test('setEnabled skips attach when mainSeries lacks attachPrimitive (backward compatible)', () => {
  const actor = new MarketProfileReplayActor({
    primitive: fakePrimitive(), mainSeries: {}, getContext: () => ({}),
    formingClient: fakeFormingClient([BASE_FULL]), makeAccumulator: fakeAccumulatorFactory().make,
  });
  // Act / Assert: throw しない（非提供時 skip）。
  actor.setEnabled(true);
  assert.equal(actor.isEnabled(), true);
});

// --- enterBar: base=1 / src=dwell / now=T を取得し base のみ描画（await ready） ---
test('enterBar fetches base=1 with src=dwell and now=T, inits accumulator, draws base only', async () => {
  // Arrange
  const forming = fakeFormingClient([BASE_FULL]);
  const facc = fakeAccumulatorFactory();
  const { actor, primitive } = makeActor({ formingClient: forming, makeAccumulator: facc.make });
  actor.setEnabled(true);
  // Act
  await actor.enterBar(1000);
  // Assert: forming 取得は base=1・src=dwell・now=T（因果）。base fields で init。base のみ描画。
  const call = forming.calls[forming.calls.length - 1];
  assert.equal(call.base, 1);
  assert.equal(call.src, 'dwell');
  assert.equal(call.now, 1000);
  assert.equal(facc.created.length, 1);
  assert.deepEqual(facc.created[0].cfg.baseFine, [0, 0, 0]);
  assert.equal(facc.created[0].cfg.formingStart, 1000);
  assert.deepEqual(facc.created[0].ticks, [], 'enterBar は forming tick を畳まない（feedTick が育てる＝二重計上回避）');
  assert.equal(primitive.profiles.length, 1, 'base のみ 1 回描画');
});

// --- enterBar: セッション窓 MP の base 下限 from=floor(now,86400)（当日始まり）を fetchForming へ渡す ---
test('enterBar passes from=floor(now,86400) (session-window base 下限=当日始まり) to fetchForming', async () => {
  // Arrange: now を UTC 日境界でない時刻に置き、当日始まり(floor)へ丸まることを固定する。
  const forming = fakeFormingClient([BASE_FULL]);
  const facc = fakeAccumulatorFactory();
  const { actor } = makeActor({ formingClient: forming, makeAccumulator: facc.make });
  actor.setEnabled(true);
  const now = 1782985000;                 // 2026-07-01..02 相当（日途中）。
  const expectedFrom = Math.floor(now / 86400) * 86400; // == 当日始まり（UTC 日境界・秒）。
  // Act
  await actor.enterBar(now);
  // Assert: fetchForming 引数に from=当日始まり が含まれ、combined=[当日始まり, now) の古典的 MP になる。
  const call = forming.calls[forming.calls.length - 1];
  assert.equal(call.from, expectedFrom);
  assert.equal(call.now, now, 'now(因果 T)は不変');
});

test('enterBar is a no-op when disabled (does not fetch)', async () => {
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor } = makeActor({ formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make });
  await actor.enterBar(1000); // disabled
  assert.equal(forming.calls.length, 0);
});

// --- rollover: enterBar 再実行で accumulator を作り直し reset ---
test('enterBar re-init creates a fresh accumulator (rollover reset)', async () => {
  const NEXT = { ...BASE_FULL, formingStart: 2000, now: 2030 };
  const forming = fakeFormingClient([BASE_FULL, NEXT]);
  const facc = fakeAccumulatorFactory();
  const { actor } = makeActor({ formingClient: forming, makeAccumulator: facc.make });
  actor.setEnabled(true);
  await actor.enterBar(1000);
  actor.feedTick(1010, 1005); // 前バーの成長
  // Act: 次バーへ rollover
  await actor.enterBar(2000);
  // Assert: accumulator を作り直す（前バーの forming をリセット）。
  assert.equal(facc.created.length, 2);
  assert.equal(facc.created[1].cfg.formingStart, 2000);
  assert.deepEqual(facc.created[1].ticks, [], 'rollover 後は forming ゼロから');
});

// --- null / 欠損 base = 前回描画保持（非破壊） ---
test('enterBar keeps previous drawing when forming is null (non-disruptive)', async () => {
  const forming = fakeFormingClient([BASE_FULL, null]);
  const facc = fakeAccumulatorFactory();
  const { actor, primitive } = makeActor({ formingClient: forming, makeAccumulator: facc.make });
  actor.setEnabled(true);
  await actor.enterBar(1000);
  const drawn = primitive.profiles.length;
  // Act
  await actor.enterBar(2000); // null
  // Assert: accumulator 再生成せず・描画追加せず（前回保持）。
  assert.equal(facc.created.length, 1);
  assert.equal(primitive.profiles.length, drawn);
});

test('enterBar keeps previous drawing when base fields are missing (no NaN init)', async () => {
  const EMPTY = { ok: true, formingStart: 2000, ticks: [], now: 2010 }; // baseFine/range 欠損
  const forming = fakeFormingClient([BASE_FULL, EMPTY]);
  const facc = fakeAccumulatorFactory();
  const { actor, primitive } = makeActor({ formingClient: forming, makeAccumulator: facc.make });
  actor.setEnabled(true);
  await actor.enterBar(1000);
  const drawn = primitive.profiles.length;
  await actor.enterBar(2000);
  assert.equal(facc.created.length, 1, '欠損時は accumulator を init しない（NaN 回避）');
  assert.equal(primitive.profiles.length, drawn);
});

test('enterBar treats explicit null range fields as missing (Number(null)=0 must not slip through)', async () => {
  const NULLB = { ok: true, formingStart: 2000, ticks: [], baseFine: [], baseKmin: null,
    activeTable: [[1]], priceMin: null, priceMax: null, nBins: null, gridW: null, now: 2010 };
  const forming = fakeFormingClient([BASE_FULL, NULLB]);
  const facc = fakeAccumulatorFactory();
  const { actor } = makeActor({ formingClient: forming, makeAccumulator: facc.make });
  actor.setEnabled(true);
  await actor.enterBar(1000);
  await actor.enterBar(2000);
  assert.equal(facc.created.length, 1, 'null レンジ/グリッドは欠損扱い');
});

// --- feedTick: enabled+accumulator で addTick、throttle で snapshot ---
test('feedTick accumulates every tick but throttles snapshots to the interval', async () => {
  // Arrange: clock 0(enter),0,50,120,130 … throttle=120。
  const forming = fakeFormingClient([BASE_FULL]);
  const facc = fakeAccumulatorFactory();
  const clock = fakeClock([0, 0, 50, 120, 121]);
  const { actor, primitive } = makeActor({ formingClient: forming, makeAccumulator: facc.make, now: clock, throttleMs: 120 });
  actor.setEnabled(true);
  await actor.enterBar(1000); // base 描画（clock 0）
  const afterEnter = primitive.profiles.length;
  // Act: 4 tick を投入（clock 0,50,120,121）。
  actor.feedTick(1010, 1005); // t=0: 直近snap=0 → throttle 抑制（差0<120）
  actor.feedTick(1020, 1015); // t=50: 差50<120 抑制
  actor.feedTick(1030, 1025); // t=120: 差120>=120 → snapshot
  actor.feedTick(1040, 1035); // t=121: 差(121-120)<120 抑制
  // Assert: 全 tick が addTick される（O(1)累積）。snapshot は throttle 通過分のみ。
  assert.deepEqual(facc.created[0].ticks, [[1010, 1005], [1020, 1015], [1030, 1025], [1040, 1035]]);
  assert.equal(primitive.profiles.length, afterEnter + 1, 'throttle を通過した 1 回だけ snapshot 反映');
});

test('feedTick is a no-op when disabled (addTick stop / MP OFF non-interference)', async () => {
  const forming = fakeFormingClient([BASE_FULL]);
  const facc = fakeAccumulatorFactory();
  const { actor, primitive } = makeActor({ formingClient: forming, makeAccumulator: facc.make });
  actor.setEnabled(true);
  await actor.enterBar(1000);
  const drawn = primitive.profiles.length;
  actor.setEnabled(false); // 停止/OFF
  // Act
  actor.feedTick(1010, 1005);
  // Assert: addTick せず・描画追加せず（停止で addTick 停止・MP OFF 無干渉）。
  assert.deepEqual(facc.created[0].ticks, []);
  assert.equal(primitive.profiles.length, drawn);
});

test('feedTick is a no-op before enterBar (no accumulator yet)', () => {
  const { actor, primitive } = makeActor({ formingClient: fakeFormingClient([BASE_FULL]), makeAccumulator: fakeAccumulatorFactory().make });
  actor.setEnabled(true);
  actor.feedTick(1010, 1005); // enterBar 前
  assert.equal(primitive.profiles.length, 0);
});

// --- settleTick: 最終 snapshot 強制（throttle 無視） ---
test('settleTick forces a final snapshot regardless of throttle', async () => {
  const forming = fakeFormingClient([BASE_FULL]);
  const facc = fakeAccumulatorFactory();
  const clock = fakeClock([0, 1, 2, 3, 4]);
  const { actor, primitive } = makeActor({ formingClient: forming, makeAccumulator: facc.make, now: clock, throttleMs: 120 });
  actor.setEnabled(true);
  await actor.enterBar(1000);
  actor.feedTick(1010, 1005); // throttle 抑制（差 <120）
  const before = primitive.profiles.length;
  // Act
  actor.settleTick();
  // Assert: throttle を無視して最終 snapshot を反映。
  assert.equal(primitive.profiles.length, before + 1);
});

test('settleTick is a no-op when disabled or before enterBar', () => {
  const { actor, primitive } = makeActor({ formingClient: fakeFormingClient([BASE_FULL]), makeAccumulator: fakeAccumulatorFactory().make });
  actor.settleTick(); // before enter
  actor.setEnabled(true);
  // enterBar 前は accumulator 無し
  actor.settleTick();
  assert.equal(primitive.profiles.length, 0);
});

// --- end-to-end: 実 DwellAccumulator で feedTick が逐次成長を描く ---
test('end-to-end with a real DwellAccumulator: enterBar draws base, feedTick grows tpo per tick', async () => {
  // Arrange: all-active・range [1000,1100]/3bins。
  const table = Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => 1));
  const payload = {
    ok: true, formingStart: 1704074400, ticks: [],
    baseFine: [0, 0, 0], baseKmin: 100, activeTable: table,
    priceMin: 1000, priceMax: 1100, nBins: 3, gridW: 10, now: 1704074400,
  };
  const forming = fakeFormingClient([payload]);
  const { actor, primitive } = makeActor({
    formingClient: forming, makeAccumulator: () => new DwellAccumulator(),
    now: (() => { let t = 0; return () => (t += 1000); })(), throttleMs: 120,
  });
  actor.setEnabled(true);
  await actor.enterBar(1704074400);
  // Act: 2 tick を投入（dwell=gap 60）。
  actor.feedTick(1704074460, 1005);
  actor.feedTick(1704074520, 1015);
  actor.settleTick();
  // Assert: 実 snapshot（bins 3・tick0 gap=60 が bin0 へ）。
  const drawn = primitive.profiles[primitive.profiles.length - 1];
  assert.equal(drawn.n_bins, 3);
  assert.equal(drawn.bins[0].tpo, 60);
});
