// chart_renderer_chrome.test.js — ChartRenderer.applyChromeColors の配線点写像
//   （基本設計_指標カラーテーマ.md §4.2・§5.2 UC-C02 手順 2・§3.4・A-11）。
//
// 配線点 id → lightweight-charts のオプション経路という写像は upstream API の知識であり、
//   宣言された唯一の隔離点（ChartRenderer・ISSUE-262）が持つ。本テストはその写像だけを固定する
//   （色の決定規則は color_resolver.test.js、2 機構への扇形分岐は chrome_theme_applier.test.js）。

import test from 'node:test';
import assert from 'node:assert/strict';

import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';
import { resolveAllChrome } from '../js/usecase/color_resolver.js';
import { CHROME_CURRENT } from '../js/usecase/chrome_tokens.js';

function newRenderer({ chartHasApplyOptions = true, seriesHasApplyOptions = true } = {}) {
  const chartCalls = [];
  const seriesCalls = [];
  const chart = { addSeries: () => ({}), addPane: () => ({}), panes: () => [] };
  if (chartHasApplyOptions) {
    chart.applyOptions = (o) => chartCalls.push(o);
  }
  const mainSeries = { setData() {}, priceScale: () => ({ applyOptions() {} }) };
  if (seriesHasApplyOptions) {
    mainSeries.applyOptions = (o) => seriesCalls.push(o);
  }
  const renderer = new ChartRenderer({
    chart, mainSeries, lwc: { LineSeries: 'L', HistogramSeries: 'H', CandlestickSeries: 'C' },
  });
  return { renderer, chartCalls, seriesCalls };
}

const theme = (roleColors) => ({ themeId: 'thm#1', name: 't', roleColors, tfModifier: null });

test('§5.2 手順 2: chart / mainSeries の applyOptions は各 1 回だけ呼ばれる', () => {
  const { renderer, chartCalls, seriesCalls } = newRenderer();
  renderer.applyChromeColors(resolveAllChrome(null).slots);
  assert.equal(chartCalls.length, 1);
  assert.equal(seriesCalls.length, 1);
});

test('通過条件 1: テーマ未設定なら書き込む値が現行リテラルと文字列一致（恒等）', () => {
  const { renderer, chartCalls, seriesCalls } = newRenderer();
  renderer.applyChromeColors(resolveAllChrome(null).slots);
  const o = chartCalls[0];
  assert.equal(o.layout.background.color, CHROME_CURRENT.layoutBackground);
  assert.equal(o.layout.textColor, CHROME_CURRENT.layoutTextColor);
  assert.equal(o.layout.panes.separatorColor, CHROME_CURRENT.paneSeparator);
  assert.equal(o.layout.panes.separatorHoverColor, CHROME_CURRENT.paneSeparatorHover);
  assert.equal(o.grid.vertLines.color, CHROME_CURRENT.gridVertLines);
  assert.equal(o.grid.horzLines.color, CHROME_CURRENT.gridHorzLines);
  assert.equal(o.rightPriceScale.borderColor, CHROME_CURRENT.rightPriceScaleBorder);
  assert.equal(o.timeScale.borderColor, CHROME_CURRENT.timeScaleBorder);

  const c = seriesCalls[0];
  for (const k of ['upColor', 'borderUpColor', 'wickUpColor']) {
    assert.equal(c[k], CHROME_CURRENT.candleUp, k);
  }
  for (const k of ['downColor', 'borderDownColor', 'wickDownColor']) {
    assert.equal(c[k], CHROME_CURRENT.candleDown, k);
  }
  assert.equal(c.priceLineColor, CHROME_CURRENT.priceLine);
});

