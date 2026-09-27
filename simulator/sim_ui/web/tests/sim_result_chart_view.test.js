// sim_result_chart_view.js — 売買履歴チャートの描画（描かない 2 条件・ライブチャートの組み立て関数を使うこと・計算量）。
//
// 観測の境界: 注入される `chartKit`（live core の公開面）。ライブチャートと同じ組み立て関数を
//   呼んでいることを、公開面の 3 関数（composeChartViewer・installPaneGeometry・installChartOperations）
//   の呼び出しで確かめる。
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { createSimResultChartView } from '../js/adapter/front/sim_result_chart_view.js';
import { resultChartInstances } from '../js/usecase/result_chart_model.js';
import { tradeCloseCurves } from '../../../report_ui/web/js/chart.js';
import { createLinkage } from '../../../report_ui/web/js/linkage.js';

function tradeClose(times) {
  return tradeCloseCurves({ meta: {}, agg: { balance_curve: [] } }, times);
}
import { fakeEl } from './_fakes.js';

const T = [100, 160, 220];

function overlay(datasetRef = 'jp225_mt5_spread', times = T) {
  const zeros = times.map(() => 0);
  return {
    timeframe: '1m',
    dataset_ref: datasetRef,
    indicators: [{ series: 'sma', placement: 'price', time: times, value: times.map(() => 1) }],
    account: {
      time: times, balance: zeros, equity: zeros, drawdown: zeros,
      realized_pnl: zeros, floating_pnl: zeros, margin_level: times.map(() => null),
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
        symbolSpec: null,
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
  return { calls, chartKit };
}

function view({ ov, candles, k }) {
  const host = fakeEl();
  const doc = { createElement: (t) => fakeEl(t) };
  const v = createSimResultChartView({
    doc, host, lwc: {}, chartKit: k.chartKit,
    fetchJson: async () => ov,
    fetchCandles: async (req) => { k.calls.request = req; return candles; },
    loadTradeClose: async (jobId, times) => {
      k.calls.tradeClose = (k.calls.tradeClose || 0) + 1;
      if (k.failTradeClose) throw new Error('結果未生成');
      return tradeClose(times);
    },
  });
  return { v, host };
}

test('台帳外の系列は描かずに理由を出す（足を読みに行かない・チャートを組まない）', async () => {
  const k = kit();
  const { v, host } = view({ ov: overlay(null), candles: [], k });
  assert.equal(await v.render('job1'), false);
  assert.equal(k.calls.request, undefined);
  assert.equal(k.calls.viewer.length, 0);
  assert.match(host.children[0].textContent, /台帳/);
});

test('読んだ足が run の足と違えば描かずに理由を出す', async () => {
  const k = kit();
  const { v, host } = view({ ov: overlay(), candles: [{ time: 100 }, { time: 160 }], k });
  assert.equal(await v.render('job1'), false);
  assert.equal(k.calls.viewer.length, 0);
  assert.match(host.children[0].textContent, /一致しない/);
});

test('ライブチャートと同じ組み立て関数で組み、操作性を付け、重ねる表示の置き場は器にする', async () => {
  const k = kit();
  const { v, host } = view({ ov: overlay(), candles: T.map((time) => ({ time })), k });
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

test('描くとき: 足・全 instance・売買マーク（ChartRenderer を渡す）・run の全期間', async () => {
  const k = kit();
  const candles = T.map((time) => ({ time }));
  const { v } = view({ ov: overlay(), candles, k });
  await v.render('job1');
  assert.equal(k.calls.candles, candles);
  assert.deepEqual(
    k.calls.renderLine.map((c) => c.id),
    resultChartInstances(overlay(), tradeClose(T)).map((inst) => inst.instanceId),
  );
  assert.deepEqual(k.calls.loaded, ['/sim/data/job1/trade_markers.json']);
  assert.equal(k.calls.timeframe, '1m');
  assert.ok(k.calls.markers.chartRenderer, '売買マークに ChartRenderer を渡していない（ペア外の減光が効かない）');
  assert.deepEqual(k.calls.focus, [100, 220]);
  assert.deepEqual(k.calls.request, { datasetRef: 'jp225_mt5_spread', timeframe: '1m', from: 100, to: 220 });
});

test('ペイン別凡例の行は描いた instance と一致し、設定・削除の処理を持たない', async () => {
  const k = kit();
  const { v } = view({ ov: overlay(), candles: T.map((time) => ({ time })), k });
  await v.render('job1');
  const rows = k.calls.legendRows.at(-1);
  assert.deepEqual(rows.map((r) => r.instanceId), k.calls.renderLine.map((c) => c.id));
  assert.ok(rows.every((r) => typeof r.onEye === 'function' && r.onGear === undefined && r.onClose === undefined));
});

test('計算量: 描いた instance − 凡例に出した instance = 0、同じジョブの再 render は組み直さない', async () => {
  const k = kit();
  const { v } = view({ ov: overlay(), candles: T.map((time) => ({ time })), k });
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
    const { v } = view({ ov: overlay('jp225_mt5_spread', times), candles: times.map((time) => ({ time })), k });
    await v.render('job1');
    counts.push([k.calls.viewer.length, k.calls.operations.length, k.calls.renderLine.length]);
  }
  assert.deepEqual(counts[0], counts[1]);
});

test('別のジョブへ替えるときは前のチャートと購読を片付けてから描く', async () => {
  const k = kit();
  const { v } = view({ ov: overlay(), candles: T.map((time) => ({ time })), k });
  await v.render('job1');
  await v.render('job2');
  assert.equal(k.calls.removed, 1);
  assert.deepEqual(k.calls.disposed, { operations: 1, geometry: 1 });
  assert.equal(v.shownJob(), 'job2');
});

test('取引終了時の残高・DD を読めなければ描かずに理由を出す（足ごとだけの半端な描画をしない）', async () => {
  const k = kit();
  k.failTradeClose = true;
  const { v, host } = view({ ov: overlay(), candles: T.map((time) => ({ time })), k });
  assert.equal(await v.render('job1'), false);
  assert.equal(k.calls.viewer.length, 0);
  assert.match(host.children[0].textContent, /取引終了時/);
});

test('計算量: 取引終了時の材料は 1 ジョブにつき 1 回だけ読む（同じジョブの再 render で読み直さない）', async () => {
  const k = kit();
  const { v } = view({ ov: overlay(), candles: T.map((time) => ({ time })), k });
  for (let i = 0; i < 4; i += 1) await v.render('job1');
  assert.equal(k.calls.tradeClose, 1);
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
  const { v } = view({ ov: overlay(), candles: T.map((time) => ({ time })), k });
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
  const { v } = view({ ov: overlay(), candles: T.map((time) => ({ time })), k });
  const linkage = countingLinkage();
  linkage.setHover(4, 'table');
  v.bindLinkage(linkage);
  await v.render('job1');
  assert.equal(k.calls.markerInstances[0].highlighted.at(-1), 4);
});

test('計算量: 同じ linkage を何度渡しても購読は 1 つ・ジョブを替えても増えない', async () => {
  const k = kit();
  const { v } = view({ ov: overlay(), candles: T.map((time) => ({ time })), k });
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
  const candles = T.map((time) => ({ time }));
  let ready = false;
  const v = createSimResultChartView({
    doc, host, lwc: {}, chartKit: k.chartKit,
    fetchJson: async () => {
      if (!ready) { const err = new Error('409'); err.status = 409; throw err; }
      return overlay();
    },
    fetchCandles: async () => candles,
    loadTradeClose: async (jobId, times) => tradeClose(times),
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
    fetchCandles: async () => [],
    loadTradeClose: async () => ({ balData: [], ddData: [] }),
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
  const { v } = view({ ov: overlay('jp225_mt5_spread', times), candles: times.map((time) => ({ time })), k });
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
  const { v, host } = view({ ov: overlay('jp225_mt5_spread', times), candles: times.map((time) => ({ time })), k });
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
