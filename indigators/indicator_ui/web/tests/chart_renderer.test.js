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

// Fake series。setData / update / applyOptions / createPriceLine / removePriceLine を記録。
function fakeSeries(def) {
  return {
    _def: def, _data: null, _options: {}, _createOpts: null, _priceLines: [], _updates: [],
    _kind: def === HistogramSeries ? 'histogram' : (def === LineSeries ? 'line' : 'candle'),
    setData(points) { this._data = points; },
    // 末尾K差分反映: series.update を点ぶん呼ぶ（過去確定足は触らない・隔離維持）。
    update(point) { this._updates.push(point); },
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
    _options: {},
    _tsOptions: {},
    applyOptions(o) { Object.assign(this._options, o); },
    timeScale() {
      return {
        fitContent() { fitCount += 1; },
        // 座標→論理 index（リプレイスワイプの x→足 index 変換用・テストは x を 100 で割った値を返す）。
        coordinateToLogical(x) { return x == null ? null : x / 100; },
      };
    },
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

// getCandles: 基準 candles の読み取り専用アクセサ（リプレイバーの min/max・index→time 変換用）。
//   setCandles/updateLastCandle が保持する _baseCandles を露出する（新規描画・波及なし）。
test('getCandles: returns the base candles set via setCandles (read-only accessor)', () => {
  const { renderer } = newRenderer();
  const candles = [
    { time: 1, open: 1, high: 2, low: 0, close: 1.5 },
    { time: 2, open: 1.5, high: 3, low: 1, close: 2.5 },
  ];
  renderer.setCandles(candles);
  assert.deepEqual(renderer.getCandles(), candles);
});

// ===========================================================================
// 増分2: リプレイ用アクセサ（lwc 直叩き隔離）。setUserInteraction / coordinateToLogical / setCandleTrim。
//   移植元 prototype_260630-01（updateCaptureMode の handleScroll/Scale 停止・coordinateToLogical・
//   applyAsofView のローソク局所トリム）。primitive/actor から lwc を直叩きせずここに閉じる。
// ===========================================================================

test('setUserInteraction(false) disables chart scroll/scale; (true) restores them', () => {
  // Arrange
  const { renderer, chart } = newRenderer();
  // Act: OFF（スワイプ捕捉のため通常スクロール/ズームを止める）
  renderer.setUserInteraction(false);
  // Assert
  assert.equal(chart._options.handleScroll, false);
  assert.equal(chart._options.handleScale, false);
  // Act: ON（復元）
  renderer.setUserInteraction(true);
  // Assert
  assert.equal(chart._options.handleScroll, true);
  assert.equal(chart._options.handleScale, true);
});

test('coordinateToLogical delegates to chart.timeScale().coordinateToLogical', () => {
  // Arrange
  const { renderer } = newRenderer();
  // Act / Assert: fake は x/100 を返す
  assert.equal(renderer.coordinateToLogical(250), 2.5);
  assert.equal(renderer.coordinateToLogical(null), null);
});

test('setCandleTrim(time) trims candles to time<=T and setData; null restores full candles', () => {
  // Arrange
  const { renderer, main } = newRenderer();
  const candles = [
    { time: 100, open: 1, high: 2, low: 0, close: 1 },
    { time: 200, open: 1, high: 2, low: 0, close: 1 },
    { time: 300, open: 1, high: 2, low: 0, close: 1 },
  ];
  renderer.setCandles(candles);
  // Act: T=200 でトリム（100,200 の 2 本のみ）
  renderer.setCandleTrim(200);
  // Assert
  assert.equal(main._data.length, 2);
  assert.equal(main._data[main._data.length - 1].time, 200);
  // Act: null で全復元
  renderer.setCandleTrim(null);
  // Assert
  assert.deepEqual(main._data, candles);
});

test('setCandleTrim re-set is skipped when the trim position does not change (no redundant setData)', () => {
  // Arrange: setData 呼び出し回数を数える main
  const { renderer, main } = newRenderer();
  const candles = [
    { time: 100, open: 1, high: 2, low: 0, close: 1 },
    { time: 200, open: 1, high: 2, low: 0, close: 1 },
  ];
  renderer.setCandles(candles);
  let setDataCount = 0;
  const orig = main.setData.bind(main);
  main.setData = (p) => { setDataCount += 1; orig(p); };
  // Act: 同一 T を 2 回
  renderer.setCandleTrim(100);
  renderer.setCandleTrim(100);
  // Assert: 2 回目は位置不変で setData を呼ばない（プロト applyAsofView の局所トリム最適化）
  assert.equal(setDataCount, 1);
});

test('setCandleTrim(null) on a never-trimmed series does NOT call setData (OFF-path 挙動不変)', () => {
  // 回帰: replay/snapshot OFF 時に setCandleTrim(null) が冗長 setData を出さない（既存挙動を触らない）。
  // Arrange
  const { renderer, main } = newRenderer();
  const candles = [
    { time: 100, open: 1, high: 2, low: 0, close: 1 },
    { time: 200, open: 1, high: 2, low: 0, close: 1 },
  ];
  renderer.setCandles(candles);
  let setDataCount = 0;
  const orig = main.setData.bind(main);
  main.setData = (p) => { setDataCount += 1; orig(p); };
  // Act: 未トリム状態で null（トリム解除）を要求
  renderer.setCandleTrim(null);
  // Assert: series へ触れない（0 回）
  assert.equal(setDataCount, 0);
});

test('setCandleTrim is a no-op before any candles are set (no throw)', () => {
  // Arrange
  const { renderer } = newRenderer();
  // Act / Assert
  assert.doesNotThrow(() => renderer.setCandleTrim(100));
  assert.doesNotThrow(() => renderer.setCandleTrim(null));
});

test('setCandleTrim(time < first candle) keeps all candles (does NOT wipe the series)', () => {
  // 回帰: to がデータ先頭より前（縮退）のとき idx=-1 で slice(0,0)=空 setData となり
  // ローソクが全消去されるバグを禁止する（トリム無効＝series へ触れない）。
  // Arrange
  const { renderer, main } = newRenderer();
  const candles = [
    { time: 100, open: 1, high: 2, low: 0, close: 1 },
    { time: 200, open: 1, high: 2, low: 0, close: 1 },
  ];
  renderer.setCandles(candles);
  let setDataCount = 0;
  const orig = main.setData.bind(main);
  main.setData = (p) => { setDataCount += 1; orig(p); };
  // Act: 先頭足(100)より前の time を要求
  renderer.setCandleTrim(50);
  // Assert: series へ触れない（全ローソク維持）
  assert.equal(setDataCount, 0);
});

test('getCandles: returns an empty array before any candles are set (no throw)', () => {
  const { renderer } = newRenderer();
  assert.deepEqual(renderer.getCandles(), []);
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

// ===========================================================================
// クロスヘア価格読み取り欄（onCrosshairReadout）— 読み取り DTO の構築・発火。
//   DTO 形: { time, ohlc:{open,high,low,close}|null, overlays:[{name,value,color}] }。
//   DTO に series 実体・lwc 型を含めない（隔離維持）。
// ===========================================================================

// onCrosshairReadout スパイ付き renderer を組む。dtos に発火 DTO を蓄積する。
function newReadoutRenderer() {
  const chart = fakeChart();
  const main = fakeMainSeries();
  const lwc = fakeLwc();
  const dtos = [];
  const renderer = new ChartRenderer({
    chart, mainSeries: main, lwc, onCrosshairReadout: (dto) => dtos.push(dto),
  });
  return { renderer, chart, main, lwc, dtos };
}

test('crosshair readout: builds main OHLC from seriesData.get(mainSeries)', () => {
  // Arrange
  const { renderer, chart, main, dtos } = newReadoutRenderer();
  const bar = { open: 1.2, high: 1.6, low: 1.1, close: 1.5 };
  // Act: hover 中（seriesData に main がある）。
  chart.fireCrosshair({ time: 1277769600, seriesData: new Map([[main, bar]]) });
  // Assert: 最後の DTO に当該 OHLC が載る。
  assert.ok(dtos.length >= 1);
  const dto = dtos[dtos.length - 1];
  assert.equal(dto.time, 1277769600);
  assert.deepEqual(dto.ohlc, { open: 1.2, high: 1.6, low: 1.1, close: 1.5 });
});

test('crosshair readout: falls back to _lastBar when seriesData lacks the main series (hover off)', () => {
  // Arrange: setCandles で最新足が _lastBar に立つ。
  const { renderer, chart, dtos } = newReadoutRenderer();
  renderer.setCandles([
    { time: 1, open: 1, high: 2, low: 0, close: 1.5 },
    { time: 2, open: 2.0, high: 2.5, low: 1.8, close: 2.2 },
  ]);
  // Act: hover 解除（seriesData に main 無し）。
  chart.fireCrosshair({ time: undefined, seriesData: new Map() });
  // Assert: 末尾足へフォールバック。
  const dto = dtos[dtos.length - 1];
  assert.deepEqual(dto.ohlc, { open: 2.0, high: 2.5, low: 1.8, close: 2.2 });
});

test('crosshair readout: includes overlay value and color from seriesData', () => {
  // Arrange: overlay（pane 0）の line 系列を生成。
  const { renderer, chart, dtos } = newReadoutRenderer();
  renderer.renderLine('prp#1', [
    { name: 'BULL', kind: 'line', style: 'solid', width: 2, color: '#2e9e5b', data: [{ time: 1, value: 100 }] },
  ]);
  const overlaySeries = chart.created[0];
  // Act: hover 中、overlay に値がある。
  chart.fireCrosshair({ time: 1277769600, seriesData: new Map([[overlaySeries, { value: 101 }]]) });
  // Assert: overlay 行（name/value/color）が DTO に載る。
  const dto = dtos[dtos.length - 1];
  assert.deepEqual(dto.overlays, [{ name: 'BULL', value: 101, color: '#2e9e5b' }]);
});

test('crosshair readout: hidden overlay (setVisible false) is excluded; re-shown when visible — 🟡-1 regression', () => {
  // Arrange: overlay を生成。
  const { renderer, chart, dtos } = newReadoutRenderer();
  renderer.renderLine('prp#1', [
    { name: 'BULL', kind: 'line', style: 'solid', width: 2, color: '#2e9e5b', data: [{ time: 1, value: 100 }] },
  ]);
  // Act/Assert: 非表示にすると読み取り欄から除外（hover 解除＝lastValue 経路でも出ない）。
  renderer.setVisible('prp#1', false);
  chart.fireCrosshair({ time: undefined, seriesData: new Map() });
  assert.deepEqual(dtos[dtos.length - 1].overlays, []);
  // 再表示で戻る。
  renderer.setVisible('prp#1', true);
  chart.fireCrosshair({ time: undefined, seriesData: new Map() });
  assert.deepEqual(dtos[dtos.length - 1].overlays, [{ name: 'BULL', value: 100, color: '#2e9e5b' }]);
});

test('crosshair readout: overlay value falls back to last point value when seriesData lacks it', () => {
  // Arrange: 末尾点 value=100 を保持しているはず。
  const { renderer, chart, dtos } = newReadoutRenderer();
  renderer.renderLine('prp#1', [
    { name: 'BULL', kind: 'line', style: 'solid', width: 2, color: '#2e9e5b', data: [{ time: 1, value: 99 }, { time: 2, value: 100 }] },
  ]);
  // Act: hover 解除（seriesData 空）。
  chart.fireCrosshair({ time: undefined, seriesData: new Map() });
  // Assert: 保持した末尾 value=100 へフォールバック。
  const dto = dtos[dtos.length - 1];
  assert.deepEqual(dto.overlays, [{ name: 'BULL', value: 100, color: '#2e9e5b' }]);
});

test('crosshair readout: setData updates the overlay last value used for fallback', () => {
  // Arrange
  const { renderer, chart, dtos } = newReadoutRenderer();
  renderer.renderLine('prp#1', [
    { name: 'BULL', kind: 'line', style: 'solid', width: 2, color: '#2e9e5b', data: [{ time: 1, value: 100 }] },
  ]);
  // Act: 再計算で末尾点が変わる → fallback 値も更新される。
  renderer.setData('prp#1::BULL', [{ time: 2, value: 200 }]);
  chart.fireCrosshair({ time: undefined, seriesData: new Map() });
  // Assert
  const dto = dtos[dtos.length - 1];
  assert.deepEqual(dto.overlays, [{ name: 'BULL', value: 200, color: '#2e9e5b' }]);
});

test('crosshair readout: pane (non-overlay) series are not included in overlays', () => {
  // Arrange: pane 指標（pane 0 ではない）は読み取り欄の overlay 行に含めない。
  const { renderer, chart, dtos } = newReadoutRenderer();
  renderer.renderLine('rsi#1', [{ name: 'rsi', kind: 'line', style: 'solid', width: 1, color: '#fff', data: [{ time: 1, value: 55 }] }], { pane: true, name: 'RSI' });
  const paneSeries = chart.created[0];
  // Act
  chart.fireCrosshair({ time: 1277769600, seriesData: new Map([[paneSeries, { value: 56 }]]) });
  // Assert: overlays は空（pane 系列は除外）。
  const dto = dtos[dtos.length - 1];
  assert.deepEqual(dto.overlays, []);
});

test('crosshair readout: omitted onCrosshairReadout is a no-op (backward compatible, no throw)', () => {
  // Arrange: コールバック省略。
  const chart = fakeChart();
  const main = fakeMainSeries();
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: fakeLwc() });
  // Act + Assert: クロスヘア発火でクラッシュしない。
  assert.doesNotThrow(() => chart.fireCrosshair({ time: 1, seriesData: new Map([[main, { open: 1, high: 1, low: 1, close: 1 }]]) }));
});

test('crosshair readout: updateLastCandle re-fires readout DTO with the new last bar', () => {
  // Arrange
  const { renderer, main, dtos } = newReadoutRenderer();
  main.update = () => {};
  renderer.setCandles([{ time: 1, open: 1, high: 2, low: 0, close: 1.5 }]);
  const before = dtos.length;
  // Act: ライブ更新で最新足が変わる。
  renderer.updateLastCandle({ time: 2, open: 2.0, high: 2.5, low: 1.8, close: 2.2 });
  // Assert: 読み取り DTO が最新足ベースで再発火する。
  assert.ok(dtos.length > before, 'updateLastCandle should re-fire the readout');
  const dto = dtos[dtos.length - 1];
  assert.deepEqual(dto.ohlc, { open: 2.0, high: 2.5, low: 1.8, close: 2.2 });
});

test('crosshair readout: existing watermark logic still updates (機能③ backward compat)', () => {
  // Arrange: pane 指標を作り、読み取りコールバックも注入する。
  const { renderer, chart, lwc } = newReadoutRenderer();
  renderer.renderHistogram('rsi#1', [{ name: 'rsi', kind: 'histogram', data: [] }], { pane: true, name: 'RSI' });
  const series = chart.created[0];
  // Act
  chart.fireCrosshair({ seriesData: new Map([[series, { value: 56.3 }]]) });
  // Assert: 既存 watermark 更新（機能③）が不変に動く。
  const text = lwc._watermarks[0]._options.lines[0].text;
  assert.match(text, /RSI/);
  assert.match(text, /56\.3/);
});

// ===========================================================================
// updateSeriesTail（Latest 末尾K差分反映）— series.update を points ぶん呼ぶ。
//   過去確定足は触らない（setData で全置換しない）。series.update を呼ぶのは本所のみ。
// ===========================================================================
test('updateSeriesTail: calls series.update once per point and does not touch past points', () => {
  // Arrange: line 系列を 1 本生成（初期 setData で過去確定足を置く）。
  const { renderer, chart } = newRenderer();
  const init = [{ time: 1, value: 10 }, { time: 2, value: 11 }, { time: 3, value: 12 }];
  renderer.renderLine('ma#1', [{ name: 'MA', kind: 'line', style: 'solid', width: 1, color: 'blue', data: init }]);
  const series = chart.created[0];
  // Act: 末尾 K=2 点を差分反映する。
  const tail = [{ time: 3, value: 12.5 }, { time: 4, value: 13 }];
  renderer.updateSeriesTail('ma#1::MA', tail);
  // Assert: series.update を 2 回（points ぶん）呼ぶ。setData による全置換はしない。
  assert.deepEqual(series._updates, tail);
  assert.deepEqual(series._data, init); // 過去 setData は不変（全置換していない）
});

test('updateSeriesTail: unknown seriesKey is a no-op (does not throw)', () => {
  const { renderer, chart } = newRenderer();
  renderer.renderLine('ma#1', [{ name: 'MA', kind: 'line', data: [{ time: 1, value: 10 }] }]);
  const series = chart.created[0];
  renderer.updateSeriesTail('missing::key', [{ time: 2, value: 11 }]);
  assert.deepEqual(series._updates, []); // 触らない
});

test('updateSeriesTail: updates overlay readout lastValue to the tail last point', () => {
  // Arrange: overlay(line・pane 0)系列を生成し読み取り fallback の lastValue を持たせる。
  const { renderer, chart } = newRenderer();
  renderer.renderLine('ma#1', [{ name: 'MA', kind: 'line', data: [{ time: 1, value: 10 }] }]);
  // Act
  renderer.updateSeriesTail('ma#1::MA', [{ time: 2, value: 99 }]);
  // Assert: overlay 読み取りメタの lastValue が末尾点に更新される（_overlayReadouts 経由）。
  const meta = renderer._overlayReadouts.get('ma#1::MA');
  assert.equal(meta.lastValue, 99);
  void chart;
});

// ===========================================================================
// v6（§12）: 基準 candles 所有・observer 通知・per-bar 減光描画/復元
// 設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §12（ローソク足のみ減光・背景不変・per-bar 着色）、
//   フェーズ2 確定機構（基準 candles は ChartRenderer 所有・ChartRenderer 起点 observer 通知）。
//   構造: Arrange-Act-Assert。mainSeries.setData の記録（fakeSeries._data）で per-bar 色上書きを観測。
//   canvas 実描画・実ピクセル（背景不変・極暗色の見た目）はブラウザ確認に委譲（node:test 範囲外）。
// ===========================================================================

const V6_CANDLES = [
  { time: 10, open: 1, high: 2, low: 0, close: 1.5 },
  { time: 20, open: 2, high: 3, low: 1, close: 2.5 },
  { time: 30, open: 3, high: 4, low: 2, close: 3.5 },
  { time: 40, open: 4, high: 5, low: 3, close: 4.5 },
  { time: 50, open: 5, high: 6, low: 4, close: 5.5 },
];

test('v6: setCandles notifies the injected candle observer (ChartRenderer 起点同期)', () => {
  // Arrange: observer を注入。
  const chart = fakeChart();
  const main = fakeMainSeries();
  const lwc = fakeLwc();
  let notified = 0;
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc, onCandlesChanged: () => { notified += 1; } });
  // Act
  renderer.setCandles(V6_CANDLES);
  // Assert: setCandles で observer が 1 回通知される（候補同期の起点）。
  assert.equal(notified, 1);
});

