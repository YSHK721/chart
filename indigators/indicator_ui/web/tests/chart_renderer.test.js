// chart_renderer.js（ChartRendererPort 実装・upstream 隔離点）の仕様検証。
//
// 設計入力: 内部設計書 §3.3.4（renderLine/renderHistogram/renderHorizontal/setData/setVisible/remove）、
//   §7.1.2（系列キー {instanceId}::{name}・隠蔽契約）、§3.3.6（lineStyle 文字列→整数）。
// upstream JS API（v5: addSeries/addPane/panes/removePane/createTextWatermark/...）はテストでは
//   Fake chart/lwc を注入して観測する。構造: Arrange-Act-Assert（AAA）。DOM 非依存。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';

// v5 シリーズ定義トークン（実体は object。renderer は addSeries(def, …) に渡すだけ）。
const LineSeries = { kind: 'Line' };
const HistogramSeries = { kind: 'Histogram' };

// Fake series。setData / applyOptions / createPriceLine / removePriceLine を記録。
function fakeSeries(def) {
  return {
    _def: def, _data: null, _options: {}, _createOpts: null, _priceLines: [],
    _kind: def === HistogramSeries ? 'histogram' : (def === LineSeries ? 'line' : 'candle'),
    setData(points) { this._data = points; },
    applyOptions(opts) { Object.assign(this._options, opts); },
    createPriceLine(opt) { const pl = { opt }; this._priceLines.push(pl); return pl; },
    removePriceLine(pl) { this._priceLines = this._priceLines.filter((x) => x !== pl); },
  };
}

// Fake chart（v5）。panes()/addPane()/removePane()/addSeries()/subscribeCrosshairMove を備える。
function fakeChart() {
  const created = [];
  const removed = [];
  let fitCount = 0;
  let crosshairHandler = null;
  const panesArr = [];
  const makePane = (preserve = false) => {
    const pane = {
      _series: [], _stretch: null, _preserve: preserve,
      paneIndex() { return panesArr.indexOf(pane); },
      setStretchFactor(f) { this._stretch = f; },
      setPreserveEmptyPane(p) { this._preserve = p; },
      addSeries(def, opts) {
        const s = fakeSeries(def); s._createOpts = opts; s._pane = pane;
        created.push(s); pane._series.push(s); return s;
      },
    };
    return pane;
  };
  panesArr.push(makePane(true)); // pane 0（ローソク・常に保持）
  const chart = {
    created, removed,
    get fitCount() { return fitCount; },
    panes() { return panesArr; },
    addPane(preserveEmptyPane = false) { const p = makePane(preserveEmptyPane); panesArr.push(p); return p; },
    removePane(index) { panesArr.splice(index, 1); },
    addSeries(def, opts) { return panesArr[0].addSeries(def, opts); }, // overlay → pane 0
    removeSeries(s) {
      removed.push(s);
      // v5 の挙動を模す: 系列を持ち主 pane から外し、空かつ非保持なら pane を自動削除する。
      const pane = s._pane;
      if (pane) {
        pane._series = pane._series.filter((x) => x !== s);
        if (pane._series.length === 0 && !pane._preserve) {
          const i = panesArr.indexOf(pane);
          if (i >= 0) panesArr.splice(i, 1);
        }
      }
    },
    timeScale() { return { fitContent() { fitCount += 1; } }; },
    subscribeCrosshairMove(h) { crosshairHandler = h; },
    fireCrosshair(param) { if (crosshairHandler) crosshairHandler(param); },
  };
  return chart;
}

// Fake lwc 名前空間（v5 シリーズ定義 + createTextWatermark）。
function fakeLwc() {
  const watermarks = [];
  return {
    LineSeries, HistogramSeries,
    _watermarks: watermarks,
    createTextWatermark(pane, opts) {
      const wm = { pane, _options: opts, _detached: false, applyOptions(o) { this._options = o; }, detach() { this._detached = true; } };
      watermarks.push(wm);
      return wm;
    },
  };
}

function fakeMainSeries() {
  return fakeSeries(undefined);
}

function newRenderer() {
  const chart = fakeChart();
  const main = fakeMainSeries();
  const lwc = fakeLwc();
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc });
  return { renderer, chart, main, lwc };
}

// ===========================================================================
// setCandles
// ===========================================================================

test('setCandles: sets main series data and fits the time scale', () => {
  const { renderer, chart, main } = newRenderer();
  const candles = [{ time: 1, open: 1, high: 2, low: 0, close: 1.5 }];
  renderer.setCandles(candles);
  assert.deepEqual(main._data, candles);
  assert.equal(chart.fitCount, 1);
});

