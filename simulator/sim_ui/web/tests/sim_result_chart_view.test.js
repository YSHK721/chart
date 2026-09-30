// sim_result_chart_view.js — 売買履歴チャートの描画（表示する範囲だけを持つ・読み足し・描かない条件・計算量）。
//
// 観測の境界（宣言する注入点。内部名の差し替えはしない）:
//   `loadExtent` / `fetchRows` … ジョブの足の成果物の宣言と、位置の区間の列（Test Spy で数える）
//   `chartKit`                 … live core の公開面（組み立て関数・ChartRenderer・最初に読む本数 RECENT_BARS）
//   `loadReport`               … report.json の取得（1 ジョブにつき 1 回）
//   `tradeCloseCurves`         … 取引終了時の残高・DD の単一ソース
//   `setTimer` / `clearTimer`  … 読み足しの「変化が止まってから」の時計
// 期待値は宣言から導く（1 回の本数＝chartKit.RECENT_BARS、上限＝extent.max_returned_rows と
//   1 回の本数から `heldRowsCap`）。ここの Fake は本番と違う値（50 本・200 行）を名乗る——View が
//   本番の数を書き写していれば落ちる。
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { createSimResultChartView } from '../js/adapter/front/sim_result_chart_view.js';
import { firstSegment } from '../js/adapter/front/report_source_client.js';
import { resultChartInstances, windowTradeClose, balanceCurveTimes } from '../js/usecase/result_chart_model.js';
import { heldRowsCap, readRowsOf, tailWindow } from '../js/usecase/chart_window.js';
import { tradeCloseCurves } from '../../../report_ui/web/js/chart.js';
import { createLinkage } from '../../../report_ui/web/js/linkage.js';
import { fakeEl } from './_fakes.js';

const RECENT_BARS = 50;          // Fake の公開面が名乗る「最初に読む本数」
const MAX_RETURNED_ROWS = 200;   // Fake の宣言が名乗る「1 回の上限」
const READ_ROWS = readRowsOf({ recentBars: RECENT_BARS, maxReturnedRows: MAX_RETURNED_ROWS });
const CAP = heldRowsCap({ readRows: READ_ROWS, maxReturnedRows: MAX_RETURNED_ROWS });
const TOTAL = 3000;

/** Bar 列の中の位置 i の足の時刻。 */
const timeAt = (i) => 60 * (i + 1);

/** 足の成果物の宣言（extent の応答）。 */
function extent({ rows = TOTAL, datasetRef = 'jp225_mt5_spread' } = {}) {
  return {
    ok: true, rows, index_column: 'bar_index',
    columns: [
      'bar_index', 'time', 'open', 'high', 'low', 'close',
      'balance', 'equity', 'drawdown', 'drawdown_pct', 'realized_pnl', 'floating_pnl', 'margin', 'margin_level',
      'indicator_0',
    ],
    indicators: [{ series: 'sma', placement: 'price', column: 'indicator_0' }],
    timeframe: '1m', ea_name: 'EA', dataset_ref: datasetRef, time_unit: 'epoch_seconds',
    max_returned_rows: MAX_RETURNED_ROWS,
  };
}

/** 位置の区間の列（rows の応答）。run 全体は作らず、問われた区間だけを作る。 */
function rowsAnswer(start, end) {
  const index = Array.from({ length: end - start }, (_, k) => start + k);
  const constant = (v) => index.map(() => v);
  return {
    ok: true, start, end, rows: index.length,
    columns: {
      bar_index: index,
      time: index.map(timeAt),
      open: constant(1), high: constant(2), low: constant(0.5), close: index.map((i) => i + 0.5),
      balance: constant(1000), equity: constant(1000), drawdown: constant(0), drawdown_pct: constant(0),
      realized_pnl: constant(0), floating_pnl: constant(0), margin: constant(0), margin_level: constant(null),
      indicator_0: constant(1),
    },
  };
}

/** report.json（足を持たない・取引終了時の残高・DD の材料と銘柄名）。 */
function report(symbol = 'JP225', curve = []) {
  return {
    meta: { initial_deposit: 1000, symbol },
    segments: { single: { meta: {}, bars: [], agg: { balance_curve: curve } } },
  };
}

