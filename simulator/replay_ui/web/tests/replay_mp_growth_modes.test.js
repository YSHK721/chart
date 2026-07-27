// replay.js setupReplay の MP tick-live 足内成長「全モード」駆動検証（統合・fake harness で closure を実駆動）。
//
// 設計入力: #mp-growth-all-modes。MP tick-live の足内成長を「最新足更新」の全モードで機能させる。
//   - every_tick / ohlc_1min: 合成 dwell secs（stream.js が窓等分生成）で feedTick が育つ。
//   - math: 足内推移なし → その時間足の完成プロファイルを settleGrowTo(winEnd) で一度描く（成長なし）。
//   - 確定形（settle）は全モードで real_ticks と同一: settleGrowTo(winEnd) → backend 実 dwell 全窓 fold へ収束。
//     ＝合成 dwell は transient のみ・settle=truth（各足の完成 MP は real_ticks と一致）。
//
// 検証の要（DoD 回帰）: MP actor は settle 時に growTo(now) で backend fold を再構築し feedTick の合成 dwell を
//   破棄する（settle grid = fold(now) の純関数）。本 spy はその意味論を忠実に模し、
//   「合成モードの settle == real_ticks の settle（同一バー・同一 winEnd）」を固定する。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { setupReplay } from '../js/replay.js';

function fakeEl(extra = {}) {
  return {
    _l: {}, value: '', min: 0, max: 0, textContent: '', title: '', hidden: false, disabled: false,
    style: {}, dataset: {}, options: [], innerHTML: '',
    classList: { toggle() {}, add() {}, remove() {} },
    appendChild() {}, removeChild() {},
    addEventListener(ev, fn) { (this._l[ev] ||= []).push(fn); },
    set onclick(fn) { this._onclick = fn; }, get onclick() { return this._onclick; },
    set oninput(fn) { this._oninput = fn; }, get oninput() { return this._oninput; },
    ...extra,
  };
}

function fakeDoc(mode) {
  const els = {
    'rp-speed': fakeEl({ value: '1' }),
    'rp-mode': fakeEl({ value: mode }),
    'rp-prev': fakeEl(),
  };
  return {
    getElementById: (id) => (els[id] || (els[id] = fakeEl())),
    querySelectorAll: () => [],
    createElement: () => fakeEl(),
    addEventListener() {},
    _els: els,
  };
}

function fakeChart() {
  const ts = { fitContent() {}, setVisibleLogicalRange() {}, getVisibleLogicalRange() { return null; } };
  return { timeScale: () => ts, panes: () => [], chartElement: () => null };
}

function fakeController() {
  return {
    _timeframe: '1D', _recentBars: 0,
    setUntilTime() {}, isRecomputing() { return false; },
    async recomputeAllApplied({ preRender } = {}) { if (preRender) preRender(); },
    async recomputeFormingLatest() {},
  };
}

const CANDLES = [
  { time: 100, open: 1, high: 2, low: 0.5, close: 1.5 },
  { time: 200, open: 1.5, high: 2.5, low: 1, close: 2 },
];
// 最新足 time=200・1D → intrabarWindow はセッション窓（ISSUE-130）: winEnd = nextSessionDayStart(200)
//   ＝1970-01-01 22:00 UTC（冬時間 EST）＝79200。
const WIN_END = 79200;
// real_ticks の最終実 tick 秒 t_k。**実データ条件 t_k < winEnd**（足終端より前で最終 tick が着く）を再現し、
//   残差を assume-away しない（前回は t_k=winEnd に合わせて等価を仮定していた）。是正案 B は settle を全モード
//   winEnd 統一するため、t_k < winEnd でも real_ticks は winEnd で settle する（t_k では settle しない）。
const T_K = 70000; // 70000 < 79200（winEnd）。

