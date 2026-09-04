// chrome_theme_wiring.test.js — クロム配信の端から端までの結線（§7.4 段階 2「4 注入点の結線」）。
//
// 受け口（ChartRenderer.applyChromeColors / :root への setProperty）を作っただけでは、呼び手が
//   居なければ無言で死ぬ（ISSUE-291 と同型の事故）。よって組み立て点（composeChartShell・両 root
//   共有の単一ソース）が applier を生成し、起動時に 1 度配信することを固定する。
//
// 段階 2 ではテーマが存在しないため配信値は恒等（現行リテラル）。段階 3 は「null の代わりに
//   選択中テーマを渡す」だけで済み、結線を足す必要がない。

import test from 'node:test';
import assert from 'node:assert/strict';

import { composeChartShell } from '../js/adapter/front/chart_app_wiring.js';
import { CHROME_CURRENT } from '../js/usecase/chrome_tokens.js';
import { CHROME_DEFAULT } from '../js/usecase/chrome_tokens.js';

function makeEnv() {
  const chartOptionCalls = [];
  const seriesOptionCalls = [];
  const props = new Map();
  const chart = {
    applyOptions: (o) => chartOptionCalls.push(o),
    addSeries: () => series,
    addPane: () => ({ addSeries: () => series, setStretchFactor() {}, setPreserveEmptyPane() {} }),
    panes: () => [],
    timeScale: () => ({ height: () => 20, subscribeVisibleLogicalRangeChange() {} }),
    subscribeCrosshairMove() {},
  };
  const series = {
    applyOptions: (o) => seriesOptionCalls.push(o),
    setData() {}, priceScale: () => ({ applyOptions() {} }),
  };
  const lwc = {
    ColorType: { Solid: 'solid' },
    CrosshairMode: { Normal: 0 },
    CandlestickSeries: 'C', LineSeries: 'L', HistogramSeries: 'H',
    createChart: () => chart,
  };
  const style = {
    setProperty: (k, v) => props.set(k, v),
    removeProperty: (k) => props.delete(k),
  };
  // 最小の要素スタブ（版面アンカー .chart-wrap を含め、どのセレクタにも要素を返す）。
  const el = () => {
    const node = {
      style: {}, dataset: {}, children: [], textContent: '', innerHTML: '',
      classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
      appendChild(c) { node.children.push(c); return c; },
      insertBefore(c) { node.children.push(c); return c; },
      addEventListener() {}, removeEventListener() {},
      setAttribute() {}, removeAttribute() {}, getAttribute: () => null,
      remove() {}, focus() {}, closest: () => null,
      querySelector: () => null, querySelectorAll: () => [],
      getBoundingClientRect: () => ({ top: 0, left: 0, width: 100, height: 100 }),
    };
    return node;
  };
  const anchors = new Map();
  const doc = {
    documentElement: { style },
    createElement: () => el(),
    getElementById: () => null,
    querySelector: (sel) => {
      if (!anchors.has(sel)) {
        anchors.set(sel, el());
      }
      return anchors.get(sel);
    },
    querySelectorAll: () => [],
    addEventListener() {},
    body: el(),
  };
  const storage = { getItem: () => null, setItem() {}, removeItem() {} };
  const fetch = async () => ({ ok: true, status: 200, json: async () => ({}) });
  return { lwc, doc, storage, fetch, chartOptionCalls, seriesOptionCalls, props, container: { clientHeight: 400 } };
}

test('結線: composeChartShell が chromeThemeApplier を組み立てて返す', async () => {
  const env = makeEnv();
  const shell = await composeChartShell({
    lwc: env.lwc, container: env.container, doc: env.doc, storage: env.storage,
    fetch: env.fetch, datasetRef: 'jp225_m1', recentBars: 300,
  });
  assert.ok(shell.chromeThemeApplier, 'chromeThemeApplier が返っていない（段階 3 が掴む先が無い）');
  assert.equal(typeof shell.chromeThemeApplier.apply, 'function');
});

test('結線: 起動時に 1 度配信され、CSS カスタムプロパティが :root へ書かれる', async () => {
  const env = makeEnv();
  await composeChartShell({
    lwc: env.lwc, container: env.container, doc: env.doc, storage: env.storage,
    fetch: env.fetch, datasetRef: 'jp225_m1', recentBars: 300,
  });
  // CSS 機構: app.css の 3 宣言が読む変数が実際に供給されている。
  assert.equal(env.props.get('--ct-text'), CHROME_DEFAULT.text);
  assert.equal(env.props.get('--ct-bullish'), CHROME_DEFAULT.bullish);
  assert.equal(env.props.get('--ct-bearish'), CHROME_DEFAULT.bearish);
});

test('通過条件 1: 起動時配信は恒等（生成時オプションと同一の色を書く）', async () => {
  const env = makeEnv();
  await composeChartShell({
    lwc: env.lwc, container: env.container, doc: env.doc, storage: env.storage,
    fetch: env.fetch, datasetRef: 'jp225_m1', recentBars: 300,
  });
  // createChart 後に applyOptions が来るため、最後の書き込みが現行リテラルであること。
  const last = env.chartOptionCalls.at(-1);
  assert.ok(last, 'chart.applyOptions が呼ばれていない（結線が死んでいる）');
  assert.equal(last.layout.background.color, CHROME_CURRENT.layoutBackground);
  assert.equal(last.grid.vertLines.color, CHROME_CURRENT.gridVertLines);
  assert.equal(last.timeScale.borderColor, CHROME_CURRENT.timeScaleBorder);

  const lastSeries = env.seriesOptionCalls.at(-1);
  assert.ok(lastSeries, 'mainSeries.applyOptions が呼ばれていない');
  assert.equal(lastSeries.upColor, CHROME_CURRENT.candleUp);
  assert.equal(lastSeries.downColor, CHROME_CURRENT.candleDown);
  assert.equal(lastSeries.priceLineColor, CHROME_CURRENT.priceLine);
});

test('F-C11: documentElement が無い環境でも組み立てが完了する', async () => {
  const env = makeEnv();
  const doc = { ...env.doc, documentElement: null };
  await assert.doesNotReject(() => composeChartShell({
    lwc: env.lwc, container: env.container, doc, storage: env.storage,
    fetch: env.fetch, datasetRef: 'jp225_m1', recentBars: 300,
  }));
});

test('F-C11: doc=null（SSR）でも組み立てが完了する', async () => {
  const env = makeEnv();
  await assert.doesNotReject(() => composeChartShell({
    lwc: env.lwc, container: env.container, doc: null, storage: env.storage,
    fetch: env.fetch, datasetRef: 'jp225_m1', recentBars: 300,
  }));
});
