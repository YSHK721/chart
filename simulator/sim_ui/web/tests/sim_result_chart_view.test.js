// sim_result_chart_view.js — 売買履歴チャートの描画（描かない条件・ライブチャートの組み立て関数を使うこと・計算量）。
//
// 観測の境界: 注入される `chartKit`（live core の公開面）・`loadReport`（report.json の取得）・
//   `tradeCloseCurves`（取引終了時の残高・DD の単一ソース）。ライブチャートと同じ組み立て関数を
//   呼んでいることを、公開面の 3 関数（composeChartViewer・installPaneGeometry・installChartOperations）
//   の呼び出しで確かめる。足はジョブ自身の report.json の足（ISSUE-552/554 段階 1）。
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { createSimResultChartView } from '../js/adapter/front/sim_result_chart_view.js';
import { firstSegment } from '../js/adapter/front/report_source_client.js';
import { resultChartInstances } from '../js/usecase/result_chart_model.js';
import { tradeCloseCurves } from '../../../report_ui/web/js/chart.js';
import { createLinkage } from '../../../report_ui/web/js/linkage.js';

function tradeClose(times) {
  return tradeCloseCurves({ meta: {}, agg: { balance_curve: [] } }, times);
}
import { fakeEl } from './_fakes.js';

const T = [100, 160, 220];

/** chart_overlay.json（値の列だけ・時刻を持たない）。 */
function overlay(datasetRef = 'jp225_mt5_spread', n = T.length) {
  const zeros = Array.from({ length: n }, () => 0);
  return {
    timeframe: '1m',
    dataset_ref: datasetRef,
    indicators: [{ series: 'sma', placement: 'price', value: zeros.map(() => 1) }],
    account: {
      balance: zeros, equity: zeros, drawdown: zeros,
      realized_pnl: zeros, floating_pnl: zeros, margin_level: zeros.map(() => null),
    },
  };
}

/** report.json（sim の単一区間 "single"・足は run が実行した Bar 列）。 */
function report(times = T, symbol = 'JP225') {
  return {
    meta: { initial_deposit: 1000, symbol },
    segments: {
      single: {
        meta: {},
        bars: times.map((time) => ({ time, open: 1, high: 2, low: 0.5, close: 1.5 })),
        agg: { balance_curve: [] },
      },
    },
  };
}