function kit() {
  const calls = {
    viewer: [], operations: [], geometry: 0, renderLine: [], legendRows: [], draws: [],
    loaded: [], removed: 0, disposed: { operations: 0, geometry: 0 }, markers: null,
    scrolls: [], toasts: [], visibleSets: [], rangeHandlers: new Set(), chartOptions: [],
  };
  // 見えている論理範囲（検定が動かす）。
  calls.range = { from: 0, to: RECENT_BARS };
  const chartKit = {
    RECENT_BARS,
    composeChartViewer(args) {
      calls.viewer.push(args);
      return {
        symbolSpec: calls.kit.symbolSpec ?? null,
        chart: {
          remove() { calls.removed += 1; },
          applyOptions(o) { calls.chartOptions.push(o); },
        },
        mainSeries: {},
        currentPriceView: { render(v) { calls.currentPrice = v; } },
        paneLegendView: { setInstances(rows) { calls.legendRows.push(rows); } },
        renderer: {
          setCandles(c) { calls.draws.push({ via: 'setCandles', candles: c, instances: null }); calls.last = c; },
          renderLine(id, payloads, opts) { calls.renderLine.push({ id, payloads, opts }); },
          replaceDataKeepingView(c, instances) {
            calls.draws.push({ via: 'replaceDataKeepingView', candles: c, instances });
            calls.last = c;
            return true;
          },
          setVisible(id, on) { calls.visibleSets.push([id, on]); },
          lastClose() { return calls.last.at(-1).close; },
          visibleLogicalRange() { return calls.range; },
          subscribeVisibleLogicalRange(fn) {
            calls.rangeHandlers.add(fn);
            return () => calls.rangeHandlers.delete(fn);
          },
          isLatestBarVisible() { return calls.latestVisible ?? true; },
          scrollToRealTime(opts) { calls.scrolls.push({ opts, candles: calls.last }); },
          barInfoAt() { return 'bar-info'; },
        },
      };
    },
    installPaneGeometry() {
      calls.geometry += 1;
      return { updatePaneHeight() {}, dispose() { calls.disposed.geometry += 1; } };
    },
    installChartOperations(args) {
      calls.operations.push(args);
      return { dispose() { calls.disposed.operations += 1; } };
    },
    ChartToastView: class { show(text) { calls.toasts.push(text); } },
    TradeMarkersRenderer: class {
      constructor(args) {
        calls.markers = args; this.highlighted = []; this.notify = null;
        (calls.markerInstances = calls.markerInstances || []).push(this);
      }
      setCurrentTimeframe(tf) { calls.timeframe = tf; }
      async load(url) { calls.loaded.push(url); return 0; }
      highlightTrade(id) { this.highlighted.push(id); }
      onHighlightChange(fn) { this.notify = fn; }
    },
  };
  const k = { calls, chartKit };
  calls.kit = k;
  return k;
}

/**
 * View と Test Spy 一式。
 * @param {object} [args.ext]  extent の応答（既定 3000 本の run）
 * @param {function} [args.rep] (jobId) => report.json（呼ばれるたびに新しい実体）
 */
function view({ k = kit(), ext = extent(), rep = () => report(), answer = rowsAnswer } = {}) {
  const host = fakeEl();
  const doc = { createElement: (t) => fakeEl(t) };
  const c = k.calls;
  c.extents = []; c.reads = []; c.reports = []; c.tradeCloseSegments = []; c.timers = [];
  const v = createSimResultChartView({
    doc, host, lwc: {}, chartKit: k.chartKit,
    loadExtent: async (jobId) => {
      if (k.failExtent) throw k.failExtent;
      c.extents.push(jobId);
      return ext;
    },
    // Test Spy: 足の成果物の読み（区間と、返した足の時刻）を覚える。
    fetchRows: async (jobId, start, end) => {
      if (k.failRows) throw k.failRows;
      const a = await answer(start, end);
      c.reads.push({ jobId, start, end, times: a.columns.time, drawsBefore: c.draws.length });
      return a;
    },
    // Test Spy: report.json の取得を数える（取得した実体を覚える）。
    loadReport: async (jobId) => {
      if (k.failReport) throw k.failReport;
      const payload = rep(jobId);
      c.reports.push(payload);
      return payload;
    },
    // Test Spy: 取引終了時の残高・DD をどの区間から作ったかを覚える（値は本物の関数で作る）。
    tradeCloseCurves: (segment, times, deposit) => {
      c.tradeCloseSegments.push(segment);
      return tradeCloseCurves(segment, times, deposit);
    },
    setTimer: (fn, ms) => { const t = { fn, ms }; c.timers.push(t); return t; },
    clearTimer: (t) => { c.timers = c.timers.filter((x) => x !== t); },
  });
  const container = () => host.children.find((el) => el.className === 'sim-result-chart-canvas');
  /** 利用者の操作（既定は押す）。 */
  const userActs = (type = 'pointerdown') => { for (const fn of container()._listeners[type]) fn(); };
  /** 表示範囲の変化の通知（lwc が出すもの）。 */
  const rangeChanges = (range) => {
    if (range) c.range = range;
    for (const fn of [...c.rangeHandlers]) fn(c.range);
  };
  /** 時計を進める（待っている読みを実行し、終わるまで待つ）。 */
  const settle = async () => {
    const due = c.timers; c.timers = [];
    for (const t of due) t.fn();
    for (let i = 0; i < 20; i += 1) await new Promise((r) => setImmediate(r));
  };
  /** 利用者が操作して表示範囲が変わり、止まった。 */
  const scrollTo = async (range) => { userActs(); rangeChanges(range); await settle(); };
  return { v, host, k, c, userActs, rangeChanges, settle, scrollTo };
}

/** 描いた足の Bar 列の中の位置の区間（足の時刻から逆算）。 */
function drawnWindow(candles) {
  return { start: candles[0].time / 60 - 1, end: candles.at(-1).time / 60 };
}

/** 使った取得＝その取得の区間から取引終了時の残高・DD を作ったもの。 */
function usedReports(c) {
  return c.reports.filter((p) => c.tradeCloseSegments.includes(firstSegment(p)));
}