test('§4.2: 生成時（chart_bootstrap）と同一の配線点へ書く（両者が食い違わない）', () => {
  // 生成時に使われる配線点と、テーマ適用で書き換わる配線点が同じ表から来ていること。
  const { renderer, chartCalls, seriesCalls } = newRenderer();
  renderer.applyChromeColors(resolveAllChrome(null).slots);
  const written = [
    chartCalls[0].layout.background.color, chartCalls[0].layout.textColor,
    chartCalls[0].layout.panes.separatorColor, chartCalls[0].layout.panes.separatorHoverColor,
    chartCalls[0].grid.vertLines.color, chartCalls[0].grid.horzLines.color,
    chartCalls[0].rightPriceScale.borderColor, chartCalls[0].timeScale.borderColor,
    seriesCalls[0].upColor, seriesCalls[0].downColor, seriesCalls[0].priceLineColor,
  ];
  const expected = [
    CHROME_CURRENT.layoutBackground, CHROME_CURRENT.layoutTextColor,
    CHROME_CURRENT.paneSeparator, CHROME_CURRENT.paneSeparatorHover,
    CHROME_CURRENT.gridVertLines, CHROME_CURRENT.gridHorzLines,
    CHROME_CURRENT.rightPriceScaleBorder, CHROME_CURRENT.timeScaleBorder,
    CHROME_CURRENT.candleUp, CHROME_CURRENT.candleDown, CHROME_CURRENT.priceLine,
  ];
  assert.deepEqual(written, expected);
});

test('ローソクの 6 経路すべてへ同一トークンの色が届く（#10/#11 は 1 配線点 = 6 オプション）', () => {
  const { renderer, seriesCalls } = newRenderer();
  renderer.applyChromeColors(resolveAllChrome(theme({ bullish: '#00ff00', bearish: '#ff00ff' })).slots);
  const c = seriesCalls[0];
  assert.deepEqual([c.upColor, c.borderUpColor, c.wickUpColor], ['#00ff00', '#00ff00', '#00ff00']);
  assert.deepEqual([c.downColor, c.borderDownColor, c.wickDownColor], ['#ff00ff', '#ff00ff', '#ff00ff']);
});

test('背景は色だけを渡し type を保つ（部分マージ・setAnalysisTint と同一方針）', () => {
  const { renderer, chartCalls } = newRenderer();
  renderer.applyChromeColors(resolveAllChrome(theme({ surface: '#202020' })).slots);
  assert.deepEqual(chartCalls[0].layout.background, { color: '#202020' });
  assert.equal('type' in chartCalls[0].layout.background, false, 'type を上書きしない');
});

test('§3.4: 渡すのは色だけ（ビュー・レイアウト設定に触れない）', () => {
  const { renderer, chartCalls, seriesCalls } = newRenderer();
  renderer.applyChromeColors(resolveAllChrome(null).slots);
  const json = JSON.stringify([chartCalls[0], seriesCalls[0]]);
  for (const forbidden of ['autoScale', 'visibleRange', 'rightOffset', 'barSpacing', 'timeVisible',
    'secondsVisible', 'autoSize', 'enableResize', 'crosshair', 'priceLineVisible', 'lastValueVisible']) {
    assert.equal(json.includes(forbidden), false, `${forbidden} を触っている`);
  }
});

test('F-C10: chart が applyOptions を持たなくても no-op（例外を投げず系列側は継続）', () => {
  const { renderer, seriesCalls } = newRenderer({ chartHasApplyOptions: false });
  assert.doesNotThrow(() => renderer.applyChromeColors(resolveAllChrome(null).slots));
  assert.equal(seriesCalls.length, 1, '系列側の配信は継続する');
});

test('F-C10: mainSeries が applyOptions を持たなくても no-op（chart 側は継続）', () => {
  const { renderer, chartCalls } = newRenderer({ seriesHasApplyOptions: false });
  assert.doesNotThrow(() => renderer.applyChromeColors(resolveAllChrome(null).slots));
  assert.equal(chartCalls.length, 1, 'chart 側の配信は継続する');
});

test('引数無しでも例外を投げない（全域的）', () => {
  const { renderer } = newRenderer();
  assert.doesNotThrow(() => renderer.applyChromeColors());
  assert.doesNotThrow(() => renderer.applyChromeColors({}));
});