// /intraday の応答をモード別に返す（url の mode= で分岐）。
function fakeFetch() {
  return async (url) => ({
    ok: true,
    async json() {
      const u = String(url);
      if (u.startsWith('/candles')) return { ok: true, candles: CANDLES };
      if (u.startsWith('/intraday')) {
        if (u.includes('mode=real_ticks')) return { ok: true, m1: [], ticks: [1.6, 1.8, 2.0], tick_secs: [300, 40000, T_K] };
        // every_tick / ohlc_1min は M1 を返す（synthM1 / flattenM1 の点列源）。
        return { ok: true, m1: [[1.5, 2.5, 1, 2], [1.7, 2.4, 1.3, 2.1]] };
      }
      return { ok: true };
    },
  });
}

// MP actor spy（actor の settle 意味論を忠実模写）:
//   - enterBar/growTo は grid = fold(now)（backend fold の純関数）を再構築し feedTick の寄与を破棄する。
//   - feedTick は transient 記録のみ（grid を変えない＝settle=truth）。
//   - settleTick は現 grid を確定 snapshot として記録する。
function spyMarketProfile() {
  const fold = (now) => `grid@${now}`; // backend 実 dwell 全窓 fold の決定論モデル（now の純関数）。
  return {
    _en: true,
    grid: null,
    settledGrid: null,
    calls: { enter: [], grow: [], feed: [], settle: 0 },
    isEnabled() { return this._en; },
    setEnabled(v) { this._en = !!v; },
    isTickInGrid() { return true; }, // ループ中の growTo 発火を抑止し settle 経路を分離。
    async enterBar(t) { this.calls.enter.push(t); this.grid = fold(t); },
    async growTo(now) { this.calls.grow.push(now); this.grid = fold(now); }, // 合成寄与を破棄し fold(now) を再構築。
    feedTick(s, m) { this.calls.feed.push([s, m]); }, // transient（grid 不変）。
    settleTick() { this.calls.settle += 1; this.settledGrid = this.grid; },
  };
}

async function driveMode(mode) {
  globalThis.window = globalThis.window || {};
  const mp = spyMarketProfile();
  const doc = fakeDoc(mode);
  await setupReplay({
    chart: fakeChart(),
    mainSeries: { attachPrimitive() {}, update() {} },
    controller: fakeController(),
    renderer: { setCandles() {} },
    datasetRef: 'jp225_tick',
    recentBars: 1500,
    document: doc,
    fetchImpl: fakeFetch(),
    marketProfile: mp,
  });
  // 現在バー（最新足 time=200）で 1 回だけ足内形成を実駆動（__rpAnimateOnce は promise を返す）。
  await globalThis.window.__rpAnimateOnce();
  return mp;
}

// --- every_tick: 合成 secs で feedTick が育ち、settle は winEnd へ収束 ---
test('every_tick grows via synth-dwell feedTick and settles at winEnd', async () => {
  const mp = await driveMode('every_tick');
  assert.ok(mp.calls.feed.length > 0, 'every_tick は合成 secs で feedTick が呼ばれ育つ');
  assert.equal(mp.calls.settle, 1, 'settleTick が確定で 1 回');
  assert.ok(mp.calls.grow.includes(WIN_END), 'settleGrowTo(winEnd) で確定窓へ収束');
  assert.equal(mp.settledGrid, `grid@${WIN_END}`, '完成 MP は winEnd の backend fold');
});

// --- ohlc_1min: 合成 secs で feedTick が育ち、settle は winEnd へ収束 ---
test('ohlc_1min grows via synth-dwell feedTick and settles at winEnd', async () => {
  const mp = await driveMode('ohlc_1min');
  assert.ok(mp.calls.feed.length > 0, 'ohlc_1min は合成 secs で feedTick が呼ばれ育つ');
  assert.equal(mp.calls.settle, 1);
  assert.ok(mp.calls.grow.includes(WIN_END), 'settleGrowTo(winEnd) で確定窓へ収束');
  assert.equal(mp.settledGrid, `grid@${WIN_END}`);
});