// ---- 初期表示 ----

test('初期表示: run の末尾からライブチャートと同じ本数を読み、ライブチャートと同じ口（setCandles）で描く', async () => {
  const { v, c } = view();
  assert.equal(await v.render('job1'), true);
  const tail = tailWindow({ totalRows: TOTAL, readRows: READ_ROWS });
  assert.deepEqual(c.reads.map((r) => [r.jobId, r.start, r.end]), [['job1', tail.start, tail.end]]);
  assert.equal(c.draws.length, 1);
  assert.equal(c.draws[0].via, 'setCandles');
  assert.deepEqual(drawnWindow(c.draws[0].candles), tail);
  // 最小足幅は lwc の既定のまま（全期間を収める指定はしない）。
  assert.deepEqual(c.chartOptions, []);
});

test('run がライブチャートの本数より短ければ全部を読む', async () => {
  const { v, c } = view({ ext: extent({ rows: 7 }) });
  assert.equal(await v.render('job1'), true);
  assert.deepEqual(c.reads.map((r) => [r.start, r.end]), [[0, 7]]);
});

test('ライブチャートと同じ組み立て関数で組み、操作性を付け、重ねる表示の置き場は器にする', async () => {
  const { v, host, c } = view();
  assert.equal(await v.render('job1'), true);
  assert.equal(c.viewer.length, 1);
  assert.equal(c.viewer[0].anchor, host);
  assert.equal(c.viewer[0].datasetRef, 'jp225_mt5_spread');
  assert.equal(c.geometry, 1);
  assert.equal(c.operations.length, 1);
  assert.equal(c.operations[0].anchor, host);
  // 右クリック「情報をコピーする」の文脈は売買履歴チャートのジョブのもの。
  assert.equal(c.operations[0].getMenuContext().timeframe, '1m');
  // 操作性へ渡す描画の口は ChartRenderer のメソッドをそのまま通す。
  assert.equal(c.operations[0].renderer.barInfoAt(), 'bar-info');
});

test('描くとき: 全 instance（点の時刻は区間の足の時刻）・売買マーク（ChartRenderer を渡す）', async () => {
  const { v, c } = view();
  await v.render('job1');
  const tail = tailWindow({ totalRows: TOTAL, readRows: READ_ROWS });
  const columns = rowsAnswer(tail.start, tail.end).columns;
  const expected = resultChartInstances(extent(), columns, tradeCloseCurves(firstSegment(report()), columns.time, 1000));
  assert.deepEqual(c.renderLine.map((x) => x.id), expected.map((inst) => inst.instanceId));
  for (const call of c.renderLine) {
    for (const p of call.payloads) assert.deepEqual(p.data.map((q) => q.time), columns.time, `${call.id} ${p.name}`);
  }
  assert.deepEqual(c.loaded, ['/sim/data/job1/trade_markers.json']);
  assert.equal(c.timeframe, '1m');
  assert.ok(c.markers.chartRenderer, '売買マークに ChartRenderer を渡していない（ペア外の減光が効かない）');
});

test('取引終了時の残高・DD は、全期間で作った値の同じ区間と一致する（区間より前の最高値を引き継ぐ）', async () => {
  // run の前半で最高値を付け、後半で下げる。末尾の区間だけで計算すると DD が浅く出る形。
  const curve = [
    { time: timeAt(100), value: 5000 }, { time: timeAt(2000), value: 3000 }, { time: timeAt(TOTAL - 10), value: 2500 },
  ];
  const { v, c } = view({ rep: () => report('JP225', curve) });
  await v.render('job1');
  const allTimes = Array.from({ length: TOTAL }, (_, i) => timeAt(i));
  const whole = tradeCloseCurves(firstSegment(report('JP225', curve)), allTimes, 1000);
  const tail = tailWindow({ totalRows: TOTAL, readRows: READ_ROWS });
  const payloads = (title) => c.renderLine.find((x) => x.id === `pane:${title}`).payloads;
  assert.deepEqual(
    payloads('残高・有効証拠金').find((p) => p.name === '残高（取引終了時）').data,
    whole.balData.slice(tail.start, tail.end),
  );
  const dd = payloads('DD').find((p) => p.name === 'DD（取引終了時）').data;
  assert.deepEqual(dd, whole.ddData.slice(tail.start, tail.end));
  assert.equal(dd[0].value, 3000 - 5000);
});

test('右クリックのコピーの銘柄は、台帳で引けない系列でも report.json の meta.symbol（同じ 1 回の取得から）', async () => {
  const { v, c } = view({ ext: extent({ datasetRef: null }) });
  assert.equal(await v.render('job1'), true);
  assert.equal(c.operations[0].getMenuContext().symbol, 'JP225');
  assert.equal(c.reports.length - usedReports(c).length, 0);
});

test('右クリックのコピーの銘柄は、台帳の銘柄仕様が引ければそれを優先する', async () => {
  const k = kit();
  k.symbolSpec = { symbol: 'LEDGER', digits: 1 };
  const { v, c } = view({ k });
  assert.equal(await v.render('job1'), true);
  assert.equal(c.operations[0].getMenuContext().symbol, 'LEDGER');
});

