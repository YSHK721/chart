// series_drawer.js / chart_renderer.js — 基準つきの面（baseline）の系列種別の検証（2026-10-02）。
//
// 由来: 売買履歴チャートの「残高（足ごと）」と「損益（初期資金比）」を、基準（初期資金・0）を境に上下で
//   塗り分けるグラデーションの面で描く（依頼者指示 2026-10-02「棒グラフではなく、面グラフでグラデーション
//   で表現しろ」）。lightweight-charts v5 の BaselineSeries（vendor で defaultOptions を確認済み:
//   baseValue・topLineColor・topFillColor1/2・bottomLineColor・bottomFillColor1/2・lineWidth・lineStyle）。
// 規則（追加だけ）: kind 'baseline' の payload だけが BaselineSeries になり、基準（payload.base → baseValue）と
//   面の色（payload.baseline）を受け取る。line / histogram / level_dash の生成オプションは、payload が
//   base・baseline を持っていても変わらない（比較の基準は同じテスト内の、それらを持たない payload から導く）。
//
// 観測の境界: 注入する Fake の chart / series（ChartRenderer の公開メソッドだけを呼ぶ）。
// 構造: Arrange-Act-Assert。DOM 非依存。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';
import { RENDER_ROUTES, seriesKind } from '../js/domain/series_kind.js';

const LineSeries = { kind: 'Line' };
const HistogramSeries = { kind: 'Histogram' };
const CandlestickSeries = { kind: 'Candlestick' };
const BaselineSeries = { kind: 'Baseline' };

function fakes() {
  const created = [];
  const series = (def, opts) => ({
    _def: def, _createOpts: opts, _data: null, _options: {},
    setData(points) { this._data = points; },
    data() { return this._data ?? []; },
    update() {},
    applyOptions(o) { Object.assign(this._options, o); },
    createPriceLine(opt) { return { opt }; },
    removePriceLine() {},
    priceScale() { return { applyOptions() {} }; },
  });
  let range = { from: 0, to: 10 };
  const timeScale = {
    fitContent() {}, scrollToRealTime() {}, applyOptions() {},
    getVisibleLogicalRange() { return range; },
    setVisibleLogicalRange(next) { range = next; },
    subscribeVisibleLogicalRangeChange() {}, unsubscribeVisibleLogicalRangeChange() {},
    width() { return 1200; }, options() { return { barSpacing: 4 }; },
  };
  const chart = {
    created,
    timeScale() { return timeScale; },
    subscribeCrosshairMove() {},
    applyOptions() {},
    panes() { return []; },
    addSeries(def, opts) { const s = series(def, opts); created.push(s); return s; },
    removeSeries() {},
  };
  const main = series(null, null);
  const renderer = new ChartRenderer({
    chart, mainSeries: main, lwc: { LineSeries, HistogramSeries, CandlestickSeries, BaselineSeries },
  });
  return { renderer, chart };
}

const bar = (i) => ({ time: 1_600_000_000 + i * 60, open: 1, high: 2, low: 0.5, close: 1.5 });
const bars = (start, end) => Array.from({ length: end - start }, (_, k) => bar(start + k));
const points = (start, end) => bars(start, end).map((b, k) => ({ time: b.time, value: 9990 + k }));

const FILL = Object.freeze({
  topLineColor: '#0a0', topFillColor1: 'rgba(0,170,0,0.3)', topFillColor2: 'rgba(0,170,0,0.1)',
  bottomLineColor: '#a00', bottomFillColor1: 'rgba(170,0,0,0.1)', bottomFillColor2: 'rgba(170,0,0,0.3)',
});
const AREA = Object.freeze({
  name: '残高', kind: 'baseline', color: '#0a0', width: 3, style: 'solid', base: 10000, baseline: FILL,
});