test('setCandles: empty/undefined yields empty data (no throw)', () => {
  const { renderer, main } = newRenderer();
  renderer.setCandles();
  assert.deepEqual(main._data, []);
});

// ===========================================================================
// renderLine（overlay = pane 0）: addSeries(LineSeries) + setData、系列キー {instanceId}::{name}
// ===========================================================================

test('renderLine: creates one line series per payload and sets its data (overlay = pane 0)', () => {
  const { renderer, chart } = newRenderer();
  const payloads = [
    { name: 'btlm_mean', kind: 'line', style: 'solid', width: 2, color: 'red', data: [{ time: 1, value: 10 }] },
    { name: 'btlm_q5', kind: 'line', style: 'dotted', width: 1, color: 'blue', data: [{ time: 1, value: 9 }] },
  ];
  renderer.renderLine('tgp_btlm#1', payloads);
  assert.equal(chart.created.length, 2);
  assert.equal(chart.panes().length, 1); // overlay は pane を増やさない
  assert.deepEqual(chart.created[0]._data, [{ time: 1, value: 10 }]);
  assert.deepEqual(chart.created[1]._data, [{ time: 1, value: 9 }]);
});

test('renderLine: maps lineStyle string to integer (solid=0,dotted=1,dashed=2)', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('x#1', [
    { name: 'a', kind: 'line', style: 'solid', width: 1, color: 'c', data: [] },
    { name: 'b', kind: 'line', style: 'dotted', width: 1, color: 'c', data: [] },
    { name: 'c', kind: 'line', style: 'dashed', width: 1, color: 'c', data: [] },
  ]);
  assert.equal(chart.created[0]._createOpts.lineStyle, 0);
  assert.equal(chart.created[1]._createOpts.lineStyle, 1);
  assert.equal(chart.created[2]._createOpts.lineStyle, 2);
});

test('renderLine: registers series under key {instanceId}::{name} for later setData', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('tgp_btlm#1', [{ name: 'btlm_mean', kind: 'line', style: 'solid', width: 2, color: 'red', data: [{ time: 1, value: 10 }] }]);
  renderer.setData('tgp_btlm#1::btlm_mean', [{ time: 2, value: 20 }]);
  assert.deepEqual(chart.created[0]._data, [{ time: 2, value: 20 }]);
});

// ===========================================================================
// renderHistogram + per-point color データ
// ===========================================================================

test('renderHistogram: creates a histogram series per payload with per-point color data', () => {
  const { renderer, chart } = newRenderer();
  const data = [{ time: 1, value: -2, color: '#aaa' }, { time: 2, value: 3, color: '#bbb' }];
  renderer.renderHistogram('profit_adx_needle#1', [{ name: 'adx_needle', kind: 'histogram', color: 'green', data }]);
  assert.equal(chart.created.length, 1);
  assert.equal(chart.created[0]._kind, 'histogram');
  assert.deepEqual(chart.created[0]._data, data);
});

// ===========================================================================
// pane 指標（機能①②）: 専用 pane 生成 + 指標名ウォーターマーク
// ===========================================================================

test('pane indicator: creates a dedicated pane and a name watermark (機能①②)', () => {
  const { renderer, chart, lwc } = newRenderer();
  renderer.renderHistogram('rsi#1', [{ name: 'rsi', kind: 'histogram', data: [] }], { pane: true, name: 'RSI' });
  // 専用 pane が増える（pane0=ローソク + pane1=指標）。
  assert.equal(chart.panes().length, 2);
  // 系列は新 pane に載る。
  assert.equal(chart.panes()[1]._series.length, 1);
  // メイン pane は大きめの stretch factor へ。
  assert.equal(chart.panes()[0]._stretch, 3);
  // 指標名ウォーターマーク（機能②）。
  assert.equal(lwc._watermarks.length, 1);
  assert.equal(lwc._watermarks[0]._options.lines[0].text, 'RSI');
});

test('pane indicator: a second pane indicator gets its own pane (機能①)', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderHistogram('rsi#1', [{ name: 'rsi', kind: 'histogram', data: [] }], { pane: true, name: 'RSI' });
  renderer.renderLine('macd#1', [{ name: 'm', kind: 'line', data: [] }], { pane: true, name: 'MACD' });
  assert.equal(chart.panes().length, 3); // ローソク + 2 指標
});