test('ペイン別凡例の行は描いた instance と一致し、設定・削除の処理を持たない', async () => {
  const { v, c } = view();
  await v.render('job1');
  const rows = c.legendRows.at(-1);
  assert.deepEqual(rows.map((r) => r.instanceId), c.renderLine.map((x) => x.id));
  assert.ok(rows.every((r) => typeof r.onEye === 'function' && r.onGear === undefined && r.onClose === undefined));
});

// ---- 描かない条件（理由を掲示する） ----

test('足の成果物が無い（404）ジョブは、再実行を促す掲示を出して描かない', async () => {
  const k = kit();
  k.failExtent = Object.assign(new Error('not found'), { status: 404 });
  const { v, host, c } = view({ k });
  assert.equal(await v.render('job1'), false);
  assert.equal(c.viewer.length, 0);
  assert.equal(
    host.children[0].textContent,
    'このジョブは変更前に実行されたため売買履歴チャートを表示できません。再実行すると表示できます。',
  );
  assert.equal(v.shownJob(), null);
});

test('宣言が 409（未完了）なら完了待ちの理由を出し、次の render（子文書の読み直し）で組み立て直せる', async () => {
  const k = kit();
  k.failExtent = Object.assign(new Error('409'), { status: 409 });
  const { v, host, c } = view({ k });
  assert.equal(await v.render('job1'), false);
  assert.match(host.children[0].textContent, /完了していない/);
  assert.equal(c.viewer.length, 0, '未完了の間はチャートを組まない');
  k.failExtent = null;
  assert.equal(await v.render('job1'), true, '同じジョブでも読めなかった分は再試行する');
  assert.equal(c.viewer.length, 1);
});

test('宣言のそれ以外の読み込み失敗も理由を出し、覚えない（再試行できる）', async () => {
  const k = kit();
  k.failExtent = new Error('接続できません');
  const { v, host } = view({ k });
  assert.equal(await v.render('job1'), false);
  assert.match(host.children[0].textContent, /接続できません/);
  assert.equal(v.shownJob(), null);
});

test('report.json を読めなければ描かずに理由を出し、覚えない（再試行できる）', async () => {
  const k = kit();
  k.failReport = new Error('結果未生成');
  const { v, host, c } = view({ k });
  assert.equal(await v.render('job1'), false);
  assert.equal(c.viewer.length, 0);
  assert.match(host.children[0].textContent, /report\.json/);
  assert.match(host.children[0].textContent, /結果未生成/);
  assert.equal(v.shownJob(), null);
});

test('report.json が 409（未完了）なら完了待ちの理由を出す', async () => {
  const k = kit();
  k.failReport = Object.assign(new Error('結果未生成'), { status: 409 });
  const { v, host } = view({ k });
  assert.equal(await v.render('job1'), false);
  assert.match(host.children[0].textContent, /完了していない/);
  assert.equal(v.shownJob(), null);
});

test('足が 0 本の run は描かずに理由を出す（読みも発行しない）', async () => {
  const { v, host, c } = view({ ext: extent({ rows: 0 }) });
  assert.equal(await v.render('job1'), false);
  assert.equal(c.viewer.length, 0);
  assert.equal(c.reads.length, 0);
  assert.match(host.children[0].textContent, /足が無い/);
});

test('返った列の長さが行数と違えば描かずに理由を出す（黙ってずらさない）', async () => {
  const short = (start, end) => {
    const a = rowsAnswer(start, end);
    a.columns.equity = a.columns.equity.slice(1);
    return a;
  };
  const { v, host, c } = view({ answer: short });
  assert.equal(await v.render('job1'), false);
  assert.equal(c.viewer.length, 0);
  assert.match(host.children[0].textContent, /一致しない/);
  assert.match(host.children[0].textContent, new RegExp(`列 equity の長さ ${READ_ROWS - 1} が行数 ${READ_ROWS}`));
});

test('最初の区間を読めなければ描かずに理由を出し、覚えない', async () => {
  const k = kit();
  k.failRows = new Error('接続できません');
  const { v, host, c } = view({ k });
  assert.equal(await v.render('job1'), false);
  assert.equal(c.viewer.length, 0);
  assert.match(host.children[0].textContent, /接続できません/);
  assert.equal(v.shownJob(), null);
});

test('公開面が最初に読む本数を名乗らなければ描かずに理由を出す（本数を推測しない）', async () => {
  const k = kit();
  delete k.chartKit.RECENT_BARS;
  const { v, host, c } = view({ k });
  assert.equal(await v.render('job1'), false);
  assert.equal(c.reads.length, 0);
  assert.match(host.children[0].textContent, /本数/);
});

test('足の成果物の宣言が 1 回の上限を名乗らなければ描かずに理由を出す（区間を推測しない）', async () => {
  const ext = extent();
  delete ext.max_returned_rows;
  const { v, host, c } = view({ ext });
  assert.equal(await v.render('job1'), false);
  assert.equal(c.reads.length, 0);
  assert.match(host.children[0].textContent, /上限/);
});