test('v6: updateLastCandle notifies the injected candle observer', () => {
  // Arrange
  const chart = fakeChart();
  const main = fakeMainSeries();
  const lwc = fakeLwc();
  let notified = 0;
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc, onCandlesChanged: () => { notified += 1; } });
  renderer.setCandles(V6_CANDLES); // 1
  // Act
  renderer.updateLastCandle({ time: 50, open: 5, high: 7, low: 4, close: 6 }); // 2
  // Assert: live 差分更新でも observer が通知される（全経路同期）。
  assert.equal(notified, 2);
});

test('v6: setCandles without an observer is a no-op notify (backward compatible)', () => {
  // Arrange + Act + Assert: observer 未注入でも throw しない（後方互換）。
  const { renderer, main } = newRenderer();
  assert.doesNotThrow(() => renderer.setCandles(V6_CANDLES));
  assert.deepEqual(main._data, V6_CANDLES);
});

test('v6: dimCandlesOutsidePair overrides per-bar color outside [from,to] and keeps inside original', () => {
  // Arrange: 基準 candles を保持させる。
  const { renderer, main } = newRenderer();
  renderer.setCandles(V6_CANDLES);
  // Act: ペア [20,40] の外（time=10,50）を減光、内（20,30,40）は原色維持。
  renderer.dimCandlesOutsidePair({ from: 20, to: 40 });
  // Assert: mainSeries.setData が dim 配列で呼ばれる。
  const data = main._data;
  assert.equal(data.length, 5);
  const inside = data.filter((b) => b.time >= 20 && b.time <= 40);
  const outside = data.filter((b) => b.time < 20 || b.time > 40);
  // 外: per-bar color/borderColor/wickColor が極暗色で上書きされる（同一の暗色）。
  for (const b of outside) {
    assert.ok(b.color, '外バーは color を持つ（減光）');
    assert.equal(b.color, b.borderColor);
    assert.equal(b.color, b.wickColor);
  }
  // 内: per-bar 色上書きを持たない（原色＝既定 up/down 着色に委ねる）。
  for (const b of inside) {
    assert.equal(b.color, undefined);
    assert.equal(b.borderColor, undefined);
    assert.equal(b.wickColor, undefined);
  }
});