// --- math: 成長なし・完成プロファイルを一度描く（settleGrowTo(winEnd)→settleTick） ---
test('math shows the completed profile once (no growth) via settleGrowTo(winEnd)', async () => {
  const mp = await driveMode('math');
  assert.equal(mp.calls.feed.length, 0, 'math は feedTick を呼ばない（足内推移なし）');
  assert.equal(mp.calls.settle, 1, '完成集計を一度だけ描く');
  assert.ok(mp.calls.grow.includes(WIN_END), 'settleGrowTo(winEnd) で完成集計へ収束');
  assert.equal(mp.settledGrid, `grid@${WIN_END}`);
});

// --- open_only: transient は始値1点のまま（成長なし）・確定形は winEnd で完成足（math と同じ振る舞い） ---
test('open_only keeps minimal transient (start-only, no growth) yet settles at winEnd (completed bar)', async () => {
  const mp = await driveMode('open_only');
  assert.equal(mp.calls.feed.length, 0, 'open_only は feedTick を呼ばない（始値のみ・育ち方 minimal）');
  assert.equal(mp.calls.settle, 1, '確定形を一度だけ描く');
  assert.ok(mp.calls.grow.includes(WIN_END), 'secs 空でも settleGrowTo(winEnd) が発火（settle を growth から分離）');
  assert.equal(mp.settledGrid, `grid@${WIN_END}`, 'open_only の完成 MP は winEnd の backend fold（base のままにしない）');
});

// --- DoD 回帰（是正案 B・強化）: 実データ条件 t_k < winEnd でも全モードの settle が winEnd で byte 一致 ---
//   前回は real_ticks の最終 tick を winEnd に合わせて等価を「仮定」していた（残差を assume-away）。今回は
//   t_k(=80000) < winEnd(=86600) の実条件で real_ticks が t_k ではなく winEnd で settle することを固定する。
test('regression: with t_k < winEnd, all 5 modes (real_ticks/every_tick/ohlc_1min/open_only/math) settle at winEnd (byte-identical completed MP)', async () => {
  const rt = await driveMode('real_ticks');
  const et = await driveMode('every_tick');
  const oc = await driveMode('ohlc_1min');
  const oo = await driveMode('open_only');
  const mt = await driveMode('math');
  // real_ticks は t_k(<winEnd) では settle せず winEnd で全窓 fold へ収束（是正案 B・完全足として正しい）。
  assert.ok(rt.calls.grow.includes(WIN_END), 'real_ticks も settleGrowTo(winEnd) で確定');
  assert.ok(!rt.calls.grow.includes(T_K), 'real_ticks は t_k では settle しない（残差を assume-away しない）');
  assert.equal(rt.settledGrid, `grid@${WIN_END}`, 'real_ticks の完成 MP は winEnd の backend fold');
  // 全 5 モードの完成 MP が byte 一致（合成 dwell/始値のみは transient・settle=truth）。open_only も含む。
  assert.equal(et.settledGrid, rt.settledGrid, 'every_tick の完成 MP == real_ticks（byte 一致）');
  assert.equal(oc.settledGrid, rt.settledGrid, 'ohlc_1min の完成 MP == real_ticks（byte 一致）');
  assert.equal(oo.settledGrid, rt.settledGrid, 'open_only の完成 MP == real_ticks（byte 一致）');
  assert.equal(mt.settledGrid, rt.settledGrid, 'math の完成 MP == real_ticks（byte 一致）');
});

// --- real_ticks の transient 成長は不変（実 tick_secs で育つ）・変わるのは settle 収束点のみ ---
test('real_ticks still grows on real tick_secs (transient growth unchanged); only settle converges to winEnd', async () => {
  const rt = await driveMode('real_ticks');
  assert.ok(rt.calls.feed.length > 0, 'real_ticks は実 tick_secs で feedTick（成長は不変）');
  assert.deepEqual(rt.calls.feed.map((f) => f[0]), [300, 40000, T_K], '実 tick_secs をそのまま供給（transient byte 不変）');
  assert.equal(rt.calls.settle, 1);
});
