// replay_forming_plan_wiring.test.js — 足内一括計算の駆動配線（ISSUE-232・fake harness で実駆動）。
//
// 背景（実測 2026-08-01・実 UI）: 再生中はローソクが 7–9ms ごとに動くのに、指標は毎ティック
//   /compute を往復するため 95–142ms 遅れて追いつく（かつ throttle で十数ティックに 1 回）。
//
// 本テストが固定する契約:
//   1. 計画がある時点では、指標の反映がローソク更新と **同一同期ブロック** で起きる（間に await 無し）。
//   2. [ISSUE-300 で改訂] 計画は足内の値の **唯一の源** である。間に合わなければ **待つ**
//      （旧規約「待たずにその場計算へ落ちる」は実測で逆効果＝二重計算がサーバを飽和させ、
//      次の計画をさらに遅らせていた）。ティック粒度は全足で維持し、降格させない。
//   3. 先のバーの計画を **複数足ぶん** 先読みする（1 足先だけだと生成が 1 足を超えた瞬間から
//      永久に間に合わない）。
//   4. 計画が末尾ティックを含むなら、確定着地の往復（settle）を発行しない。

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
  const els = { 'rp-speed': fakeEl({ value: '1' }), 'rp-mode': fakeEl({ value: mode }) };
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

const CANDLES = [
  { time: 100, open: 10, high: 12, low: 9, close: 11 },
  { time: 200, open: 11, high: 14, low: 10, close: 13 },
  { time: 300, open: 13, high: 15, low: 12, close: 14 },
];
const TICKS = [11, 12, 13];   // 1 バーの足内ティック列（ohlc_1min 相当の短い列）

// controller の最小 fake。足内一括計算に必要な 2 面（対象列挙・同期反映）を持つ。
function fakeController(log, { supportsPlan = true } = {}) {
  const c = {
    _timeframe: '5m', _recentBars: 0,
    setUntilTime() {}, isRecomputing() { return false; },
    setTimeframeApplier() {},
    async recomputeAllApplied({ preRender } = {}) { if (preRender) preRender(); },
    async recomputeFormingLatest() { log.push('その場計算'); },
  };
  if (supportsPlan) {
    c.formingSeqTargets = () => ([
      { instanceId: 'ma#1', indicatorId: 'moving_averages', variant: 'default', params: {} },
    ]);
    c.applyFormingStep = (step) => log.push(`指標:${Object.keys(step).join(',')}`);
  }
  return c;
}

// fetch の fake。/compute（POST）は steps を返す。遅延を注入できる（先読みの間に合わない状況の再現）。
function makeFetch(log, { computeDelayMs = 0, failCompute = false } = {}) {
  return async (url, opts) => {
    const u = String(url);
    if (u === '/compute') {
      const body = JSON.parse(opts.body);
      log.push(`compute:${body.mode}:${(body.formingSeq || []).length}`);
      if (computeDelayMs) {
        await new Promise((r) => setTimeout(r, computeDelayMs));
      }
      if (failCompute) {
        return { ok: false, status: 500, async json() { return { ok: false, error: { message: 'boom' } }; } };
      }
      const steps = (body.formingSeq || []).map(() => ([{ name: 'MA', data: [{ time: 1, value: 1 }] }]));
      if (body.mode === 'latest_seq_multi') {
        const results = {};
        for (const spec of body.specs || []) { results[spec.instanceId] = steps; }
        return { ok: true, async json() { return { ok: true, generation: 0, results }; } };
      }
      return { ok: true, async json() { return { ok: true, generation: 0, steps }; } };
    }
    return {
      ok: true,
      async json() {
        if (u.startsWith('/candles')) return { ok: true, candles: CANDLES };
        if (u.startsWith('/intraday')) return { ok: true, m1: [], ticks: TICKS.map((p, i) => ({ t: 100 + i, p })), tick_secs: [] };
        return { ok: true, days: [] };
      },
    };
  };
}

// 起動直後は末尾バー（＝次バーが存在せず先読み対象も無い）。1 本戻して「次バーがある」状態にする。
async function boot({ mode = 'real_ticks', log, fetchImpl, supportsPlan = true, stepBack = true } = {}) {
  globalThis.window = globalThis.window || {};
  const doc = fakeDoc(mode);
  const controller = fakeController(log, { supportsPlan });
  const handle = await setupReplay({
    chart: fakeChart(),
    mainSeries: { attachPrimitive() {}, update() {} },
    controller,
    renderer: { setCandles() {}, updateLastCandle: () => log.push('ローソク') },
    datasetRef: 'jp225_tick',
    recentBars: 1500,
    document: doc,
    fetchImpl,
    marketProfile: null,
  });
  await handle.enable();   // リプレイモードへ（先読みはリプレイ層が起きている時のみ発火する）
  if (stepBack) {
    await doc._els['rp-prev']._onclick();   // bar=末尾-1（先読み対象の次バーが存在する位置）
  }
  return { handle, doc, controller };
}

const settle = () => new Promise((r) => setTimeout(r, 60));