test('v6: dimCandlesOutsidePair preserves OHLC values of every bar (only color is added)', () => {
  // Arrange
  const { renderer, main } = newRenderer();
  renderer.setCandles(V6_CANDLES);
  // Act
  renderer.dimCandlesOutsidePair({ from: 20, to: 40 });
  // Assert: time/open/high/low/close は基準と完全一致（データ改変なし＝§12 背景/データ不変則）。
  const data = main._data;
  for (let i = 0; i < V6_CANDLES.length; i += 1) {
    assert.equal(data[i].time, V6_CANDLES[i].time);
    assert.equal(data[i].open, V6_CANDLES[i].open);
    assert.equal(data[i].high, V6_CANDLES[i].high);
    assert.equal(data[i].low, V6_CANDLES[i].low);
    assert.equal(data[i].close, V6_CANDLES[i].close);
  }
});

test('v6: dimming does not mutate the stored base candles (restore yields originals)', () => {
  // Arrange
  const { renderer, main } = newRenderer();
  renderer.setCandles(V6_CANDLES);
  // Act: 減光 → 復元。
  renderer.dimCandlesOutsidePair({ from: 20, to: 40 });
  renderer.restoreCandles();
  // Assert: 復元後は基準 candles（色上書きなし）が setData される。
  assert.deepEqual(main._data, V6_CANDLES);
});

