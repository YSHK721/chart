// series_drawer.js — 棒（histogram）の基準 `base` を payload から受け取る口の検証（2026-10-02）。
//
// 由来: 売買履歴チャートの「残高・有効証拠金」ペインで、足ごとの残高を初期資金の高さを基準にした棒で描く
//   （依頼者指示 2026-10-02「残高グラフも同じ仕様にしろ」）。lwc の HistogramSeries の base は既定 0 で、
//   SeriesDrawer は base を渡していなかった。
// 規則（追加だけ）: payload が base を持つ棒のときだけ、生成オプションの base に渡す。持たないときの
//   生成オプション・styleMeta は従来と同じ（ライブ・リプレイの既存経路は base を持たない）。
//   生成以外の写像点（_swapSeriesType）も同じ規則: base を持つ系列はその base、持たない系列は従来の 0。
//
// 観測の境界: 注入する Fake の chart / series（ChartRenderer の公開メソッドだけを呼ぶ）。
// 構造: Arrange-Act-Assert。DOM 非依存。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';

const LineSeries = { kind: 'Line' };
const HistogramSeries = { kind: 'Histogram' };

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
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: { LineSeries, HistogramSeries } });
  return { renderer, chart };
}

const bar = (i) => ({ time: 1_600_000_000 + i * 60, open: 1, high: 2, low: 0.5, close: 1.5 });
const bars = (start, end) => Array.from({ length: end - start }, (_, k) => bar(start + k));
const points = (start, end) => bars(start, end).map((b, k) => ({ time: b.time, value: 9990 + k, color: '#0a0' }));

test('棒の payload が base を持てば、生成オプションの base に渡す', () => {
  const { renderer, chart } = fakes();
  renderer.renderHistogram('pane:残高', [{ name: '残高', kind: 'histogram', color: '#0a0', base: 10000, data: points(0, 3) }]);
  assert.equal(chart.created.at(-1)._def, HistogramSeries);
  assert.equal(chart.created.at(-1)._createOpts.base, 10000);
});

test('base を持たない棒・線の生成オプションと styleMeta は従来と同じ（base のキーを持たない）', () => {
  const { renderer, chart } = fakes();
  renderer.renderHistogram('osc#1', [{ name: 'lc', kind: 'histogram', color: '#0a0', data: points(0, 3) }]);
  renderer.renderLine('ma#1', [{ name: 'ma', kind: 'line', color: '#fff', width: 1, style: 'solid', data: points(0, 3) }]);
  const [hist, line] = chart.created.slice(-2);
  assert.deepEqual(Object.keys(hist._createOpts).sort(), ['color', 'lastValueVisible', 'priceLineVisible']);
  assert.deepEqual(Object.keys(line._createOpts).sort(), ['color', 'lastValueVisible', 'lineStyle', 'lineWidth', 'priceLineVisible']);
  for (const id of ['osc#1', 'ma#1']) {
    assert.ok(renderer.getSeriesStyles(id).every((m) => !('base' in m)), `${id} の styleMeta に base が付いた`);
  }
});

test('線の payload に base があっても線には渡さない（base は棒の基準）', () => {
  const { renderer, chart } = fakes();
  renderer.renderLine('ma#1', [{ name: 'ma', kind: 'line', color: '#fff', width: 1, style: 'solid', base: 5, data: points(0, 3) }]);
  assert.equal('base' in chart.created.at(-1)._createOpts, false);
});

test('表示を保つ差し替え（replaceDataKeepingView）の後も、棒は同じ系列のまま base を保つ', () => {
  const { renderer, chart } = fakes();
  renderer.setCandles(bars(10, 20));
  renderer.renderHistogram('pane:残高', [{ name: '残高', kind: 'histogram', color: '#0a0', base: 10000, data: points(10, 20) }]);
  const before = chart.created.length;
  const hist = chart.created.at(-1);
  renderer.replaceDataKeepingView(bars(0, 20), [{ instanceId: 'pane:残高', payloads: [{ name: '残高', data: points(0, 20) }] }]);
  assert.equal(chart.created.length, before, '系列を作り直した');
  assert.equal(hist._data.length, 20);
  assert.equal(hist._createOpts.base, 10000);
  assert.equal('base' in hist._options, false, '差し替えで base を上書きした');
});

test('系列の種類の切り替え（_swapSeriesType）: base を持つ系列はその base、持たない系列は従来の 0', () => {
  const { renderer, chart } = fakes();
  const payload = (extra) => ({
    name: 'm', kind: 'line', color: '#7b68ee', width: 2, style: 'solid', bar_editable: true,
    data: points(0, 3), ...extra,
  });
  renderer.renderLine('plain#1', [payload({})]);
  renderer.applySeriesStyle('plain#1', 'm', { display: 'bar' });
  assert.equal(chart.created.at(-1)._def, HistogramSeries);
  assert.equal(chart.created.at(-1)._createOpts.base, 0);
  renderer.renderLine('based#1', [payload({ base: 7 })]);
  renderer.applySeriesStyle('based#1', 'm', { display: 'bar' });
  assert.equal(chart.created.at(-1)._def, HistogramSeries);
  assert.equal(chart.created.at(-1)._createOpts.base, 7);
});