test('読んでいる途中で別のジョブへ替えたら、前のジョブの結果（失敗も）は今のジョブの表示に触れない', async () => {
  const k = kit();
  const h = view({ k });
  // job1 の宣言の読みを保留にし、その間に job2 を描く。
  let failJob1;
  const gate = new Promise((_, reject) => { failJob1 = reject; });
  const v = createSimResultChartView({
    doc: { createElement: (t) => fakeEl(t) }, host: h.host, lwc: {}, chartKit: k.chartKit,
    loadExtent: (jobId) => (jobId === 'job1' ? gate : Promise.resolve(extent())),
    fetchRows: async (jobId, start, end) => rowsAnswer(start, end),
    loadReport: async () => report(),
    tradeCloseCurves,
  });
  const first = v.render('job1');
  assert.equal(await v.render('job2'), true);
  failJob1(Object.assign(new Error('not found'), { status: 404 }));
  assert.equal(await first, false);
  assert.equal(v.shownJob(), 'job2');
  assert.ok(!h.host.children.some((el) => el.className === 'sim-result-chart-message'), '前のジョブの掲示が出た');
  assert.equal(k.calls.viewer.length, 1);
});

// ---- 読み足し ----

test('利用者が操作して左端へ近づき、変化が止まったら、前の区間を 1 回読み、表示を保つ口で差し替える', async () => {
  const { v, c, scrollTo } = view();
  await v.render('job1');
  const tail = tailWindow({ totalRows: TOTAL, readRows: READ_ROWS });
  await scrollTo({ from: 2, to: 20 });
  assert.deepEqual(c.reads.slice(1).map((r) => [r.start, r.end]), [[tail.start - READ_ROWS, tail.start]]);
  const draw = c.draws.at(-1);
  assert.equal(draw.via, 'replaceDataKeepingView');
  assert.deepEqual(drawnWindow(draw.candles), { start: tail.start - READ_ROWS, end: tail.end });
  // 読んだ結果で表示範囲を動かさない（移動の口を呼ばない）。
  assert.deepEqual(c.scrolls, []);
  // 値の系列は描き直さず、同じ instance へ入れる。
  assert.deepEqual(draw.instances.map((inst) => inst.instanceId), c.renderLine.map((x) => x.id));
  for (const inst of draw.instances) {
    for (const p of inst.payloads) assert.equal(p.data.length, draw.candles.length, `${inst.instanceId} ${p.name}`);
  }
});

test('変化が続いている間は読まず、止まってから 1 回だけ読む（待ちは 1 つ）', async () => {
  const { v, c, userActs, rangeChanges, settle } = view();
  await v.render('job1');
  userActs('wheel');
  for (let i = 0; i < 25; i += 1) rangeChanges({ from: 2, to: 20 });
  assert.equal(c.timers.length, 1);
  assert.equal(c.reads.length, 1, '止まる前に読んだ');
  await settle();
  assert.equal(c.reads.length, 2);
});

test('端から離れていれば、操作しても読まない', async () => {
  const { v, c, scrollTo } = view();
  await v.render('job1');
  await scrollTo({ from: 2, to: 20 });            // 前を 1 回読む → 100 本持つ
  await scrollTo({ from: 40, to: 60 });           // 左に 40 本・右に 39 本残る（幅 20）
  assert.equal(c.reads.length, 2);
});

test('目のボタンで隠した instance は、読み足しの後も隠れたまま（凡例の行も同じ状態）', async () => {
  const { v, c, scrollTo } = view();
  await v.render('job1');
  const id = c.legendRows.at(-1)[0].instanceId;
  c.legendRows.at(-1)[0].onEye();
  assert.deepEqual(c.visibleSets, [[id, false]]);
  await scrollTo({ from: 2, to: 20 });
  assert.equal(c.draws.length, 2);
  // 描き直し（renderLine の再発行）も、表示の付け直しもしない。
  assert.equal(c.renderLine.length, c.draws[1].instances.length);
  assert.deepEqual(c.visibleSets, [[id, false]]);
  assert.equal(c.legendRows.at(-1).find((r) => r.instanceId === id).visible, false);
});

test('読み足しの列が行数と合わなければ差し替えず、理由を告知する（持っている足はそのまま）', async () => {
  let broken = false;
  const answer = (start, end) => {
    const a = rowsAnswer(start, end);
    if (broken) a.columns.close = a.columns.close.slice(2);
    return a;
  };
  const { v, c, scrollTo } = view({ answer });
  await v.render('job1');
  broken = true;
  await scrollTo({ from: 2, to: 20 });
  assert.equal(c.draws.length, 1);
  assert.equal(c.toasts.length, 1);
  assert.match(c.toasts[0], /列 close の長さ/);
  // 直れば次の操作で読める。
  broken = false;
  await scrollTo({ from: 2, to: 20 });
  assert.equal(c.draws.length, 2);
});

test('読み足しが失敗（取得できない）しても落ちず、理由を告知する', async () => {
  const k = kit();
  const { v, c, scrollTo } = view({ k });
  await v.render('job1');
  k.failRows = new Error('接続できません');
  await scrollTo({ from: 2, to: 20 });
  assert.equal(c.draws.length, 1);
  assert.match(c.toasts[0], /接続できません/);
});

