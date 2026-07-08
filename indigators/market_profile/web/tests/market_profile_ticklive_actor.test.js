// market_profile_actor.js の tick 逐次成長（ticklive）拡張の検証。
//
// 設計入力: Phase2 設計 mp_ticklive_design.md「変更 front actor」。
//   onLiveTick / _enterTicklive / _exitTicklive / _applyMode('ticklive') を追加（既存メソッド不変）。
//   非増分（ticklive OFF or formingClient 未注入）は onLiveTick === refresh 委譲（byte-identical・回帰ゼロ）。
//   増分（ticklive ON）は forming client → DwellAccumulator へ増分 → primitive.setProfile(snapshot())。
//   client / primitive / formingClient / accumulator は Fake 注入。構造: Arrange-Act-Assert。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { MarketProfileActor } from '../js/adapter/front/market_profile_actor.js';
import { DwellAccumulator } from '../js/domain/market_profile_dwell_accumulator.js';

const REFRESH_PROFILE = { bins: [{ price: 1, tpo: 1, norm: 1 }], poc: 1, va_low: 1, va_high: 1 };

function fakeClient(result = REFRESH_PROFILE) {
  const calls = [];
  return { calls, async fetchProfile(ctx) { calls.push(ctx); return result; } };
}

function fakePrimitive() {
  return {
    profiles: [], visibles: [],
    setProfile(p) { this.profiles.push(p); },
    setVisible(v) { this.visibles.push(v); },
  };
}

// scripted forming client（呼び出しごとに responses を順に返す）。
function fakeFormingClient(responses) {
  const calls = [];
  let i = 0;
  return {
    calls,
    async fetchForming(args) {
      calls.push(args);
      const r = responses[Math.min(i, responses.length - 1)];
      i += 1;
      return r;
    },
  };
}

// 観測可能な Fake accumulator。factory は生成物を created[] へ push する。
function fakeAccumulatorFactory() {
  const created = [];
  const make = () => {
    const acc = {
      init(cfg) { this.cfg = cfg; this.ticks = []; },
      addTick(sec, mid) { this.ticks.push([sec, mid]); },
      snapshot() { return { poc: this.ticks.length, bins: [], _snap: true }; },
    };
    created.push(acc);
    return acc;
  };
  return { make, created };
}

const BASE_FULL = {
  ok: true, formingStart: 1000, ticks: [[1010, 1005], [1020, 1015]],
  baseFine: [0, 0, 0], baseKmin: 100, activeTable: [[1]], priceMin: 1000, priceMax: 1100,
  nBins: 3, gridW: 10, now: 1030,
};

function makeActor({ formingClient, makeAccumulator, client, primitive } = {}) {
  const c = client ?? fakeClient();
  const p = primitive ?? fakePrimitive();
  const actor = new MarketProfileActor({
    client: c,
    primitive: p,
    getContext: () => ({ datasetRef: 'jp225_tick', timeframe: '1h', limit: 1500 }),
    formingClient,
    makeAccumulator,
  });
  return { actor, client: c, primitive: p };
}

// --- 非増分 = refresh 委譲の同一性（回帰ゼロ） ---
test('onLiveTick delegates to refresh (identical fetch) when ticklive is OFF even with a formingClient injected', async () => {
  // Arrange: formingClient 注入済みだが ticklive OFF（既定）。
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor, client } = makeActor({ formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make });
  await actor.setEnabled(true); // setEnabled は内部 refresh を 1 回行う。
  const before = client.calls.length;
  // Act
  await actor.onLiveTick();
  // Assert: onLiveTick は refresh と同一の /market_profile 取得へ委譲（forming client は呼ばれない）。
  assert.equal(client.calls.length, before + 1);
  assert.deepEqual(client.calls[before], { datasetRef: 'jp225_tick', timeframe: '1h', limit: 1500 });
  assert.equal(forming.calls.length, 0, 'ticklive OFF では forming client を呼ばない');
});

