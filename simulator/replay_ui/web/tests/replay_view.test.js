// replay_view.test.js — 再生層副作用アダプタの検証（fake chart/series/renderer/DOM 注入・AAA）。
//   参照実装＝プロト replay.js の副作用（attachPrimitive / setVisibleLogicalRange / setCandles /
//   syncPaneDims / syncModeOptions / renderPresets）。lwc/DOM 実体には触れない。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ReplayView } from '../js/adapter/front/replay_view.js';

// ---- 最小 fake DOM ---- //
function fakeEl(extra = {}) {
  return {
    _text: '', set textContent(v) { this._text = v; }, get textContent() { return this._text; },
    classList: { _s: new Set(), toggle(c, on) { if (on) this._s.add(c); else this._s.delete(c); }, has(c) { return this._s.has(c); } },
    style: {}, addEventListener() {}, appendChild(c) { (this.children ||= []).push(c); },
    set innerHTML(_v) { this.children = []; }, get innerHTML() { return ''; },
    ...extra,
  };
}
function fakeDoc(byId = {}) {
  return {
    getElementById: (id) => byId[id] || null,
    createElement: () => fakeEl({ onclick: null }),
    querySelectorAll: () => [],
  };
}
function fakeSeries() {
  const attached = [];
  return { attached, attachPrimitive(p) { attached.push(p); } };
}

test('constructor attaches a boundary-dim primitive to mainSeries', () => {
  const mainSeries = fakeSeries();
  new ReplayView({ chart: { timeScale: () => ({}) }, mainSeries, renderer: {}, document: fakeDoc() });
  assert.equal(mainSeries.attached.length, 1); // 減光primitive 1本を装着
});

test('setVisibleLogicalRange delegates to chart.timeScale().setVisibleLogicalRange', () => {
  let got = null;
  const chart = { timeScale: () => ({ setVisibleLogicalRange: (r) => { got = r; } }) };
  const v = new ReplayView({ chart, mainSeries: fakeSeries(), renderer: {}, document: fakeDoc() });
  v.setVisibleLogicalRange({ from: 1, to: 9 });
  assert.deepEqual(got, { from: 1, to: 9 });
});

test('setCandles delegates to renderer.setCandles', () => {
  let got = null;
  const renderer = { setCandles: (c) => { got = c; } };
  const v = new ReplayView({ chart: { timeScale: () => ({}) }, mainSeries: fakeSeries(), renderer, document: fakeDoc() });
  v.setCandles([{ time: 1 }]);
  assert.deepEqual(got, [{ time: 1 }]);
});

test('syncBoundary sets the boundary time on main + pane series (pane 0 skipped)', () => {
  const paneSeries = fakeSeries();
  const chart = {
    timeScale: () => ({}),
    panes: () => [
      { getSeries: () => [fakeSeries()] }, // pane 0 = メイン（スキップ）
      { getSeries: () => [paneSeries] },   // pane 1 = オシレータ（減光同期対象）
    ],
  };
  const v = new ReplayView({ chart, mainSeries: fakeSeries(), renderer: {}, document: fakeDoc() });
  const candles = [{ time: 100 }, { time: 200 }, { time: 300 }];
  v.syncBoundary({ replayStart: 2, candles });
  assert.equal(paneSeries.attached.length, 1); // pane 1 系列へ減光primitive を装着
  assert.equal(paneSeries.attached[0]._boundaryTime, 300); // candles[2].time
});

test('applyModeDegeneration hides degenerate options and retreats selection to real_ticks', () => {
  const options = [
    { value: 'real_ticks', hidden: false, disabled: false },
    { value: 'ohlc_1min', hidden: false, disabled: false },
    { value: 'every_tick', hidden: false, disabled: false },
  ];
  const modeEl = { value: 'ohlc_1min', options };
  const v = new ReplayView({ chart: { timeScale: () => ({}) }, mainSeries: fakeSeries(), renderer: {}, document: fakeDoc({ 'rp-mode': modeEl }) });
  v.applyModeDegeneration(new Set(['ohlc_1min', 'every_tick']));
  assert.equal(options[1].hidden, true);  // ohlc_1min 非表示
  assert.equal(options[2].disabled, true); // every_tick 無効
  assert.equal(modeEl.value, 'real_ticks'); // 縮退モード選択中→退避
});

test('renderPresets builds a button per preset and wires onSelect with its secs', () => {
  const host = fakeEl();
  const v = new ReplayView({ chart: { timeScale: () => ({}) }, mainSeries: fakeSeries(), renderer: {}, document: fakeDoc({ 'rp-presets': host }) });
  const picked = [];
  v.renderPresets({ presets: [['3か月', 100], ['全期間', null]], activeSecs: null, onSelect: (s) => picked.push(s) });
  assert.equal(host.children.length, 2);
  host.children[0].onclick(); // 3か月
  host.children[1].onclick(); // 全期間
  assert.deepEqual(picked, [100, null]);
});