test('v6: restoreCandles re-applies the base candles to the main series', () => {
  // Arrange
  const { renderer, main } = newRenderer();
  renderer.setCandles(V6_CANDLES);
  renderer.dimCandlesOutsidePair({ from: 20, to: 40 });
  // Act
  renderer.restoreCandles();
  // Assert: 基準復元（色なし）。
  assert.deepEqual(main._data, V6_CANDLES);
});

test('v6: dimCandlesOutsidePair is a safe no-op when no base candles were set', () => {
  // Arrange: setCandles を呼ばない（基準未供給）。
  const { renderer, main } = newRenderer();
  const before = main._data;
  // Act / Assert: throw せず mainSeries.setData も呼ばない（フォールバック・候補据え置き）。
  assert.doesNotThrow(() => renderer.dimCandlesOutsidePair({ from: 20, to: 40 }));
  assert.equal(main._data, before); // 触らない（null のまま）
});

test('v6: restoreCandles is a safe no-op when no base candles were set', () => {
  // Arrange
  const { renderer, main } = newRenderer();
  const before = main._data;
  // Act / Assert
  assert.doesNotThrow(() => renderer.restoreCandles());
  assert.equal(main._data, before);
});

test('v6: setCandleObserver installs the observer after construction (late binding)', () => {
  // Arrange: 構築後に observer を据える（composition root の生成順序差を吸収）。
  const { renderer } = newRenderer();
  let notified = 0;
  renderer.setCandleObserver(() => { notified += 1; });
  // Act
  renderer.setCandles(V6_CANDLES);
  // Assert
  assert.equal(notified, 1);
});