test('crosshair move appends pane series values to the watermark (機能③)', () => {
  const { renderer, chart, lwc } = newRenderer();
  renderer.renderHistogram('rsi#1', [{ name: 'rsi', kind: 'histogram', data: [] }], { pane: true, name: 'RSI' });
  const series = chart.created[0];
  chart.fireCrosshair({ seriesData: new Map([[series, { value: 56.3 }]]) });
  const text = lwc._watermarks[0]._options.lines[0].text;
  assert.match(text, /RSI/);
  assert.match(text, /56\.3/);
});

test('crosshair move with no series data shows the name only (機能③)', () => {
  const { renderer, chart, lwc } = newRenderer();
  renderer.renderHistogram('rsi#1', [{ name: 'rsi', kind: 'histogram', data: [] }], { pane: true, name: 'RSI' });
  chart.fireCrosshair({ seriesData: new Map() });
  assert.equal(lwc._watermarks[0]._options.lines[0].text, 'RSI');
});

// ===========================================================================
// renderHorizontal: 水準線の載せ先（pane 系列 / mainSeries）
// ===========================================================================

test('renderHorizontal: attaches levels to the instance series (pane) when one exists', () => {
  const { renderer, chart, main } = newRenderer();
  renderer.renderHistogram('osc#1', [{ name: 'lc', kind: 'histogram', data: [] }], { pane: true, name: 'OSC' });
  renderer.renderHorizontal('osc#1', [{ price: 1.65, color: 'g', width: 1, style: 'dotted', text: 'up', axis_label_visible: false }]);
  assert.equal(main._priceLines.length, 0);            // mainSeries には載らない
  assert.equal(chart.created[0]._priceLines.length, 1); // pane の histogram 系列へ載る
});

function parseRgb(s) {
  const m = /rgb\((\d+),\s*(\d+),\s*(\d+)\)/.exec(s);
  return m ? { r: +m[1], g: +m[2], b: +m[3] } : null;
}

test('renderHorizontal: applies dimmed green→red scheme to pane σ levels (extreme=赤 / 中心=緑)', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderHistogram('vol#1', [{ name: 'volatility_lc', kind: 'histogram', data: [] }], { pane: true, name: 'Volatility' });
  // 0 中心の対称 σ 水準（内側 ±32 / 外側 ±160）。入力色は灰一色。
  renderer.renderHorizontal('vol#1', [
    { price: -160, color: '#545454', width: 1, style: 'dotted', text: 'dn_329', axis_label_visible: false },
    { price: -32, color: '#545454', width: 1, style: 'dotted', text: 'dn_067', axis_label_visible: false },
    { price: 32, color: '#545454', width: 1, style: 'dotted', text: 'up_067', axis_label_visible: false },
    { price: 160, color: '#545454', width: 1, style: 'dotted', text: 'up_329', axis_label_visible: false },
  ]);
  const pls = chart.created[0]._priceLines; // host = pane の histogram 系列
  const cOuter = parseRgb(pls[0].opt.color); // ±160（極）
  const cInner = parseRgb(pls[1].opt.color); // ±32（中心寄り）
  // スキーム適用（灰一色ではない）。
  assert.notEqual(pls[0].opt.color, '#545454');
  // 極は赤勝ち（r>g）、中心寄りは緑勝ち（g>r）。
  assert.ok(cOuter.r > cOuter.g, `outer should be reddish: ${pls[0].opt.color}`);
  assert.ok(cInner.g > cInner.r, `inner should be greenish: ${pls[1].opt.color}`);
  // 減光されている（端点 211/125 より暗い）。
  assert.ok(cOuter.r < 211 && cInner.g < 125);
});

test('renderHorizontal: overlay levels keep the backend color (scheme は適用しない)', () => {
  const { renderer, main } = newRenderer();
  renderer.renderHorizontal('price_range_power#1', [
    { price: 100, color: 'green', width: 2, style: 'solid', text: 'BULL', axis_label_visible: false },
  ]);
  assert.equal(main._priceLines[0].opt.color, 'green'); // backend 色を維持
});

test('renderHorizontal: creates a price line on main series for overlay-only instances', () => {
  const { renderer, main } = newRenderer();
  renderer.renderHorizontal('price_range_power#1', [
    { price: 100, color: 'green', width: 2, style: 'solid', text: 'BULL', axis_label_visible: false },
    { price: 90, color: 'red', width: 2, style: 'solid', text: 'BEAR', axis_label_visible: false },
  ]);
  assert.equal(main._priceLines.length, 2);
  assert.equal(main._priceLines[0].opt.price, 100);
  assert.equal(main._priceLines[0].opt.title, 'BULL');
  assert.equal(main._priceLines[0].opt.lineStyle, 0);
});