test('kind baseline は BaselineSeries で、基準（baseValue）・面の色・線の太さを payload から受け取る', () => {
  const { renderer, chart } = fakes();
  const data = points(0, 3);
  renderer.renderBaseline('pane:残高', [{ ...AREA, data }], { pane: false });
  const s = chart.created.at(-1);
  assert.equal(s._def, BaselineSeries);
  assert.deepEqual(s._createOpts.baseValue, { type: 'price', price: AREA.base });
  for (const [k, v] of Object.entries(FILL)) assert.equal(s._createOpts[k], v, k);
  assert.equal(s._createOpts.lineWidth, AREA.width);
  assert.deepEqual(s._data, data);
});

test('line / histogram / level_dash は payload が base・baseline を持っても生成オプションが変わらない', () => {
  // 比較の基準: base・baseline を持たない同じ payload（ライブ・リプレイの既存経路と同じ形）。
  const { renderer, chart } = fakes();
  const draw = (method, id, payload) => {
    renderer[method](id, [{ ...payload, data: points(0, 3) }]);
    return chart.created.at(-1)._createOpts;
  };
  const cases = [
    ['renderLine', { name: 'ma', kind: 'line', color: '#fff', width: 1, style: 'solid' }],
    ['renderHistogram', { name: 'lc', kind: 'histogram', color: '#0a0' }],
    ['renderLevelDash', { name: 'lv', kind: 'level_dash', color: '#ff0' }],
  ];
  cases.forEach(([method, payload], k) => {
    const plain = draw(method, `plain#${k}`, payload);
    const carrying = draw(method, `carry#${k}`, { ...payload, base: 5, baseline: FILL });
    assert.deepEqual(carrying, plain, method);
    assert.equal(chart.created.at(-1)._def === BaselineSeries, false, method);
  });
});

test('表示を保つ差し替え（replaceDataKeepingView）は同じ面の系列へ入れ、基準・面の色を保つ', () => {
  const { renderer, chart } = fakes();
  renderer.setCandles(bars(10, 20));
  renderer.renderBaseline('pane:残高', [{ ...AREA, data: points(10, 20) }], { pane: false });
  const before = chart.created.length;
  const s = chart.created.at(-1);
  renderer.replaceDataKeepingView(bars(0, 20), [{ instanceId: 'pane:残高', payloads: [{ name: '残高', data: points(0, 20) }] }]);
  assert.equal(chart.created.length, before, '系列を作り直した');
  assert.equal(s._data.length, 20);
  assert.deepEqual(s._createOpts.baseValue, { type: 'price', price: AREA.base });
  assert.deepEqual(s._options, {}, '差し替えで生成オプションを上書きした');
});

test('目のボタン（setVisible）・凡例の系列（getSeriesStyles）は面にも効く', () => {
  const { renderer, chart } = fakes();
  renderer.renderBaseline('pane:残高', [{ ...AREA, data: points(0, 3) }], { pane: false });
  const s = chart.created.at(-1);
  renderer.setVisible('pane:残高', false);
  assert.equal(s._options.visible, false);
  renderer.setVisible('pane:残高', true);
  assert.equal(s._options.visible, true);
  assert.deepEqual(renderer.getSeriesStyles('pane:残高').map((m) => [m.name, m.kind, m.color]), [['残高', 'baseline', '#0a0']]);
});

test('台帳: baseline は線の太さ・線種を持ち、描画経路 renderBaseline へ振り分けられる', () => {
  const cap = seriesKind('baseline');
  assert.equal(cap.seriesType, 'baseline');
  assert.equal(cap.appliesLineStyle, true);
  assert.equal(cap.supportsHeat, false);
  assert.equal(cap.renderRoute, 'baseline');
  const route = RENDER_ROUTES.find((r) => r.route === cap.renderRoute);
  assert.equal(route.method, 'renderBaseline');
  // 既存の経路の順（z 順）は変えない: baseline は既存の経路すべての後ろ（水準線の前）。
  const order = RENDER_ROUTES.map((r) => r.route);
  assert.deepEqual(order.filter((r) => r !== 'baseline'), ['histogram', 'line', 'level_dash', 'horizontal']);
});