test('計画がある時点では指標がローソクと同一同期ブロックで反映される（ISSUE-232）', async () => {
  const log = [];
  await boot({ log, fetchImpl: makeFetch(log) });
  await settle();                       // 起動時の先読みを完了させる
  log.length = 0;
  await globalThis.window.__rpAnimateOnce();
  // 指標の反映は必ず「直前のローソク更新」と対で起きる（＝同一同期ブロック・間に await 無し）。
  const drawn = log.indexOf('指標:ma#1');
  assert.ok(drawn > 0, `指標が計画から反映されていない（log: ${log.join(' → ')}）`);
  assert.equal(log[drawn - 1], 'ローソク', `指標の直前がローソク更新でない（実際: ${log.slice(Math.max(0, drawn - 2), drawn + 1).join(' → ')}）`);
  for (let i = 0; i < log.length; i++) {
    if (log[i] === '指標:ma#1') {
      assert.equal(log[i - 1], 'ローソク', `${i} 番目の指標反映がローソクと対になっていない`);
    }
  }
  assert.equal(log.includes('その場計算'), false, '計画があるのにその場計算（遅延経路）を使っている');
});

test('計画が間に合わなければ待つ（ティック粒度を落とさない・ISSUE-300）', async () => {
  const log = [];
  // compute を遅延させる＝先読みが終わらないまま再生に入る状況。
  await boot({ log, fetchImpl: makeFetch(log, { computeDelayMs: 300 }) });
  log.length = 0;
  await globalThis.window.__rpAnimateOnce();
  // 待ってでも計画の値で描く（＝その足だけ足内更新なし、には降格しない）。
  assert.ok(log.includes('指標:ma#1'), `計画を待たずに足内更新を落としている（log: ${log.join(' → ')}）`);
  // 二重計算（ティックごとのその場計算）は発行しない。これが 30 秒/足の主因だった。
  assert.equal(log.includes('その場計算'), false, 'その場計算へ落ちている（二重計算＝ISSUE-300 の原因）');
});

test('足内の値は 1 要求で全指標ぶん取る（ISSUE-300・固定費を指標数ぶん払わない）', async () => {
  const log = [];
  await boot({ log, fetchImpl: makeFetch(log) });
  await settle();
  log.length = 0;
  await globalThis.window.__rpAnimateOnce();
  await settle();
  const seq = log.filter((l) => l.startsWith('compute:latest_seq'));
  assert.ok(seq.every((l) => l.startsWith('compute:latest_seq_multi')),
    `指標ごとの単発 latest_seq が残っている（${seq.join(' , ')}）`);
});

test('一括計算が失敗しても再生は継続し着地で描く（fail-open・ISSUE-232）', async () => {
  const log = [];
  await boot({ log, fetchImpl: makeFetch(log, { failCompute: true }) });
  await settle();
  log.length = 0;
  await globalThis.window.__rpAnimateOnce();
  assert.ok(log.includes('ローソク'), '失敗時に再生が止まっている');
  assert.ok(log.includes('その場計算'), '失敗時に従来経路へ落ちていない');
});

test('一括計算に対応しない controller では従来経路のまま（後方互換・ISSUE-232）', async () => {
  const log = [];
  await boot({ log, fetchImpl: makeFetch(log), supportsPlan: false });
  await settle();
  log.length = 0;
  await globalThis.window.__rpAnimateOnce();
  assert.equal(log.some((l) => l.startsWith('compute:latest_seq')), false, '対象が無いのに一括計算を発行している');
  assert.ok(log.includes('その場計算'));
});

test('再生したバーの次バーぶんの計画が用意されている（先読み・ISSUE-232）', async () => {
  const log = [];
  await boot({ log, fetchImpl: makeFetch(log) });
  await settle();
  const bar = globalThis.window.__rpbar;
  await globalThis.window.__rpAnimateOnce();
  await settle();
  // 再生を終えたバーの計画は破棄され、次バーぶんが（先読み済み or 本再生中に発行されて）在る。
  const plans = globalThis.window.__rpPlans();
  assert.ok(plans.includes(bar + 1), `次バー(${bar + 1}) の計画が無い（毎バー待ちが出る・実際: ${plans.join(',')}）`);
  assert.equal(plans.includes(bar), false, '再生し終えたバーの計画が破棄されていない');
});

test('計画がある場合は確定着地の往復を発行しない（settle 省略・ISSUE-232）', async () => {
  const log = [];
  await boot({ log, fetchImpl: makeFetch(log) });
  await settle();
  log.length = 0;
  await globalThis.window.__rpAnimateOnce();
  await settle();
  assert.equal(log.includes('その場計算'), false, 'バー確定で往復（settle）を発行している（1 バーあたり ~100ms の無駄）');
});

test('ライブモード（リプレイ層が停止中）では先読みを発火しない（ISSUE-232・実測 404 の再発防止）', async () => {
  // 統合レイヤは live 表示のまま setupReplay を 1 回 mount する。その時点で先読みが走ると
  //   root 相対 /intraday が live 側へ回り 404 になる（実測 2026-08-01・実 UI コンソール）。
  const log = [];
  const { handle } = await boot({ log, fetchImpl: makeFetch(log) });
  await handle.disable();          // ライブモードへ（統合レイヤの mount 直後と同じ状態）
  log.length = 0;
  await new Promise((r) => setTimeout(r, 80));
  assert.equal(
    log.some((l) => l.startsWith('compute:latest_seq')), false,
    'ライブモードで足内一括計算を発行している（ライブ側へ余計な要求が飛ぶ）',
  );
});
