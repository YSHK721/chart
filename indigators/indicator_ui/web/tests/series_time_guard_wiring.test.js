// ISSUE-383: SeriesTimeGuard の結線検証 — 契約違反 payload が lwc（Fake series）へ届く前に
//   厳密増加へ畳まれること、清浄 payload は同一参照のまま通ること（挙動 byte 不変）。
//   端から端まで結線を固定する（受け口だけの防壁は無言で死ぬ・ISSUE-291 の教訓）。
// 構造: Arrange-Act-Assert（AAA）。DOM 非依存（Fake chart/lwc 注入・chart_renderer.test.js と同型）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';

const LineSeries = { kind: 'Line' };
const HistogramSeries = { kind: 'Histogram' };

function fakeSeries(def) {
  return {
    _def: def, _data: null, _options: {}, _createOpts: null, _priceLines: [],
    setData(points) { this._data = points; },
    data() { return this._data ?? []; },
    update() {},
    applyOptions(opts) { Object.assign(this._options, opts); },
    createPriceLine(opt) { const pl = { opt }; this._priceLines.push(pl); return pl; },
    removePriceLine(pl) { this._priceLines = this._priceLines.filter((x) => x !== pl); },
    priceScale() {
      return {
        _options: { autoScale: true },
        options() { return this._options; },
        applyOptions() {},
        getVisibleRange() { return null; },
        setVisibleRange() {},
      };
    },
  };
}

function fakeChart() {
  const created = [];
  const panesArr = [];
  const makePane = () => {
    const pane = {
      _series: [],
      paneIndex() { return panesArr.indexOf(pane); },
      setStretchFactor() {},
      setPreserveEmptyPane() {},
      addSeries(def, opts) {
        const s = fakeSeries(def); s._createOpts = opts; s._pane = pane;
        created.push(s); pane._series.push(s); return s;
      },
    };
    return pane;
  };
  return {
    _created: created,
    panes() { return panesArr; },
    addPane() { const p = makePane(); panesArr.push(p); return p; },
    addSeries(def, opts) { const s = fakeSeries(def); s._createOpts = opts; created.push(s); return s; },
    removeSeries() {},
    applyOptions() {},
    timeScale() { return { fitContent() {} }; },
    subscribeCrosshairMove() {},
  };
}

function newRenderer() {
  const chart = fakeChart();
  const main = fakeSeries(undefined);
  const lwc = { LineSeries, HistogramSeries, createTextWatermark() { return { applyOptions() {}, detach() {} }; } };
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc });
  return { renderer, chart };
}

function withSilencedError(fn) {
  const orig = console.error;
  const calls = [];
  console.error = (...args) => { calls.push(args); };
  try { fn(); } finally { console.error = orig; }
  return calls;
}

test('renderLine: 契約違反 payload は series.setData 前に厳密増加へ畳まれる', () => {
  const { renderer, chart } = newRenderer();
  const calls = withSilencedError(() => {
    renderer.renderLine('x#1', [{
      name: 'l', kind: 'line', style: 'solid', width: 1, color: 'red',
      data: [{ time: 10, value: 1 }, { time: 20, value: 2 }, { time: 15, value: 9 }],
    }], { overlay: true });
  });
  const s = chart._created[0];
  assert.deepEqual(s._data, [{ time: 10, value: 1 }, { time: 20, value: 2 }]);
  assert.equal(calls.length, 1); // フィンガープリントが出る（無言で畳まない）
});

test('renderLine: 清浄 payload は同一参照のまま setData へ届く（挙動不変）', () => {
  const { renderer, chart } = newRenderer();
  const data = [{ time: 10, value: 1 }, { time: 20, value: 2 }];
  const calls = withSilencedError(() => {
    renderer.renderLine('x#1', [{ name: 'l', kind: 'line', style: 'solid', width: 1, color: 'red', data }], { overlay: true });
  });
  assert.equal(chart._created[0]._data, data);
  assert.equal(calls.length, 0);
});

test('setData(seriesKey): 既存系列への差替も同じ防壁を通る', () => {
  const { renderer, chart } = newRenderer();
  withSilencedError(() => {
    renderer.renderLine('x#1', [{ name: 'l', kind: 'line', style: 'solid', width: 1, color: 'red', data: [{ time: 1, value: 0 }] }], { overlay: true });
    renderer.setData('x#1::l', [{ time: 5, value: 1 }, { time: 5, value: 2 }, { time: 9, value: 3 }]);
  });
  assert.deepEqual(chart._created[0]._data, [{ time: 5, value: 2 }, { time: 9, value: 3 }]);
});