test('onLiveTick is a no-op-equivalent to refresh when disabled (byte-identical guard)', async () => {
  // Arrange: 無効時 refresh は no-op（fetch しない）。onLiveTick も同一。
  const { actor, client } = makeActor();
  // Act
  await actor.onLiveTick();
  // Assert
  assert.equal(client.calls.length, 0);
});

// --- 増分: 初回 onLiveTick = _enterTicklive（base=1 取得→init→畳み込み→描画） ---
test('first onLiveTick in ticklive mode enters ticklive: fetches base=1, inits accumulator, folds ticks, draws snapshot', async () => {
  // Arrange
  const forming = fakeFormingClient([BASE_FULL]);
  const facc = fakeAccumulatorFactory();
  const { actor, primitive } = makeActor({ formingClient: forming, makeAccumulator: facc.make });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  const drawnBefore = primitive.profiles.length;
  // Act
  await actor.onLiveTick();
  // Assert: base=1 取得 → init(base/range) → 全 tick を addTick → snapshot を setProfile。
  const enterCall = forming.calls[forming.calls.length - 1];
  assert.equal(enterCall.base, 1);
  assert.equal(facc.created.length, 1);
  assert.deepEqual(facc.created[0].cfg.baseFine, [0, 0, 0]);
  assert.equal(facc.created[0].cfg.baseKmin, 100);
  assert.equal(facc.created[0].cfg.formingStart, 1000);
  assert.deepEqual(facc.created[0].ticks, [[1010, 1005], [1020, 1015]]);
  assert.equal(primitive.profiles.length, drawnBefore + 1);
  assert.equal(primitive.profiles[primitive.profiles.length - 1]._snap, true);
});

// --- 増分: 2 回目 onLiveTick = base=0 尾部を addTick して snapshot 反映 ---
test('subsequent onLiveTick fetches base=0 tail and applies incremental ticks to the snapshot', async () => {
  // Arrange
  const BASE2 = { ok: true, formingStart: 1000, ticks: [[1030, 1025]], now: 1040 };
  const forming = fakeFormingClient([BASE_FULL, BASE2]);
  const facc = fakeAccumulatorFactory();
  const { actor, primitive } = makeActor({ formingClient: forming, makeAccumulator: facc.make });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  await actor.onLiveTick(); // enter
  const drawnAfterEnter = primitive.profiles.length;
  // Act
  await actor.onLiveTick(); // incremental
  // Assert: 2 回目は base=0 尾部取得（since=前回最終 sec）で addTick、同一 accumulator を継続。
  const incCall = forming.calls[forming.calls.length - 1];
  assert.equal(incCall.base, 0);
  assert.equal(incCall.since, 1020, 'since=前回最終 tick 秒（尾部差分）');
  assert.equal(facc.created.length, 1, 'rollover でなければ accumulator は再生成しない');
  assert.deepEqual(facc.created[0].ticks, [[1010, 1005], [1020, 1015], [1030, 1025]]);
  assert.equal(primitive.profiles.length, drawnAfterEnter + 1);
});

// --- MP-04: ticklive の forming 取得は src=dwell を強制する（UI の src 選択に依らず dwell 原子固定） ---
test('onLiveTick forces src=dwell in forming args regardless of the UI-selected src', async () => {
  // Arrange: UI で src=candle（非 dwell）を選択中でも forming 取得は dwell に揃うべき。
  const forming = fakeFormingClient([BASE_FULL]);
  const facc = fakeAccumulatorFactory();
  const { actor } = makeActor({ formingClient: forming, makeAccumulator: facc.make });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive', src: 'candle' });
  // Act
  await actor.onLiveTick(); // enter（base=1 取得）
  // Assert: forming 取得の src は常に 'dwell'（ticklive は dwell 原子限定＝base と live 増分の原子一致）。
  assert.equal(forming.calls[forming.calls.length - 1].src, 'dwell', 'ticklive は src=dwell を強制する');
  assert.equal(forming.calls[forming.calls.length - 1].base, 1);
});

