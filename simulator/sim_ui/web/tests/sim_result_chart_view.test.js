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
import {
  AREA_FILL_ALPHA, DEFAULT_LINE_WIDTH, resultChartInstances, windowTradeClose, balanceCurveTimes,
} from '../js/usecase/result_chart_model.js';
import { heldRowsCap, readRowsOf, tailWindow } from '../js/usecase/chart_window.js';
import { tradeCloseCurves, _withAlpha } from '../../../report_ui/web/js/chart.js';
import { createLinkage } from '../../../report_ui/web/js/linkage.js';
import { fakeEl } from './_fakes.js';

const RECENT_BARS = 50;          // Fake の公開面が名乗る「最初に読む本数」
const MAX_RETURNED_ROWS = 200;   // Fake の宣言が名乗る「1 回の上限」
const STOP_OUT_LEVEL = 87.5;     // Fake の宣言が名乗る、run が使ったストップアウト水準（台帳の値と違う）
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
    stop_out_level: STOP_OUT_LEVEL,
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
      // 有効証拠金は初期資金（report の meta.initial_deposit＝1000）の上下と、ちょうど同じ値を混ぜる。
      balance: index.map((i) => 1000 + ((i % 3) - 1)), equity: index.map((i) => 1000 + ((i % 5) - 2)), drawdown: index.map((i) => i % 4), drawdown_pct: constant(0),
      realized_pnl: constant(0), floating_pnl: constant(0), margin: constant(0), margin_level: index.map((i) => (i % 5 === 0 ? null : 80 + (i % 21))),
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
    viewer: [], operations: [], geometry: 0, renderLine: [], renderBaseline: [], renders: [], legendRows: [], draws: [],
    loaded: [], removed: 0, disposed: { operations: 0, geometry: 0 }, markers: null,
    scrolls: [], toasts: [], visibleSets: [], rangeHandlers: new Set(), chartOptions: [],
    // ChartRenderer が持つクロム色（ローソク足の陽線・陰線）。本番（CHROME_CURRENT）と違う色を名乗る——
    //   View が色を書き写していれば落ちる。
    chrome: { candleUp: '#11aa11', candleDown: '#aa1111' }, chromeSubscribed: 0, chromeUnsubscribed: 0,
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
          renderLine(id, payloads, opts) {
            calls.renderLine.push({ id, payloads, opts });
            calls.renders.push({ method: 'renderLine', id, payloads, opts });
          },
          renderBaseline(id, payloads, opts) {
            calls.renderBaseline.push({ id, payloads, opts });
            calls.renders.push({ method: 'renderBaseline', id, payloads, opts });
          },
          addChromeObserver(fn) {
            calls.chromeSubscribed += 1;
            fn(calls.chrome);
            return () => { calls.chromeUnsubscribed += 1; };
          },
          replaceDataKeepingView(c, instances) {
            // 本物（ChartRenderer.replaceDataKeepingView）と同じく、同じ時刻の足を同じ画面位置に
            //   留める＝先頭に増えた（減った）足の本数だけ、見えている論理範囲がずれる。
            const shift = (calls.last[0].time - c[0].time) / 60;
            calls.range = { from: calls.range.from + shift, to: calls.range.to + shift };
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
    withAlpha: _withAlpha,
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

/** View が model へ渡すはずの初期資金基準と色（report の初期資金・ChartRenderer の色）。 */
function baseline(c, deposit = 1000) {
  return { deposit, upColor: c.chrome.candleUp, downColor: c.chrome.candleDown, withAlpha: _withAlpha };
}

/** 描いた instance（renderLine・renderBaseline のどちらで描いたものも・描いた順・重複なし）。 */
function drawnIds(c) {
  return [...new Set(c.renders.map((x) => x.id))];
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
  const expected = resultChartInstances(
    extent(), columns, tradeCloseCurves(firstSegment(report()), columns.time, 1000), baseline(c),
  );
  assert.deepEqual(drawnIds(c), expected.map((inst) => inst.instanceId));
  for (const call of c.renders) {
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
  assert.deepEqual(rows.map((r) => r.instanceId), drawnIds(c));
  assert.ok(rows.every((r) => typeof r.onEye === 'function' && r.onGear === undefined && r.onClose === undefined));
});

// ---- 面（基準つき・グラデーション）: 残高・DD・損益（初期資金比） ----

/** 面の系列と、区間の列から期待する値・基準。 */
const AREAS = Object.freeze([
  { id: 'pane:残高・有効証拠金', name: '有効証拠金（足ごと）', lines: ['残高（足ごと）', '残高（取引終了時）'],
    base: (deposit) => deposit, values: (cols) => cols.equity },
  { id: 'pane:DD', name: 'DD（足ごと）', lines: ['DD（取引終了時）'],
    base: () => 0, values: (cols) => cols.drawdown.map((v) => -v) },
  { id: 'pane:損益（初期資金比）', name: '損益（初期資金比）', lines: ['確定損益（累計）'],
    base: () => 0, values: (cols, deposit) => cols.equity.map((v) => v - deposit) },
  { id: 'pane:証拠金維持率(%)', name: '証拠金維持率', lines: [],
    base: () => STOP_OUT_LEVEL, values: (cols) => cols.margin_level },
]);

/** 区間の列から期待する面の点（値なしは whitespace）。 */
function expectedArea(cols, values) {
  return cols.time.map((time, k) => (values[k] === null ? { time } : { time, value: values[k] }));
}

/** 面の 6 色（ChartRenderer が持つローソク足の陽線・陰線の色から作る）。 */
function expectedFill(c) {
  const up = c.chrome.candleUp;
  const down = c.chrome.candleDown;
  return {
    topLineColor: up,
    topFillColor1: _withAlpha(up, AREA_FILL_ALPHA.edge),
    topFillColor2: _withAlpha(up, AREA_FILL_ALPHA.base),
    bottomLineColor: down,
    bottomFillColor1: _withAlpha(down, AREA_FILL_ALPHA.base),
    bottomFillColor2: _withAlpha(down, AREA_FILL_ALPHA.edge),
  };
}

test('面: 3 つとも renderBaseline で、上に重ねる線より先に（下に）同じ instance・同じペインへ描く', async () => {
  const { v, c } = view();
  await v.render('job1');
  for (const a of AREAS) {
    const mine = c.renders.filter((x) => x.id === a.id);
    assert.deepEqual(mine.map((x) => [x.method, x.payloads.map((p) => p.name), x.opts]), [
      ['renderBaseline', [a.name], { pane: true }],
      ...(a.lines.length > 0 ? [['renderLine', a.lines, { pane: true }]] : []),
    ], a.name);
    // 凡例の行は 1 つ（面と線で 1 instance）。
    assert.equal(c.legendRows.at(-1).filter((r) => r.instanceId === a.id).length, 1);
  }
  assert.deepEqual(c.renderBaseline.map((x) => x.id).sort(), AREAS.map((a) => a.id).sort());
  // 含み損益の線は描かない。
  assert.ok(c.renders.every((x) => x.payloads.every((p) => p.name !== '含み損益')));
});

test('面: 値・基準は区間の列と report.json の初期資金・宣言の水準から、面の色は ChartRenderer のローソク足の色から、縁の線は既定の太さ', async () => {
  const deposit = 999;
  const { v, c } = view({ rep: () => ({ ...report(), meta: { initial_deposit: deposit, symbol: 'JP225' } }) });
  await v.render('job1');
  const tail = tailWindow({ totalRows: TOTAL, readRows: READ_ROWS });
  const cols = rowsAnswer(tail.start, tail.end).columns;
  for (const a of AREAS) {
    const p = c.renderBaseline.find((x) => x.id === a.id).payloads[0];
    assert.equal(p.base, a.base(deposit), a.name);
    assert.deepEqual(p.baseline, expectedFill(c), a.name);
    assert.equal(p.width, DEFAULT_LINE_WIDTH, a.name);
    assert.deepEqual(p.data, expectedArea(cols, a.values(cols, deposit)), a.name);
  }
  // 色は購読 1 回で受け取り、購読を残さない。
  assert.equal(c.chromeSubscribed - c.chromeUnsubscribed, 0);
});

test('面: 読み足しの後も、持っている区間の値と同じ基準・色で表示を保つ口から差し替わる（系列を作り直さない）', async () => {
  const { v, c, scrollTo } = view();
  await v.render('job1');
  await scrollTo({ from: 2, to: 20 });
  const draw = c.draws.at(-1);
  assert.equal(draw.via, 'replaceDataKeepingView');
  const w = drawnWindow(draw.candles);
  const cols = rowsAnswer(w.start, w.end).columns;
  for (const a of AREAS) {
    const p = draw.instances.find((i) => i.instanceId === a.id).payloads[0];
    assert.equal(p.kind, 'baseline');
    assert.equal(p.base, a.base(1000));
    assert.deepEqual(p.baseline, expectedFill(c));
    assert.deepEqual(p.data, expectedArea(cols, a.values(cols, 1000)), a.name);
  }
  // 面は作り直さない（renderBaseline は instance ごとに 1 回のまま）。
  assert.equal(c.renderBaseline.length - new Set(c.renderBaseline.map((x) => x.id)).size, 0);
});

test('計算量: 色の購読は 1 チャートにつき 1 回で、読み足しの回数（2 点）で増えない', async () => {
  const observed = [];
  for (const steps of [2, 6]) {
    const h = view();
    await h.v.render('job1');
    await script(h, steps);
    assert.ok(h.c.reads.length > steps, '読み足しが起きていない（検定が空虚）');
    assert.equal(h.c.chromeSubscribed - h.c.viewer.length, 0);
    assert.equal(h.c.chromeSubscribed - h.c.chromeUnsubscribed, 0);
    observed.push(h.c.chromeSubscribed);
  }
  assert.equal(observed[0], observed[1]);
});

test('report.json が初期資金（meta.initial_deposit）を名乗らなければ描かずに理由を出す（基準を推測しない・足も読まない）', async () => {
  for (const meta of [{ symbol: 'JP225' }, { initial_deposit: null }, { initial_deposit: Number.NaN }]) {
    const { v, c, host } = view({ rep: () => ({ ...report(), meta }) });
    assert.equal(await v.render('job1'), false);
    assert.match(host.children.at(-1).textContent, /初期資金/);
    assert.equal(c.reads.length, 0);
    assert.equal(c.draws.length, 0);
  }
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
    withAlpha: _withAlpha,
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
  assert.deepEqual(draw.instances.map((inst) => inst.instanceId), drawnIds(c));
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
  const rendersBefore = c.renders.length;
  await scrollTo({ from: 2, to: 20 });
  assert.equal(c.draws.length, 2);
  // 描き直し（renderLine・renderBaseline の再発行）も、表示の付け直しもしない。
  assert.equal(c.renders.length - rendersBefore, 0);
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
      : h.c.renders.flatMap((x) => x.payloads);
    assert.ok(series.some((p) => p.kind === 'baseline'), '面が検定に入っていない（検定が空虚）');
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
    const drawn = new Set(drawnIds(h.c));
    const listed = new Set(h.c.legendRows.at(-1).map((r) => r.instanceId));
    assert.deepEqual([...drawn].filter((id) => !listed.has(id)), []);
    counts.push([h.c.viewer.length, h.c.operations.length, h.c.renders.length]);
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

// ---- 計算量: 広い表示範囲でも「読んでから捨てる」が無い（独立レビュー 🟡-1） ----
//   上の「取得した行 − 描画へ渡した点 = 0」は読みの直後の描画しか見ない。前を読んで後を捨て、
//   続けて後を読んで前を捨てると、読みの直後にはどちらも描かれているので落ちない。
//   ここは操作 1 回の終わりに持っている区間で数える。観測は注入した fetchRows と chartKit だけ。

/** 半開区間 a と b の重なりの行数。 */
const overlapRows = (a, b) => Math.max(0, Math.min(a.end, b.end) - Math.max(a.start, b.start));

/** 狭い表示範囲で左へ進み、上限まで持たせる。 */
async function fillToCap(h) {
  for (let i = 0; i < CAP / READ_ROWS; i += 1) await h.scrollTo({ from: 2, to: 20 });
  assert.equal(h.c.draws.at(-1).candles.length, CAP, '上限まで持てていない（検定の前提が崩れた）');
}

/**
 * 操作 1 回（表示範囲を `rangeOf(持っている本数, 今の表示範囲)` にして止まる）を行い、数える。
 * @returns {{read: number, kept: number, reread: number, seenLost: number, sizes: number[]}}
 *   read＝この操作で読んだ行・kept＝そのうち操作の後も持っている行・
 *   reread＝読んだ行のうち、その読みの前から持っていた行（読み直し）・
 *   seenLost＝操作の時点で見えていた足のうち、操作の後に持っていない行
 */
async function operate(h, rangeOf) {
  const heldBefore = drawnWindow(h.c.draws.at(-1).candles);
  const range = rangeOf(heldBefore.end - heldBefore.start, h.c.range);
  const readsBefore = h.c.reads.length;
  await h.scrollTo(range);
  const heldAfter = drawnWindow(h.c.draws.at(-1).candles);
  const reads = h.c.reads.slice(readsBefore);
  const seen = {
    start: Math.max(heldBefore.start, heldBefore.start + Math.ceil(range.from)),
    end: Math.min(heldBefore.end, heldBefore.start + Math.floor(range.to) + 1),
  };
  const seenRows = Math.max(0, seen.end - seen.start);
  return {
    read: reads.reduce((n, r) => n + (r.end - r.start), 0),
    kept: reads.reduce((n, r) => n + overlapRows(r, heldAfter), 0),
    // その読みの前から持っていた区間＝その読みの直前の描画（Test Spy が覚えた描画の数から引く）。
    reread: reads.reduce((n, r) => n + overlapRows(r, drawnWindow(h.c.draws[r.drawsBefore - 1].candles)), 0),
    seenLost: seenRows - overlapRows(seen, heldAfter),
    sizes: reads.map((r) => r.end - r.start),
  };
}

// 見えている幅の 4 点（整数の本数）: 上限の 1/4・上限の 1/3 の直下・上限の 3/5・上限の 3/2。
//   上限の 1/3 の直下は、守る範囲（幅 × 3）が上限にわずかに足りない帯（独立レビュー 推奨 1・2）。
const WIDTHS = [CAP / 4, Math.ceil(CAP / 3) - 1, (CAP * 3) / 5, (CAP * 3) / 2];
// 端数の有無: 見えている範囲の両端へ足す端数（lwc の論理範囲は小数で来る・実測）。
const FRACTIONS = [{ from: 0, to: 0 }, { from: 0.14, to: 0.23 }];
const RUNS = [3_000, 3_000_000];
/** 幅（整数の本数）× 端数の有無。 */
const SHAPES = WIDTHS.flatMap((width) => FRACTIONS.map((fraction) => ({ width, fraction })));
const label = ({ width, fraction }) => `幅 ${width}・端数 ${fraction.from}/${fraction.to}`;

/** 持っている区間の先頭からの整数の位置 `at` から幅 `width` の見えている範囲（端数を足す）。 */
const rangeAt = (at, { width, fraction }) => ({ from: at + fraction.from, to: at + width + fraction.to });

test('検定の前提: 幅の 4 点は整数の本数で、上限の 1/3 の両側と上限の外を通り、端数つきでも帯を出ない', () => {
  const [quarter, third, wide, wider] = WIDTHS;
  assert.ok(WIDTHS.every(Number.isInteger));
  const grown = Math.max(...FRACTIONS.map((f) => f.to - f.from));
  assert.ok(quarter + grown < third && third + grown < CAP / 3 && CAP / 3 - third - grown < 1);
  assert.ok(wide > CAP / 2 && wide < CAP && wider > CAP);
  assert.ok(FRACTIONS.some((f) => f.from === 0 && f.to === 0));
  assert.ok(FRACTIONS.some((f) => !Number.isInteger(f.from) && !Number.isInteger(f.to)));
});

/** 同じ操作の台本: 見えている幅 `shape.width` で、持っている左端の近く・右端の近くを交互に見る。 */
async function wideScript(h, shape) {
  const results = [];
  for (let i = 0; i < 3; i += 1) {
    results.push(await operate(h, () => rangeAt(5, shape)));
    results.push(await operate(h, (heldRows) => rangeAt(heldRows - 5 - shape.width, shape)));
  }
  return results;
}

test('計算量: 読んだ行 − 操作の後も持ち続けた行 = 0・読んだ行のうち読む前から持っていた行 = 0（見えている幅 4 点 × 端数の有無 × run の長さ 2 点）', async () => {
  for (const shape of SHAPES) {
    const perRun = [];
    for (const rows of RUNS) {
      const h = view({ ext: extent({ rows }) });
      await h.v.render('job1');
      await fillToCap(h);
      const results = await wideScript(h, shape);
      const read = results.reduce((n, r) => n + r.read, 0);
      const kept = results.reduce((n, r) => n + r.kept, 0);
      const reread = results.reduce((n, r) => n + r.reread, 0);
      assert.ok(read > 0, `${label(shape)}: 読み足しが起きていない（検定が空虚）`);
      assert.equal(read - kept, 0, `${label(shape)}・run ${rows} 本: 読んだ ${read} 行のうち ${read - kept} 行を捨てた`);
      assert.equal(reread, 0, `${label(shape)}・run ${rows} 本: 読んだ ${read} 行のうち ${reread} 行は読む前から持っていた`);
      assert.ok(h.c.draws.every((d) => d.candles.length <= CAP));
      perRun.push(results.map((r) => r.sizes));
    }
    // 読みの本数と回数は run の長さに依らない。
    assert.deepEqual(perRun[0], perRun[1], label(shape));
  }
});

test('広い表示範囲で読み足しても、見えていた足を捨てない（見えている幅 4 点 × 端数の有無）', async () => {
  for (const shape of SHAPES) {
    const h = view();
    await h.v.render('job1');
    await fillToCap(h);
    for (const r of await wideScript(h, shape)) {
      assert.equal(r.seenLost, 0, `${label(shape)}: 見えていた足を ${r.seenLost} 本捨てた`);
    }
  }
});

test('計算量: 同じ位置で操作を繰り返すと読みは止まり、読んだ行はすべて持ち続け、読み直さない（見えている幅 4 点 × 端数の有無 × run の長さ 2 点・持っている左端の近くと右端の近く）', async () => {
  for (const shape of SHAPES) {
    const perRun = [];
    for (const rows of RUNS) {
      const sizes = [];
      // 持っている左端の近く・右端の近く。
      for (const atOf of [() => 5, (heldRows) => heldRows - 5 - shape.width]) {
        const h = view({ ext: extent({ rows }) });
        await h.v.render('job1');
        await fillToCap(h);
        const origin = drawnWindow(h.c.draws.at(-1).candles);
        const where = `${label(shape)}・run ${rows} 本・位置 ${atOf(CAP)}`;
        const results = [await operate(h, (heldRows) => rangeAt(atOf(heldRows), shape))];
        // 同じ位置のまま操作する（表示範囲は動かさない。Fake の描画の口は本物と同じく、差し替えで
        //   同じ足を同じ画面位置に留める）。守る範囲は上限に収まるので「上限 / 1 回の本数」回までに
        //   覆える。そこから先の操作は、読みが止まっていることを確かめるぶん。
        for (let i = 0; i < 2 * (CAP / READ_ROWS); i += 1) results.push(await operate(h, (_, range) => range));
        const settled = results.slice(CAP / READ_ROWS + 1);
        assert.ok(settled.length > 0);
        const late = settled.reduce((n, r) => n + r.read, 0);
        assert.equal(late, 0, `${where}: 同じ位置で読みが止まらない（覆えた後に ${late} 行を読んだ）`);
        const read = results.reduce((n, r) => n + r.read, 0);
        const kept = results.reduce((n, r) => n + r.kept, 0);
        const reread = results.reduce((n, r) => n + r.reread, 0);
        assert.equal(read - kept, 0, `${where}: 読んだ ${read} 行のうち ${read - kept} 行を同じ操作で捨てた`);
        assert.equal(reread, 0, `${where}: 読んだ ${read} 行のうち ${reread} 行は読む前から持っていた`);
        // 読んだ行 − 最後に持っている行のうち新しく持った行 = 0（前を読んで後を捨て、次に後を読んで
        //   前を捨てる往復が無い）。
        const held = drawnWindow(h.c.draws.at(-1).candles);
        const gained = (held.end - held.start) - overlapRows(held, origin);
        assert.equal(read - gained, 0, `${where}: 読んだ ${read} 行のうち ${read - gained} 行を持っていない`);
        sizes.push(results.map((r) => r.sizes));
      }
      perRun.push(sizes);
    }
    assert.deepEqual(perRun[0], perRun[1], label(shape));
  }
});