function kit() {
  const calls = {
    viewer: [], operations: [], geometry: 0, renderLine: [], legendRows: [], candles: null,
    loaded: [], removed: 0, disposed: { operations: 0, geometry: 0 }, focus: null, markers: null,
  };
  const chartKit = {
    composeChartViewer(args) {
      calls.viewer.push(args);
      return {
        symbolSpec: calls.kit.symbolSpec ?? null,
        chart: {
          remove() { calls.removed += 1; },
          timeScale() {
            return {
              width: () => calls.plotWidth ?? 1514,
              subscribeSizeChange(fn) { calls.sizeHandler = fn; },
              unsubscribeSizeChange(fn) { if (calls.sizeHandler === fn) calls.sizeHandler = null; },
            };
          },
          applyOptions(o) { (calls.chartOptions = calls.chartOptions || []).push(o); },
        },
        mainSeries: {},
        currentPriceView: { render() {} },
        paneLegendView: { setInstances(rows) { calls.legendRows.push(rows); } },
        renderer: {
          setCandles(c) { calls.candles = c; },
          renderLine(id, payloads, opts) { calls.renderLine.push({ id, payloads, opts }); },
          setVisible() {},
          lastClose() { return 1; },
          focusTimeRange(from, to) { calls.focus = [from, to]; },
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
    ChartToastView: class {},
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
 * @param {object} args
 * @param {object} args.ov   chart_overlay.json
 * @param {function} [args.rep] (jobId) => report.json（呼ばれるたびに新しい実体を返す＝取得 1 回ごとに別物）
 */
function view({ ov, rep = () => report(), k }) {
  const host = fakeEl();
  const doc = { createElement: (t) => fakeEl(t) };
  k.calls.reports = [];
  k.calls.tradeCloseSegments = [];
  const v = createSimResultChartView({
    doc, host, lwc: {}, chartKit: k.chartKit,
    fetchJson: async () => ov,
    // Test Spy: report.json の取得を数える（取得した実体を覚える）。
    loadReport: async (jobId) => {
      if (k.failReport) throw k.failReport;
      const payload = rep(jobId);
      k.calls.reports.push(payload);
      return payload;
    },
    // Test Spy: 取引終了時の残高・DD をどの区間から作ったかを覚える（値は本物の関数で作る）。
    tradeCloseCurves: (segment, times, deposit) => {
      k.calls.tradeCloseSegments.push(segment);
      return tradeCloseCurves(segment, times, deposit);
    },
  });
  return { v, host };
}

/** 使った取得＝その取得の足をローソク足に描き、同じ取得の区間から取引終了時の残高・DD を作ったもの。 */
function usedReports(k) {
  return k.calls.reports.filter((p) => {
    const seg = firstSegment(p);
    return k.calls.candles === seg.bars && k.calls.tradeCloseSegments.includes(seg);
  });
}

test('台帳外の系列（dataset_ref が null）でもジョブ自身の足で描く', async () => {
  const k = kit();
  const { v } = view({ ov: overlay(null), k });
  assert.equal(await v.render('job1'), true);
  assert.deepEqual(k.calls.candles.map((c) => c.time), T);
});

test('値の列の長さが report.json の足の本数と違えば描かずに理由を出す（黙ってずらさない）', async () => {
  const k = kit();
  const { v, host } = view({ ov: overlay('jp225_mt5_spread', 2), k });
  assert.equal(await v.render('job1'), false);
  assert.equal(k.calls.viewer.length, 0);
  assert.match(host.children[0].textContent, /一致しない/);
  assert.match(host.children[0].textContent, /長さ 2 が足の本数 3/);
});

test('report.json に足が無ければ描かずに理由を出す', async () => {
  const k = kit();
  const { v, host } = view({ ov: overlay('jp225_mt5_spread', 0), rep: () => report([]), k });
  assert.equal(await v.render('job1'), false);
  assert.equal(k.calls.viewer.length, 0);
  assert.match(host.children[0].textContent, /足が無い/);
});

test('区間の無い report.json も描かずに理由を出す', async () => {
  const k = kit();
  const { v, host } = view({ ov: overlay(), rep: () => ({ segments: {} }), k });
  assert.equal(await v.render('job1'), false);
  assert.equal(k.calls.viewer.length, 0);
  assert.match(host.children[0].textContent, /足が無い/);
});

test('ライブチャートと同じ組み立て関数で組み、操作性を付け、重ねる表示の置き場は器にする', async () => {
  const k = kit();
  const { v, host } = view({ ov: overlay(), k });
  assert.equal(await v.render('job1'), true);
  assert.equal(k.calls.viewer.length, 1);
  assert.equal(k.calls.viewer[0].anchor, host);
  assert.equal(k.calls.viewer[0].datasetRef, 'jp225_mt5_spread');
  assert.equal(k.calls.geometry, 1);
  assert.equal(k.calls.operations.length, 1);
  assert.equal(k.calls.operations[0].anchor, host);
  // 右クリック「情報をコピーする」の文脈は売買履歴チャートのジョブのもの。
  assert.equal(k.calls.operations[0].getMenuContext().timeframe, '1m');
});

test('描くとき: 足（report.json の足そのもの）・全 instance・売買マーク（ChartRenderer を渡す）・run の全期間', async () => {
  const k = kit();
  const { v } = view({ ov: overlay(), k });
  await v.render('job1');
  assert.equal(k.calls.candles, firstSegment(k.calls.reports[0]).bars);
  assert.deepEqual(
    k.calls.renderLine.map((c) => c.id),
    resultChartInstances(overlay(), T, tradeClose(T)).map((inst) => inst.instanceId),
  );
  // 指標・口座の点の時刻は report.json の足の時刻。
  for (const call of k.calls.renderLine) {
    for (const p of call.payloads) assert.deepEqual(p.data.map((q) => q.time), T, `${call.id} ${p.name}`);
  }
  assert.deepEqual(k.calls.loaded, ['/sim/data/job1/trade_markers.json']);
  assert.equal(k.calls.timeframe, '1m');
  assert.ok(k.calls.markers.chartRenderer, '売買マークに ChartRenderer を渡していない（ペア外の減光が効かない）');
  assert.deepEqual(k.calls.focus, [100, 220]);
});

test('取引終了時の残高・DD は report.json の先頭の区間と payload の初期証拠金から作る', async () => {
  const k = kit();
  const { v } = view({ ov: overlay(), k });
  await v.render('job1');
  const seg = firstSegment(k.calls.reports[0]);
  const bal = k.calls.renderLine.find((c) => c.id === 'pane:残高・有効証拠金').payloads
    .find((p) => p.name === '残高（取引終了時）').data;
  assert.deepEqual(bal, tradeCloseCurves(seg, T, 1000).balData);
});

test('右クリックのコピーの銘柄は、台帳で引けない系列でも report.json の meta.symbol（同じ 1 回の取得から）', async () => {
  const k = kit();
  const { v } = view({ ov: overlay(null), k });
  assert.equal(await v.render('job1'), true);
  assert.equal(k.calls.operations[0].getMenuContext().symbol, 'JP225');
  // 計算量: 銘柄のために report.json を取り直さない（取得 − 使った取得 = 0）。
  assert.equal(k.calls.reports.length - usedReports(k).length, 0);
});

test('右クリックのコピーの銘柄は、台帳の銘柄仕様が引ければそれを優先する', async () => {
  const k = kit();
  k.symbolSpec = { symbol: 'LEDGER', digits: 1 };
  const { v } = view({ ov: overlay(), rep: () => report(T, 'JP225'), k });
  assert.equal(await v.render('job1'), true);
  assert.equal(k.calls.operations[0].getMenuContext().symbol, 'LEDGER');
});

test('ペイン別凡例の行は描いた instance と一致し、設定・削除の処理を持たない', async () => {
  const k = kit();
  const { v } = view({ ov: overlay(), k });
  await v.render('job1');
  const rows = k.calls.legendRows.at(-1);
  assert.deepEqual(rows.map((r) => r.instanceId), k.calls.renderLine.map((c) => c.id));
  assert.ok(rows.every((r) => typeof r.onEye === 'function' && r.onGear === undefined && r.onClose === undefined));
});

test('計算量: 描いた instance − 凡例に出した instance = 0、同じジョブの再 render は組み直さない', async () => {
  const k = kit();
  const { v } = view({ ov: overlay(), k });
  await v.render('job1');
  const drawn = new Set(k.calls.renderLine.map((c) => c.id));
  const listed = new Set(k.calls.legendRows.at(-1).map((r) => r.instanceId));
  assert.deepEqual([...drawn].filter((id) => !listed.has(id)), []);
  const once = k.calls.renderLine.length;
  for (let i = 0; i < 5; i += 1) await v.render('job1');
  assert.equal(k.calls.renderLine.length, once);
  assert.equal(k.calls.viewer.length, 1);
  assert.equal(k.calls.loaded.length, 1);
});

test('計算量: 足の数を増やしても組み立ての回数と instance の数は変わらない', async () => {
  const counts = [];
  for (const n of [3, 3000]) {
    const times = Array.from({ length: n }, (_, i) => 60 * (i + 1));
    const k = kit();
    const { v } = view({ ov: overlay('jp225_mt5_spread', n), rep: () => report(times), k });
    await v.render('job1');
    counts.push([k.calls.viewer.length, k.calls.operations.length, k.calls.renderLine.length]);
  }
  assert.deepEqual(counts[0], counts[1]);
});

test('別のジョブへ替えるときは前のチャートと購読を片付けてから描く', async () => {
  const k = kit();
  const { v } = view({ ov: overlay(), k });
  await v.render('job1');
  await v.render('job2');
  assert.equal(k.calls.removed, 1);
  assert.deepEqual(k.calls.disposed, { operations: 1, geometry: 1 });
  assert.equal(v.shownJob(), 'job2');
});

test('report.json を読めなければ描かずに理由を出し、覚えない（再試行できる）', async () => {
  const k = kit();
  k.failReport = new Error('結果未生成');
  const { v, host } = view({ ov: overlay(), k });
  assert.equal(await v.render('job1'), false);
  assert.equal(k.calls.viewer.length, 0);
  assert.match(host.children[0].textContent, /report\.json/);
  assert.match(host.children[0].textContent, /結果未生成/);
  assert.equal(v.shownJob(), null);
});

test('report.json が 409（未完了）なら完了待ちの理由を出す', async () => {
  const k = kit();
  k.failReport = Object.assign(new Error('結果未生成'), { status: 409 });
  const { v, host } = view({ ov: overlay(), k });
  assert.equal(await v.render('job1'), false);
  assert.match(host.children[0].textContent, /完了していない/);
  assert.equal(v.shownJob(), null);
});

test('計算量: report.json の取得 − 使った取得 = 0（足と取引終了時の残高・DD を同じ 1 回の取得から作る・再 render で取り直さない）', async () => {
  const k = kit();
  const { v } = view({ ov: overlay(), k });
  for (let i = 0; i < 4; i += 1) await v.render('job1');
  assert.ok(k.calls.reports.length > 0, '取得が観測口を通っていない（検定が空虚）');
  assert.equal(k.calls.reports.length - usedReports(k).length, 0);
});

test('計算量: 足の本数を変えても report.json の取得は増えない（取得 − 使った取得 = 0 を 2 点で）', async () => {
  const fetched = [];
  for (const n of [3, 3000]) {
    const times = Array.from({ length: n }, (_, i) => 60 * (i + 1));
    const k = kit();
    const { v } = view({ ov: overlay('jp225_mt5_spread', n), rep: () => report(times), k });
    assert.equal(await v.render('job1'), true);
    assert.equal(k.calls.reports.length - usedReports(k).length, 0);
    fetched.push(k.calls.reports.length);
  }
  assert.equal(fetched[0], fetched[1]);
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
  const k = kit();
  const { v } = view({ ov: overlay(), k });
  await v.render('job1');
  const linkage = countingLinkage();
  v.bindLinkage(linkage);
  const markers = k.calls.markerInstances[0];

  linkage.setHover(7, 'table');
  assert.equal(markers.highlighted.at(-1), 7);

  const seen = [];
  linkage.subscribe((id, source) => seen.push([id, source]));
  markers.notify(3);
  markers.notify(null);
  assert.deepEqual(seen, [[3, 'chart'], [null, 'chart']]);
});

test('linkage が先に届いても、描いた後の売買マークを今の hover の取引で強調する', async () => {
  const k = kit();
  const { v } = view({ ov: overlay(), k });
  const linkage = countingLinkage();
  linkage.setHover(4, 'table');
  v.bindLinkage(linkage);
  await v.render('job1');
  assert.equal(k.calls.markerInstances[0].highlighted.at(-1), 4);
});

test('計算量: 同じ linkage を何度渡しても購読は 1 つ・ジョブを替えても増えない', async () => {
  const k = kit();
  const { v } = view({ ov: overlay(), k });
  const linkage = countingLinkage();
  await v.render('job1');
  for (let i = 0; i < 5; i += 1) v.bindLinkage(linkage);
  await v.render('job2');
  v.bindLinkage(linkage);
  assert.equal(linkage.subscriptions, 1);
  // 替えた後の hover は新しい売買マークへ届く（古い売買マークへは届かない）。
  const [oldMarkers, newMarkers] = k.calls.markerInstances;
  const oldCount = oldMarkers.highlighted.length;
  linkage.setHover(9, 'table');
  assert.equal(newMarkers.highlighted.at(-1), 9);
  assert.equal(oldMarkers.highlighted.length, oldCount);
});

// ---- ISSUE-540: ジョブ未完了（409）の掲示と、完了後の load イベントでの再試行 ----

test('材料が 409（未完了）なら理由を出し、次の render（子文書の読み直し）で組み立て直せる', async () => {
  const k = kit();
  const host = fakeEl();
  const doc = { createElement: (t) => fakeEl(t) };
  let ready = false;
  const v = createSimResultChartView({
    doc, host, lwc: {}, chartKit: k.chartKit,
    fetchJson: async () => {
      if (!ready) { const err = new Error('409'); err.status = 409; throw err; }
      return overlay();
    },
    loadReport: async () => report(),
    tradeCloseCurves,
  });

  assert.equal(await v.render('job1'), false);
  assert.match(host.children[0].textContent, /完了していない/);
  assert.equal(k.calls.viewer.length, 0, '未完了の間はチャートを組まない');

  ready = true;
  assert.equal(await v.render('job1'), true, '同じジョブでも読めなかった分は再試行する');
  assert.equal(k.calls.viewer.length, 1);
});

test('409 以外の読み込み失敗も理由を出し、覚えない（再試行できる）', async () => {
  const k = kit();
  const host = fakeEl();
  const v = createSimResultChartView({
    doc: { createElement: (t) => fakeEl(t) }, host, lwc: {}, chartKit: k.chartKit,
    fetchJson: async () => { throw new Error('接続できません'); },
    loadReport: async () => report(),
    tradeCloseCurves,
  });
  assert.equal(await v.render('job1'), false);
  assert.match(host.children[0].textContent, /接続できません/);
  assert.equal(v.shownJob(), null);
});

test('足 1 本の最小幅を run の本数に合わせてから全期間を指定する（既定 0.5px では 描画幅÷0.5 本で止まる・2026-09-27 実測）', async () => {
  // Arrange: 描画幅 1000px に 5000 本（既定 0.5px なら 2000 本しか入らない）。
  const times = Array.from({ length: 5000 }, (_, i) => 100 + i * 60);
  const k = kit();
  k.calls.plotWidth = 1000;
  const { v } = view({ ov: overlay('jp225_mt5_spread', times.length), rep: () => report(times), k });
  // Act
  assert.equal(await v.render('job1'), true);
  // Assert: 最小幅 ≤ 描画幅 ÷ 本数、全期間を指定。
  const spacing = k.calls.chartOptions.at(-1).timeScale.minBarSpacing;
  assert.ok(spacing * times.length <= 1000, `最小幅 ${spacing}px では ${times.length} 本が 1000px に入りません`);
  assert.deepEqual(k.calls.focus, [times[0], times.at(-1)]);
  // 計算量: 最小幅の設定の発行 − 描画に使った設定 = 0（1 回の描画で決め直すのは 1 回だけ）。
  assert.equal(k.calls.chartOptions.length - 1, 0);
});

test('利用者が操作するまでは幅が確定していくたびに全期間を指定し直し、操作の後は表示に触れない', async () => {
  // Arrange: 組み立て直後の描画幅は 0。実測では 23ms で 1600→1540→1514px と確定していった。
  const times = Array.from({ length: 5000 }, (_, i) => 100 + i * 60);
  const k = kit();
  k.calls.plotWidth = 0;
  const { v, host } = view({ ov: overlay('jp225_mt5_spread', times.length), rep: () => report(times), k });
  const spacingOf = () => k.calls.chartOptions.at(-1).timeScale.minBarSpacing;
  // Act / Assert: 幅 0 の間は何も決めない（推測の幅を使わない）。
  assert.equal(await v.render('job1'), true);
  assert.equal(k.calls.chartOptions, undefined);
  assert.equal(k.calls.focus, null);
  // 幅が確定していく間は、そのたびに最小幅を決め直して全期間を指定する。
  const focuses = [];
  for (const w of [1600, 1540, 1514]) {
    k.calls.focus = null;
    k.calls.sizeHandler(w, 400);
    assert.ok(spacingOf() * times.length <= w);
    focuses.push(k.calls.focus);
  }
  assert.deepEqual(focuses, [[times[0], times.at(-1)], [times[0], times.at(-1)], [times[0], times.at(-1)]]);
  // 利用者が操作した（押した）後は、幅が変わっても最小幅だけを決め直し、表示には触れない。
  const container = host.children.find((c) => c.className === 'sim-result-chart-canvas');
  container._listeners.pointerdown[0]();
  k.calls.focus = 'kept';
  k.calls.sizeHandler(800, 400);
  assert.ok(spacingOf() * times.length <= 800);
  assert.equal(k.calls.focus, 'kept');
  // 計算量: 最小幅の設定の発行 − 幅の通知（測れたもの）= 0。
  assert.equal(k.calls.chartOptions.length - 4, 0);
});