// --- rollover: formingStart 変化で _enterTicklive を再実行（base 再取得・reset） ---
test('onLiveTick re-enters ticklive on rollover when formingStart changes', async () => {
  // Arrange
  const ROLL = { ok: true, formingStart: 2000, ticks: [[2005, 1030]], now: 2010 };
  const BASE_NEW = {
    ok: true, formingStart: 2000, ticks: [[2005, 1030]],
    baseFine: [0, 0, 0], baseKmin: 100, activeTable: [[1]], priceMin: 1000, priceMax: 1100,
    nBins: 3, gridW: 10, now: 2010,
  };
  const forming = fakeFormingClient([BASE_FULL, ROLL, BASE_NEW]);
  const facc = fakeAccumulatorFactory();
  const { actor } = makeActor({ formingClient: forming, makeAccumulator: facc.make });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  await actor.onLiveTick(); // enter (formingStart 1000)
  // Act
  await actor.onLiveTick(); // detects formingStart 2000 → re-enter (base=1)
  // Assert: rollover で accumulator を再生成、新 base を base=1 で取得。
  assert.equal(facc.created.length, 2, 'rollover で accumulator を再生成');
  assert.equal(forming.calls[forming.calls.length - 1].base, 1, 'rollover 時は base=1 で再取得');
  assert.equal(facc.created[1].cfg.formingStart, 2000);
});

// --- null forming = 前回描画保持（非破壊） ---
test('onLiveTick keeps the previous drawing when forming fetch yields null', async () => {
  // Arrange: enter 済みで 2 回目が null。
  const forming = fakeFormingClient([BASE_FULL, null]);
  const facc = fakeAccumulatorFactory();
  const { actor, primitive } = makeActor({ formingClient: forming, makeAccumulator: facc.make });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  await actor.onLiveTick(); // enter
  const drawn = primitive.profiles.length;
  // Act
  await actor.onLiveTick(); // null
  // Assert: setProfile を追加しない（前回保持）。
  assert.equal(primitive.profiles.length, drawn);
});

// --- MP-05: base=1 応答が空 profile（必須フィールド欠損）なら init へ NaN を入れず前回描画を保持 ---
test('onLiveTick keeps the previous drawing when base=1 forming lacks range/grid fields (no NaN)', async () => {
  // Arrange: 無ローソク等で priceMin/priceMax/nBins/gridW/baseFine を欠く空 profile を返す。
  const EMPTY_BASE = { ok: true, formingStart: 1000, ticks: [], now: 1010 }; // baseFine 等が無い。
  const forming = fakeFormingClient([EMPTY_BASE]);
  const facc = fakeAccumulatorFactory();
  const { actor, primitive } = makeActor({ formingClient: forming, makeAccumulator: facc.make });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  const drawn = primitive.profiles.length;
  // Act
  await actor.onLiveTick(); // enter だが base フィールド欠損 → presence ガードで前回保持。
  // Assert: accumulator を生成せず（NaN init を回避）、setProfile を追加しない（前回描画保持）。
  assert.equal(facc.created.length, 0, '必須フィールド欠損時は accumulator を init しない（NaN 混入回避）');
  assert.equal(primitive.profiles.length, drawn, '前回描画を保持（既存 fetch null と同じ非破壊）');
});

test('onLiveTick treats explicit null range fields as missing (Number(null)=0 must not slip through)', async () => {
  // Arrange: backend が空 profile で price_min 等を JSON null で返す形（Number(null)===0 で誤通過し得る）。
  const NULL_BASE = {
    ok: true, formingStart: 1000, ticks: [], baseFine: [], baseKmin: null,
    activeTable: [[1]], priceMin: null, priceMax: null, nBins: null, gridW: null, now: 1010,
  };
  const forming = fakeFormingClient([NULL_BASE]);
  const facc = fakeAccumulatorFactory();
  const { actor, primitive } = makeActor({ formingClient: forming, makeAccumulator: facc.make });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  const drawn = primitive.profiles.length;
  // Act
  await actor.onLiveTick();
  // Assert: null 必須フィールドは欠損扱い（accumulator を init しない・前回描画保持）。
  assert.equal(facc.created.length, 0, 'null レンジ/グリッドは欠損扱い（Number(null)=0 の誤通過を禁止）');
  assert.equal(primitive.profiles.length, drawn);
});