// ===========================================================================
// setVisible
// ===========================================================================

test('setVisible(false) hides all line series of the instance', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('tgp_btlm#1', [
    { name: 'btlm_mean', kind: 'line', style: 'solid', width: 2, color: 'red', data: [] },
    { name: 'btlm_q5', kind: 'line', style: 'dotted', width: 1, color: 'blue', data: [] },
  ]);
  renderer.setVisible('tgp_btlm#1', false);
  assert.equal(chart.created[0]._options.visible, false);
  assert.equal(chart.created[1]._options.visible, false);
});

test('setVisible(false) removes price lines and setVisible(true) re-adds them (horizontal)', () => {
  const { renderer, main } = newRenderer();
  const hlines = [{ price: 100, color: 'green', width: 2, style: 'solid', text: 'BULL', axis_label_visible: false }];
  renderer.renderHorizontal('prp#1', hlines);
  assert.equal(main._priceLines.length, 1);
  renderer.setVisible('prp#1', false);
  assert.equal(main._priceLines.length, 0);
  renderer.setVisible('prp#1', true);
  assert.equal(main._priceLines.length, 1);
});

// ===========================================================================
// remove: 系列 / 水準線 / ウォーターマーク / 専用 pane、冪等
// ===========================================================================

test('remove: removes line series and is idempotent', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('tgp_btlm#1', [{ name: 'btlm_mean', kind: 'line', style: 'solid', width: 2, color: 'red', data: [] }]);
  renderer.remove('tgp_btlm#1');
  assert.equal(chart.removed.length, 1);
  renderer.remove('tgp_btlm#1');
  assert.equal(chart.removed.length, 1);
});

test('remove: removes price lines of a horizontal instance', () => {
  const { renderer, main } = newRenderer();
  renderer.renderHorizontal('prp#1', [{ price: 100, color: 'green', width: 2, style: 'solid', text: 'BULL', axis_label_visible: false }]);
  renderer.remove('prp#1');
  assert.equal(main._priceLines.length, 0);
});

test('remove: detaches the watermark and removes the dedicated pane (機能①②④ cleanup)', () => {
  const { renderer, chart, lwc } = newRenderer();
  renderer.renderHistogram('rsi#1', [{ name: 'rsi', kind: 'histogram', data: [] }], { pane: true, name: 'RSI' });
  assert.equal(chart.panes().length, 2);
  renderer.remove('rsi#1');
  assert.equal(chart.panes().length, 1);            // 専用 pane が消える
  assert.equal(lwc._watermarks[0]._detached, true); // ウォーターマーク detach
});

test('remove+redraw survives v5 empty-pane auto-removal (regression: period 変更で消える)', () => {
  // v5 は空 pane を既定で自動削除する。preserveEmptyPane=true を付けないと、remove() の
  // removeSeries で pane が消えて index がずれ、続く removePane が candle pane(0) を誤削除する。
  // 本テストは fake が auto-removal を模した状態で、ローソク pane が常に生き残ることを固定する。
  const { renderer, chart } = newRenderer();
  renderer.renderHistogram('vol#1', [{ name: 'volatility_lc', kind: 'histogram', data: [] }], { pane: true, name: 'Volatility' });
  assert.equal(chart.panes().length, 2);
  // recompute 相当（remove → 再描画）。
  renderer.remove('vol#1');
  assert.equal(chart.panes().length, 1);  // candle pane は誤削除されず残存
  renderer.renderHistogram('vol#1', [{ name: 'volatility_lc', kind: 'histogram', data: [] }], { pane: true, name: 'Volatility' });
  assert.equal(chart.panes().length, 2);  // 指標 pane が復活（消えない）
});

// ===========================================================================
// updateLastCandle（ライブ更新・最新足の差分反映）— series.update は本所のみ呼ぶ（隔離維持）
// ===========================================================================

test('updateLastCandle: forwards the candle to mainSeries.update exactly once', () => {
  // Arrange: main 系列に update スパイを仕込む（series.update を呼ぶのは ChartRenderer だけ）。
  const chart = fakeChart();
  const main = fakeMainSeries();
  const updateCalls = [];
  main.update = (c) => updateCalls.push(c);
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: fakeLwc() });
  const candle = { time: 1277769600, open: 1.2, high: 1.6, low: 1.1, close: 1.5 };
  // Act
  renderer.updateLastCandle(candle);
  // Assert: _mainSeries.update を 1 回・当該 candle で呼ぶ。
  assert.equal(updateCalls.length, 1);
  assert.deepEqual(updateCalls[0], candle);
});