// ---- 「最新足」ボタン ----

test('run の末尾を持っている間は、最新足が見えているかの判定も移動もライブチャートと同じ（読まない）', async () => {
  const { v, c, settle } = view();
  await v.render('job1');
  const operated = c.operations[0].renderer;
  c.latestVisible = false;
  assert.equal(operated.isLatestBarVisible(), false);
  c.latestVisible = true;
  assert.equal(operated.isLatestBarVisible(), true);
  operated.scrollToRealTime({ speed: 2 });
  await settle();
  assert.equal(c.reads.length, 1);
  assert.deepEqual(c.scrolls.map((s) => s.opts), [{ speed: 2 }]);
});

test('run の末尾を捨てた後は「最新足が見えていない」と答え、ボタンで run の末尾を読んでから移動する', async () => {
  const { v, c, scrollTo, settle } = view();
  await v.render('job1');
  // 左へ進み続け、上限を超えて run の末尾を捨てさせる。
  for (let i = 0; i < CAP / READ_ROWS + 1; i += 1) await scrollTo({ from: 2, to: 20 });
  assert.ok(drawnWindow(c.draws.at(-1).candles).end < TOTAL, 'run の末尾をまだ持っている（検定の前提が崩れた）');
  const operated = c.operations[0].renderer;
  c.latestVisible = true;   // 持っている末尾が見えていても、run の末尾ではない
  assert.equal(operated.isLatestBarVisible(), false);

  const readsBefore = c.reads.length;
  operated.scrollToRealTime({ speed: 2 });
  await settle();
  const tail = tailWindow({ totalRows: TOTAL, readRows: READ_ROWS });
  assert.deepEqual(c.reads.slice(readsBefore).map((r) => [r.start, r.end]), [[tail.start, tail.end]]);
  assert.deepEqual(drawnWindow(c.draws.at(-1).candles), tail);
  // 移動は run の末尾を描いた後。
  assert.equal(c.scrolls.length, 1);
  assert.equal(c.scrolls[0].candles, c.draws.at(-1).candles);
  assert.equal(operated.isLatestBarVisible(), true);
});

// ---- 片付け ----

test('同じジョブの再 render は組み直さない・読み直さない', async () => {
  const { v, c } = view();
  await v.render('job1');
  for (let i = 0; i < 5; i += 1) await v.render('job1');
  assert.equal(c.viewer.length, 1);
  assert.equal(c.loaded.length, 1);
  assert.equal(c.extents.length, 1);
  assert.equal(c.reads.length, 1);
});

test('別のジョブへ替えるときは前のチャート・購読・待ちを片付けてから描く', async () => {
  const { v, c, userActs, rangeChanges } = view();
  await v.render('job1');
  userActs();
  rangeChanges({ from: 2, to: 20 });
  assert.equal(c.timers.length, 1);
  await v.render('job2');
  assert.equal(c.removed, 1);
  assert.deepEqual(c.disposed, { operations: 1, geometry: 1 });
  assert.equal(v.shownJob(), 'job2');
  assert.equal(c.timers.length, 0, '前のジョブの待ちが残っている');
  assert.equal(c.rangeHandlers.size, 1, '前のジョブの購読が残っている');
  assert.deepEqual(c.reads.map((r) => r.jobId), ['job1', 'job2']);
});

test('読みの途中で片付けたら、届いた結果を描かない', async () => {
  // 2 回目の読み（読み足し）を保留にし、発行されたのを確かめてから片付け、その後で結果を届ける。
  let asked = 0;
  let deliver;
  const answer = (start, end) => {
    asked += 1;
    if (asked === 1) return rowsAnswer(start, end);
    return new Promise((resolve) => { deliver = () => resolve(rowsAnswer(start, end)); });
  };
  const { v, c, userActs, rangeChanges, settle } = view({ answer });
  await v.render('job1');
  userActs();
  rangeChanges({ from: 2, to: 20 });
  await settle();
  assert.equal(asked, 2, '読み足しが発行されていない（検定の前提が崩れた）');
  v.clear();
  deliver();
  await settle();
  assert.equal(c.draws.length, 1);
  assert.deepEqual(c.toasts, []);
});

// ---- ISSUE-538: 取引明細・priceChart との hover の連動 ----

function countingLinkage() {
  const linkage = createLinkage();
  const subscribe = linkage.subscribe.bind(linkage);
  linkage.subscriptions = 0;
  linkage.subscribe = (fn) => { linkage.subscriptions += 1; subscribe(fn); };
  return linkage;
}

test('取引明細の hover（linkage）で売買履歴チャートの同じ取引番号を強調し、グリフ hover を linkage へ返す', async () => {
  const { v, c } = view();
  await v.render('job1');
  const linkage = countingLinkage();
  v.bindLinkage(linkage);
  const markers = c.markerInstances[0];

  linkage.setHover(7, 'table');
  assert.equal(markers.highlighted.at(-1), 7);

  const seen = [];
  linkage.subscribe((id, source) => seen.push([id, source]));
  markers.notify(3);
  markers.notify(null);
  assert.deepEqual(seen, [[3, 'chart'], [null, 'chart']]);
});