test('onLiveTick with a real accumulator does not emit NaN prices for an empty base profile', async () => {
  // Arrange: 実 DwellAccumulator。空 base（欠損）でも snapshot が NaN 価格を出さないこと（描画非追加）。
  const EMPTY_BASE = { ok: true, formingStart: 1000, ticks: [], now: 1010 };
  const forming = fakeFormingClient([EMPTY_BASE]);
  const { actor, primitive } = makeActor({
    formingClient: forming, makeAccumulator: () => new DwellAccumulator(),
  });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  const drawn = primitive.profiles.length;
  // Act
  await actor.onLiveTick();
  // Assert: NaN 価格の snapshot を描画しない（前回保持）。
  assert.equal(primitive.profiles.length, drawn);
});

// --- 排他遷移: ticklive は replay/sessions と排他。相互に解除される ---
test('_applyMode ticklive is exclusive with replay/sessions and toggles back off', async () => {
  // Arrange
  const forming = fakeFormingClient([BASE_FULL]);
  const { actor } = makeActor({ formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make });
  await actor.setEnabled(true);
  // Act: ticklive ON
  actor.setParams({ mode: 'ticklive' });
  // Assert: ticklive ON・replay/sessions OFF。
  assert.equal(actor.isTicklive(), true);
  assert.equal(actor.isReplay(), false);
  assert.equal(actor.isSessions(), false);
  // Act: replay へ切替 → ticklive OFF。
  actor.setParams({ mode: 'replay' });
  assert.equal(actor.isTicklive(), false);
  assert.equal(actor.isReplay(), true);
});

// --- OFF 復帰: normal へ戻すと onLiveTick は refresh へ委譲する ---
test('after switching back to normal, onLiveTick delegates to refresh again (OFF 復帰)', async () => {
  // Arrange
  const forming = fakeFormingClient([BASE_FULL, BASE_FULL]);
  const { actor, client } = makeActor({ formingClient: forming, makeAccumulator: fakeAccumulatorFactory().make });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  await actor.onLiveTick(); // incremental enter
  const formingCallsAfterEnter = forming.calls.length;
  const clientCallsAfterEnter = client.calls.length;
  // Act: normal 復帰後の onLiveTick
  actor.setParams({ mode: 'normal' });
  await actor.onLiveTick();
  // Assert: forming client はもう呼ばれず、refresh（/market_profile）へ委譲する。
  assert.equal(forming.calls.length, formingCallsAfterEnter, 'OFF 後は forming を呼ばない');
  assert.equal(client.calls.length, clientCallsAfterEnter + 1, 'refresh へ委譲する');
});

// --- 実 DwellAccumulator を用いた end-to-end: snapshot が primitive へ反映される ---
test('end-to-end with a real DwellAccumulator: onLiveTick draws a real snapshot', async () => {
  // Arrange: all-active table・range [1000,1100]/3 bins。
  const table = Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => 1));
  const payload = {
    ok: true, formingStart: 1704074400,
    ticks: [[1704074460, 1005], [1704074520, 1015]],
    baseFine: [0, 0, 0], baseKmin: 100, activeTable: table,
    priceMin: 1000, priceMax: 1100, nBins: 3, gridW: 10, now: 1704074600,
  };
  const forming = fakeFormingClient([payload]);
  const { actor, primitive } = makeActor({
    formingClient: forming, makeAccumulator: () => new DwellAccumulator(),
  });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'ticklive' });
  // Act
  await actor.onLiveTick();
  // Assert: 実 snapshot（bins 配列・poc）が primitive へ反映される。
  const drawn = primitive.profiles[primitive.profiles.length - 1];
  assert.ok(Array.isArray(drawn.bins) && drawn.bins.length === 3);
  assert.equal(drawn.n_bins, 3);
  // tick0(1005) dwell=60（1704074520-1704074460）→ bin0。末尾 tick は dwell 0。
  assert.equal(drawn.bins[0].tpo, 60);
});

