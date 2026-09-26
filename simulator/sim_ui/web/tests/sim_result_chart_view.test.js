// sim_result_chart_view.js — 上のチャート領域へのジョブ結果の描画（描かない 2 条件・系列の数・計算量）。
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { createSimResultChartView } from '../js/adapter/front/sim_result_chart_view.js';
import { resultChartPanes } from '../js/usecase/result_chart_model.js';
import { fakeEl } from './_fakes.js';

const T = [100, 160, 220];

function overlay(datasetRef = 'jp225_mt5_spread') {
  return {
    timeframe: '1m',
    dataset_ref: datasetRef,
    indicators: [{ series: 'sma', placement: 'price', time: T, value: [null, 1, 2] }],
    account: {
      time: T, balance: [1, 1, 1], equity: [1, 1, 1], drawdown: [0, 0, 0],
      realized_pnl: [0, 0, 0], floating_pnl: [0, 0, 0], margin_level: [null, null, null],
    },
  };
}

function kit() {
  const calls = { addSeries: 0, loaded: [], removed: 0, candles: null };
  const chart = {
    addSeries() { calls.addSeries += 1; return { setData() {} }; },
    panes() { return [{ setStretchFactor() {} }]; },
    timeScale() { return { fitContent() {} }; },
    remove() { calls.removed += 1; },
  };
  return {
    calls,
    chartKit: {
      createChartWithMainSeries() {
        return { chart, mainSeries: { setData(c) { calls.candles = c; } } };
      },
      TradeMarkersRenderer: class {
        setCurrentTimeframe(tf) { calls.timeframe = tf; }
        async load(url) { calls.loaded.push(url); return 0; }
      },
    },
  };
}

function view({ ov, candles, k }) {
  const host = fakeEl();
  const doc = { createElement: (t) => fakeEl(t) };
  const v = createSimResultChartView({
    doc, host, lwc: { LineSeries: 'L', AreaSeries: 'A' }, chartKit: k.chartKit,
    fetchJson: async () => ov,
    fetchCandles: async (req) => { k.calls.request = req; return candles; },
  });
  return { v, host };
}

test('台帳外の系列は描かずに理由を出す（足を読みに行かない）', async () => {
  const k = kit();
  const { v, host } = view({ ov: overlay(null), candles: [], k });
  assert.equal(await v.render('job1'), false);
  assert.equal(k.calls.request, undefined);
  assert.equal(k.calls.addSeries, 0);
  assert.match(host.children[0].textContent, /台帳/);
});

test('読んだ足が run の足と違えば描かずに理由を出す', async () => {
  const k = kit();
  const { v, host } = view({ ov: overlay(), candles: [{ time: 100 }, { time: 160 }], k });
  assert.equal(await v.render('job1'), false);
  assert.equal(k.calls.addSeries, 0);
  assert.match(host.children[0].textContent, /一致しない/);
});

test('描くとき: モデルの系列をすべて置き、売買マークをそのジョブから読み、時間足を合わせる', async () => {
  const k = kit();
  const candles = T.map((time) => ({ time }));
  const { v } = view({ ov: overlay(), candles, k });
  assert.equal(await v.render('job1'), true);
  const expected = resultChartPanes(overlay()).reduce((n, p) => n + p.series.length, 0);
  assert.equal(k.calls.addSeries, expected);
  assert.equal(k.calls.candles, candles);
  assert.deepEqual(k.calls.loaded, ['/sim/data/job1/trade_markers.json']);
  assert.equal(k.calls.timeframe, '1m');
  assert.deepEqual(k.calls.request, { datasetRef: 'jp225_mt5_spread', timeframe: '1m', from: 100, to: 220 });
});

test('計算量: 同じジョブを何度 render しても組み直さない（発行 − 表示の変化 = 0）', async () => {
  const k = kit();
  const { v } = view({ ov: overlay(), candles: T.map((time) => ({ time })), k });
  await v.render('job1');
  const once = k.calls.addSeries;
  for (let i = 0; i < 5; i += 1) await v.render('job1');
  assert.equal(k.calls.addSeries, once);
  assert.equal(k.calls.loaded.length, 1);
});

test('別のジョブへ替えるときは前のチャートを片付けてから描く', async () => {
  const k = kit();
  const { v } = view({ ov: overlay(), candles: T.map((time) => ({ time })), k });
  await v.render('job1');
  await v.render('job2');
  assert.equal(k.calls.removed, 1);
  assert.equal(v.shownJob(), 'job2');
});