test('linkage が先に届いても、描いた後の売買マークを今の hover の取引で強調する', async () => {
  const { v, c } = view();
  const linkage = countingLinkage();
  linkage.setHover(4, 'table');
  v.bindLinkage(linkage);
  await v.render('job1');
  assert.equal(c.markerInstances[0].highlighted.at(-1), 4);
});

test('取引の hover では足を読まず、表示も動かさない（持っている区間の外の取引でも）', async () => {
  const { v, c, settle } = view();
  await v.render('job1');
  const linkage = countingLinkage();
  v.bindLinkage(linkage);
  for (const id of [1, 999_999, null]) linkage.setHover(id, 'table');
  await settle();
  assert.equal(c.reads.length, 1);
  assert.equal(c.draws.length, 1);
  assert.deepEqual(c.scrolls, []);
  assert.equal(c.timers.length, 0);
});

test('計算量: 同じ linkage を何度渡しても購読は 1 つ・ジョブを替えても増えない', async () => {
  const { v, c } = view();
  const linkage = countingLinkage();
  await v.render('job1');
  for (let i = 0; i < 5; i += 1) v.bindLinkage(linkage);
  await v.render('job2');
  v.bindLinkage(linkage);
  assert.equal(linkage.subscriptions, 1);
  // 替えた後の hover は新しい売買マークへ届く（古い売買マークへは届かない）。
  const [oldMarkers, newMarkers] = c.markerInstances;
  const oldCount = oldMarkers.highlighted.length;
  linkage.setHover(9, 'table');
  assert.equal(newMarkers.highlighted.at(-1), 9);
  assert.equal(oldMarkers.highlighted.length, oldCount);
});

// ---- 計算量（絶対命令）。測るのは回数。観測は注入した fetchRows・chartKit・loadReport だけ ----

/** 同じ操作の台本: 左へ `steps` 回、右へ `steps` 回。各回、端の近くを見て止まる。 */
async function script(h, steps) {
  for (let i = 0; i < steps; i += 1) await h.scrollTo({ from: 2, to: 20 });
  for (let i = 0; i < steps; i += 1) {
    const heldRows = h.c.draws.at(-1).candles.length;
    await h.scrollTo({ from: heldRows - 20, to: heldRows - 2 });
  }
}

test('計算量: 取得した行 − 描画へ渡した点 = 0（足も各系列も。読むたびに）', async () => {
  const h = view();
  await h.v.render('job1');
  await script(h, 3);
  assert.ok(h.c.reads.length > 3, '読み足しが起きていない（検定が空虚）');
  for (const read of h.c.reads) {
    // その読みの直後の描画。
    const draw = h.c.draws[read.drawsBefore];
    assert.ok(draw, `区間 [${read.start}, ${read.end}) を読んだ後に描いていない`);
    const drawn = new Set(draw.candles.map((b) => b.time));
    const wasted = read.times.filter((t) => !drawn.has(t));
    assert.equal(wasted.length, 0, `読んだ ${read.times.length} 行のうち ${wasted.length} 行を足に描いていない`);
    const series = draw.instances
      ? draw.instances.flatMap((inst) => inst.payloads)
      : h.c.renderLine.flatMap((x) => x.payloads);
    for (const p of series) {
      const points = new Set(p.data.map((q) => q.time));
      assert.equal(read.times.filter((t) => !points.has(t)).length, 0, `${p.name} に渡していない行がある`);
      // 描画へ渡した点 − 持っている足 = 0（系列ごと）。
      assert.equal(p.data.length - draw.candles.length, 0);
    }
  }
  // 読みの回数 − 描画の回数 = 0（読んで描かない・読まずに描き直す、のどちらも無い）。
  assert.equal(h.c.reads.length - h.c.draws.length, 0);
});

test('計算量: 利用者の操作によらない読み = 0（最初の 1 回＝render が発行する末尾の区間の読みを除く）', async () => {
  const h = view();
  await h.v.render('job1');
  const initial = h.c.reads.length;
  assert.equal(initial, 1);
  // 操作なしで表示範囲が変わる（幅の確定・差し替えの余波）: 端の近くでも読まない・待ちも作らない。
  for (let i = 0; i < 30; i += 1) h.rangeChanges({ from: 2, to: 20 });
  assert.equal(h.c.timers.length, 0);
  await h.settle();
  assert.equal(h.c.reads.length - initial, 0);

  // 操作 1 回 → 読み 1 回。読み込み後の範囲変化（lwc は差し替えの直後と次の描画で通知する・実測）
  //   では連鎖しない: 端の近くのままでも、次の操作までは読まない。
  await h.scrollTo({ from: 2, to: 20 });
  const afterOne = h.c.reads.length;
  assert.equal(afterOne - initial, 1);
  for (let i = 0; i < 30; i += 1) h.rangeChanges({ from: 2, to: 20 });
  assert.equal(h.c.timers.length, 0);
  await h.settle();
  assert.equal(h.c.reads.length - afterOne, 0);

  // 操作の数 − 読みの数 ≥ 0（操作 1 回が許す読みは、前後それぞれ高々 1 回）。
  let acts = 1;
  for (let i = 0; i < 6; i += 1) { await h.scrollTo({ from: 2, to: 20 }); acts += 1; }
  assert.ok(h.c.reads.length - initial <= acts);
});