// ===========================================================================
// Model A 直交化（Phase 2）: 成長は表示モードから独立し applyGrowthState({growing}) の
//   単一信号で駆動する。mode='normal'（非 ticklive）でも growing=true なら成長エンジンが
//   forming を起動し（FOLLOW+normal 成長＝CP-5）、growing=false なら onLiveTick は refresh へ委譲する。
//   これが「表示モード×成長状態の直交化」の実証（present #2 FOLLOW 成長の非退行契約）。
// ===========================================================================

test("applyGrowthState({growing:true}) drives forming growth even in mode='normal' (FOLLOW+normal 成長)", async () => {
  // Arrange: 表示モードは normal（非 ticklive）。forming client / accumulator を注入。
  const forming = fakeFormingClient([BASE_FULL]);
  const factory = fakeAccumulatorFactory();
  const { actor, primitive } = makeActor({ formingClient: forming, makeAccumulator: factory.make });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'normal' }); // 表示モード normal（ticklive にしない）。
  assert.equal(actor.isTicklive(), false, '前提: mode=normal は ticklive ではない');
  forming.calls.length = 0;
  // Act: growing 信号のみで成長を ON にする（mode は normal のまま）。
  actor.applyGrowthState({ growing: true });
  await actor.onLiveTick();
  // Assert: forming エンドポイントが叩かれ（成長エンジン起動）、snapshot が primitive へ反映される。
  assert.ok(forming.calls.length >= 1, 'growing=true は mode=normal でも forming を起動する（直交化）');
  assert.equal(factory.created.length, 1, 'accumulator を init（_enterTicklive 経路）');
  const drawn = primitive.profiles.at(-1);
  assert.ok(drawn && drawn._snap === true, 'snapshot が描画される');
});

test('applyGrowthState({growing:false}) reverts growth: onLiveTick delegates to refresh (static)', async () => {
  // Arrange: normal + growing=true で成長させた後、growing=false へ（ANALYSIS 相当）。
  const forming = fakeFormingClient([BASE_FULL, BASE_FULL]);
  const factory = fakeAccumulatorFactory();
  const { actor, client } = makeActor({ formingClient: forming, makeAccumulator: factory.make });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'normal' });
  actor.applyGrowthState({ growing: true });
  await actor.onLiveTick();       // 成長（forming）。
  const formingBefore = forming.calls.length;
  const clientBefore = client.calls.length;
  // Act: growing OFF（static 復帰）。
  actor.applyGrowthState({ growing: false });
  await actor.onLiveTick();
  // Assert: onLiveTick は /market_profile refresh へ委譲し、forming を追加で叩かない（成長停止）。
  assert.equal(forming.calls.length, formingBefore, 'growing=false は forming を叩かない（成長停止）');
  assert.equal(client.calls.length, clientBefore + 1, 'onLiveTick は refresh(/market_profile)へ委譲する');
});

test('applyGrowthState is idempotent for the same state (no accumulator reset on repeat growing=true)', async () => {
  // Arrange
  const forming = fakeFormingClient([BASE_FULL, BASE_FULL]);
  const factory = fakeAccumulatorFactory();
  const { actor } = makeActor({ formingClient: forming, makeAccumulator: factory.make });
  await actor.setEnabled(true);
  actor.setParams({ mode: 'normal' });
  actor.applyGrowthState({ growing: true });
  await actor.onLiveTick(); // _enterTicklive → accumulator #1。
  // Act: 同状態（growing:true）を再適用しても累積器を捨てない（冪等）。
  actor.applyGrowthState({ growing: true });
  await actor.onLiveTick(); // 尾部 addTick（base=0）＝再 enter しない。
  // Assert: accumulator は 1 つのまま（同状態再適用でリセットされない）。
  assert.equal(factory.created.length, 1, '同状態 growing=true 再適用は accumulator を作り直さない（冪等）');
});
