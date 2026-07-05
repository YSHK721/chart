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

// --- _buildFormingArgs override: now(=getContext().to) と GrowthWindow 委譲（絞った窓・視認性） ---
//   視認性修正: normal 再生=全期間累積だと 1 本ぶんの成長が極小で見えないため、GrowthWindow(normal) の
//   from を「絞った窓」min(当日始まり, formingStart) へ戻す（ユーザー確定）。日中足は当日始端が base 下限。
test('_buildFormingArgs merges now(=getContext().to) and delegates from to GrowthWindow (normal→絞った窓 当日始端)', () => {
  const to = 1782985000; // 日途中
  const daySt = Math.floor(to / 86400) * 86400; // 当日始端
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

// --- enterBar（ticklive）: base=1/src=dwell/now=T/from=絞った窓(当日始端)で forming 取得し base 描画 ---
test('enterBar (ticklive) fetches base=1/src=dwell/now=T/from=絞った窓(当日始端), inits accumulator, draws base only', async () => {
  const now = 1782985000;
  const daySt = Math.floor(now / 86400) * 86400; // 当日始端
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
    activeTable: [[1]], priceMin: 0, priceMax: 1, nBins: 1, gridW: 10, now: dayStart,
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
    activeTable: [[1]], priceMin: 0, priceMax: 1, nBins: 1, gridW: 10, now: dayStart,
  };
  const GROWN = {
    ok: true, formingStart: dayStart, ticks: [[dayStart + 100, 71000], [dayStart + 200, 71050]],
    baseFine: [0, 0, 0, 0, 0, 0], baseKmin: 7100, activeTable: [[1]],
    priceMin: 71000, priceMax: 71050, nBins: 3, gridW: 10, now: nowMid,
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
    activeTable: table, priceMin: 0, priceMax: 1, nBins: 1, gridW: 10, now: dayStart,
  };
  const lo = 71000; const hi = 71050; const gw = 10;
  const kmin = Math.floor(lo / gw);
  const size = Math.floor(hi / gw) - kmin + 1;
  const GROWN = {
    ok: true, formingStart: dayStart, ticks: [[dayStart + 100, lo], [dayStart + 200, hi]],
    baseFine: new Array(size).fill(0), baseKmin: kmin, activeTable: table,
    priceMin: lo, priceMax: hi, nBins: 3, gridW: gw, now: nowMid,
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
  acc.init({ baseFine: [0, 0, 0], baseKmin: 100, activeTable: table, priceMin: 1000, priceMax: 1100, nBins: 3, gridW: 10, formingStart: 1704074400 });
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