test('計算量: 何回スクロールしても、持っている本数 ≤ 上限（上限は宣言から導く）', async () => {
  const h = view();
  await h.v.render('job1');
  await script(h, 3 * (CAP / READ_ROWS));
  assert.ok(h.c.draws.length > CAP / READ_ROWS, '上限に届くほど読んでいない（検定が空虚）');
  const held = h.c.draws.map((d) => d.candles.length);
  assert.ok(Math.max(...held) <= CAP, `持っている本数 ${Math.max(...held)} が上限 ${CAP} を超えた`);
  assert.equal(Math.max(...held), CAP, '上限まで持てていない（捨てすぎ）');
  // 捨てた側へ戻ったら読み直す: 左へ進んで捨てた run の末尾を、右へ戻って再び持つ。
  assert.ok(h.c.draws.some((d) => drawnWindow(d.candles).end < TOTAL));
  assert.equal(drawnWindow(h.c.draws.at(-1).candles).end, TOTAL);
  // 足は Bar 列の連続した区間（つなぎ目で抜け・重複が無い）。
  for (const d of h.c.draws) {
    const w = drawnWindow(d.candles);
    assert.equal(w.end - w.start, d.candles.length);
  }
});

test('計算量: 1 回の読みの本数と読みの回数は run の長さ 2 点（3 千本と 300 万本）で同じ', async () => {
  const observed = [];
  for (const rows of [3_000, 3_000_000]) {
    const h = view({ ext: extent({ rows }) });
    await h.v.render('job1');
    await script(h, 2 * (CAP / READ_ROWS));
    observed.push({
      sizes: h.c.reads.map((r) => r.end - r.start),
      extents: h.c.extents.length,
      heldMax: Math.max(...h.c.draws.map((d) => d.candles.length)),
    });
  }
  assert.deepEqual(observed[0], observed[1]);
  assert.ok(observed[0].sizes.every((n) => n === READ_ROWS));
});

test('計算量: report.json の取得 − 使った取得 = 0（1 ジョブ 1 回・再 render でも読み足しでも取り直さない）', async () => {
  const h = view();
  for (let i = 0; i < 4; i += 1) await h.v.render('job1');
  await script(h, 3);
  assert.ok(h.c.reports.length > 0, '取得が観測口を通っていない（検定が空虚）');
  assert.equal(h.c.reports.length - usedReports(h.c).length, 0);
  assert.equal(h.c.reports.length, new Set(h.c.extents).size);
});

test('計算量: run の長さを変えても report.json の取得は増えない（取得 − 使った取得 = 0 を 2 点で）', async () => {
  const fetched = [];
  for (const rows of [3_000, 3_000_000]) {
    const h = view({ ext: extent({ rows }) });
    assert.equal(await h.v.render('job1'), true);
    await script(h, 3);
    assert.equal(h.c.reports.length - usedReports(h.c).length, 0);
    fetched.push(h.c.reports.length);
  }
  assert.equal(fetched[0], fetched[1]);
});

test('計算量: 描いた instance − 凡例に出した instance = 0、組み立ての回数は run の長さ 2 点で同じ', async () => {
  const counts = [];
  for (const rows of [3_000, 3_000_000]) {
    const h = view({ ext: extent({ rows }) });
    await h.v.render('job1');
    await script(h, 3);
    const drawn = new Set(h.c.renderLine.map((x) => x.id));
    const listed = new Set(h.c.legendRows.at(-1).map((r) => r.instanceId));
    assert.deepEqual([...drawn].filter((id) => !listed.has(id)), []);
    counts.push([h.c.viewer.length, h.c.operations.length, h.c.renderLine.length]);
  }
  assert.deepEqual(counts[0], counts[1]);
});

test('取引終了時の残高・DD は読み足した区間でも全期間の値と一致する', async () => {
  const curve = [
    { time: timeAt(100), value: 5000 }, { time: timeAt(TOTAL - 70), value: 3000 }, { time: timeAt(TOTAL - 10), value: 2500 },
  ];
  const rep = () => report('JP225', curve);
  const h = view({ rep });
  await h.v.render('job1');
  await h.scrollTo({ from: 2, to: 20 });
  const draw = h.c.draws.at(-1);
  const w = drawnWindow(draw.candles);
  const segment = firstSegment(rep());
  const expected = windowTradeClose({
    tradeCloseCurves, segment, deposit: 1000, curveTimes: balanceCurveTimes(segment),
    times: draw.candles.map((b) => b.time),
  });
  const allTimes = Array.from({ length: TOTAL }, (_, i) => timeAt(i));
  const whole = tradeCloseCurves(segment, allTimes, 1000);
  assert.deepEqual(expected.ddData, whole.ddData.slice(w.start, w.end));
  const dd = draw.instances.find((inst) => inst.instanceId === 'pane:DD').payloads
    .find((p) => p.name === 'DD（取引終了時）').data;
  assert.deepEqual(dd, whole.ddData.slice(w.start, w.end));
});
