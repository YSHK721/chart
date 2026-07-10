// chart_renderer.js（ChartRendererPort 実装・upstream 隔離点）の仕様検証。
//
// 設計入力: 内部設計書 §3.3.4（renderLine/renderHistogram/renderHorizontal/setData/setVisible/remove）、
//   §7.1.2（系列キー {instanceId}::{name}・隠蔽契約）、§3.3.6（lineStyle 文字列→整数）。
// upstream JS API（v5: addSeries/addPane/panes/removePane/createTextWatermark/...）はテストでは
//   Fake chart/lwc を注入して観測する。構造: Arrange-Act-Assert（AAA）。DOM 非依存。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartRenderer, zoomedPriceRange, clampPriceRange } from '../js/adapter/front/chart_renderer.js';

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

// pixelsPerBar（スワイプ感度基準）: |logicalToCoordinate(1)-logicalToCoordinate(0)|、極小(<0.5)は 8 下限。
//   最小 fake chart で timeScale().logicalToCoordinate を差し替えて検証する。
function rendererWithSpacing(spacingPx) {
  const main = { setData() {}, priceScale: () => ({ applyOptions() {} }) };
  const chart = {
    addSeries: () => main,
    subscribeCrosshairMove() {},
    timeScale: () => ({
      logicalToCoordinate: spacingPx == null ? undefined : ((i) => (i == null ? null : i * spacingPx)),
    }),
  };
  return new ChartRenderer({ chart, mainSeries: main, lwc: {} });
}

test('pixelsPerBar: 通常は barSpacing（|logicalToCoordinate(1)-(0)|）を返す', () => {
  assert.equal(rendererWithSpacing(12).pixelsPerBar(), 12);
});

test('pixelsPerBar: barSpacing が極小(<0.5px)なら 8 を下限に使う（ズームアウト時の暴走防止・プロト準拠）', () => {
  assert.equal(rendererWithSpacing(0.3).pixelsPerBar(), 8, '0.3px/bar → 8 下限');
});

test('pixelsPerBar: logicalToCoordinate 非提供なら 8（後方互換）', () => {
  assert.equal(rendererWithSpacing(null).pixelsPerBar(), 8);
});

test('focusRecentBars(n): 直近 n バーを可視範囲にする（sessions の初期ズーム）', () => {
  const ranges = [];
  const main = { setData() {}, applyOptions() {}, priceScale: () => ({ applyOptions() {} }) };
  const ts = {
    fitContent() {}, applyOptions() {}, setVisibleLogicalRange: (r) => ranges.push(r),
  };
  const chart = { addSeries: () => main, subscribeCrosshairMove() {}, timeScale: () => ts };
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: {} });
  const candles = Array.from({ length: 100 }, (_, i) => ({ time: i + 1, open: 1, high: 2, low: 0, close: 1 }));
  renderer.setCandles(candles); // total=100
  ranges.length = 0;
  renderer.focusRecentBars(20);
  const r = ranges.at(-1);
  // from = 100-20-0.5 = 79.5、to = 100-0.5 + max(1,20*0.04)=99.5+1=100.5。
  assert.ok(Math.abs(r.from - 79.5) < 1e-9, 'from=total-n-0.5');
  assert.ok(Math.abs(r.to - 100.5) < 1e-9, 'to=total-0.5+右余白');
});

test('focusTimeRange(from,to): 時間ベースで可視範囲を設定する（日別プロファイルの全tf対応）', () => {
  const ranges = [];
  const main = { setData() {}, applyOptions() {}, priceScale: () => ({ applyOptions() {} }) };
  const ts = { fitContent() {}, applyOptions() {}, setVisibleRange: (r) => ranges.push(r) };
  const chart = { addSeries: () => main, subscribeCrosshairMove() {}, timeScale: () => ts };
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: {} });
  renderer.focusTimeRange(1000, 2000);
  assert.deepEqual(ranges.at(-1), { from: 1000, to: 2000 }, 'setVisibleRange({from,to}) 時間ベース');
  // 不正レンジ（from>=to / 非有限）は触らない。
  ranges.length = 0;
  renderer.focusTimeRange(2000, 1000);
  renderer.focusTimeRange(NaN, 100);
  assert.equal(ranges.length, 0, '不正レンジは setVisibleRange を呼ばない');
});

// _fitTrimView（スクラブ追従）: 現在の可視幅 span を保ったまま T を右端へスクロールする。
//   ズームを保持したまま過去↔現在を移動できる（ユーザー選択・プロトの全historyフィットから意図的に外れる）。
function rendererWithVisibleRange(visSpan) {
  const ranges = [];
  const main = {
    setData() {}, applyOptions() {},
    priceScale: () => ({ applyOptions() {} }),
  };
  const ts = {
    fitContent() {}, applyOptions() {},
    width: () => 1000, options: () => ({ barSpacing: 6 }),
    getVisibleLogicalRange: () => (visSpan == null ? null : { from: 0, to: visSpan }),
    setVisibleLogicalRange: (r) => ranges.push(r),
  };
  const chart = { addSeries: () => main, subscribeCrosshairMove() {}, timeScale: () => ts };
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: {} });
  return { renderer, ranges };
}

test('_fitTrimView（setCandleTrim経由）: 可視幅 span を保持し T を右端(右fを余白)へ置く＝ズーム保持追従', () => {
  // Arrange: 20 本、margin f=0.30、現在の可視幅 span=10（ズームイン相当）。
  const { renderer, ranges } = rendererWithVisibleRange(10);
  const candles = Array.from({ length: 20 }, (_, i) => ({ time: i + 1, open: 1, high: 2, low: 0, close: 1 }));
  renderer.setCandles(candles);
  renderer.setRightMarginFraction(0.30); // f=0.30
  // Act: T=足10（time=10）までトリム → L=10、lastIdx=9.5。
  renderer.setCandleTrim(10);
  // Assert: span=10 を保持、to=lastIdx + span*f = 9.5+3=12.5、from=to-span=2.5。
  const r = ranges.at(-1);
  assert.ok(Math.abs((r.to - r.from) - 10) < 1e-9, 'span=10 を保持（ズーム維持）');
  assert.ok(Math.abs(r.to - 12.5) < 1e-9, 'to = 9.5 + span*0.30');
  assert.ok(Math.abs(r.from - 2.5) < 1e-9, 'from = to - span');
});

test('_fitTrimView: getVisibleLogicalRange が一時的に null でも直前 span を保持（全historyへ戻さない）', () => {
  // 回帰: 一度得た span をキャッシュし、次に null を返しても全history フィットへスナップしない
  //   （ユーザー報告「拡大表示して移動させると全history表示に戻る」の確実化）。
  const ranges = [];
  let call = 0;
  const main = { setData() {}, applyOptions() {}, priceScale: () => ({ applyOptions() {} }) };
  const ts = {
    fitContent() {}, applyOptions() {}, width: () => 1000, options: () => ({ barSpacing: 6 }),
    // 1回目は span=10、2回目以降は null（一時的な取得失敗を模す）。
    getVisibleLogicalRange: () => { call += 1; return call === 1 ? { from: 0, to: 10 } : null; },
    setVisibleLogicalRange: (r) => ranges.push(r),
  };
  const chart = { addSeries: () => main, subscribeCrosshairMove() {}, timeScale: () => ts };
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: {} });
  const candles = Array.from({ length: 20 }, (_, i) => ({ time: i + 1, open: 1, high: 2, low: 0, close: 1 }));
  renderer.setCandles(candles);
  renderer.setRightMarginFraction(0.30);
  // 1回目トリム（span=10 取得）→ 2回目トリム（null だがキャッシュ span=10 を使う）。
  renderer.setCandleTrim(10); // L=10, span=10
  renderer.setCandleTrim(8);  // L=8, getVisibleLogicalRange=null → cached span=10
  const r = ranges.at(-1);
  assert.ok(Math.abs((r.to - r.from) - 10) < 1e-9, 'span=10 を保持（全history=20 に戻らない）');
  // to = (8-0.5) + 10*0.30 = 7.5+3 = 10.5
  assert.ok(Math.abs(r.to - 10.5) < 1e-9, 'キャッシュ span で T 追従');
});

test('_fitTrimView: getVisibleLogicalRange 非提供なら全historyフィット {from:-0.5, to:L-0.5+blank} にフォールバック', () => {
  // Arrange: visSpan=null（取得不能）。f=0.30、L=10 → blank=10*0.3/0.7≈4.2857。
  const { renderer, ranges } = rendererWithVisibleRange(null);
  const candles = Array.from({ length: 20 }, (_, i) => ({ time: i + 1, open: 1, high: 2, low: 0, close: 1 }));
  renderer.setCandles(candles);
  renderer.setRightMarginFraction(0.30);
  renderer.setCandleTrim(10);
  const r = ranges.at(-1);
  assert.ok(Math.abs(r.from - (-0.5)) < 1e-9, 'from=-0.5（先頭）');
  assert.ok(Math.abs(r.to - (9.5 + (10 * 0.3) / 0.7)) < 1e-6, 'to=L-0.5+blank');
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

test('updateLastCandle: スナップショット(トリム)中は series へ現在足を入れない（不可解なバグ修正）', () => {
  // 回帰: トリム系列（過去 T まで）へライブの現在足を append すると範囲外にバーが出る。
  //   トリム中は _mainSeries.update を呼ばず、基準 _baseCandles のマージのみ行う。
  const chart = fakeChart();
  const main = fakeMainSeries();
  const updateCalls = [];
  main.update = (c) => updateCalls.push(c);
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: fakeLwc() });
  // 基準 candles を入れ、T=足2 までトリム（_lastTrimIdx != null）。
  renderer.setCandles([
    { time: 1, open: 1, high: 2, low: 0, close: 1 },
    { time: 2, open: 1, high: 2, low: 0, close: 1 },
    { time: 3, open: 1, high: 2, low: 0, close: 1 },
  ]);
  renderer.setCandleTrim(2); // トリム中。
  updateCalls.length = 0;
  // Act: ライブの現在足（time=99・現在価格）を反映。
  renderer.updateLastCandle({ time: 99, open: 690, high: 692, low: 688, close: 691 });
  // Assert: series.update は呼ばれない（トリム系列へ append しない）。基準へはマージされる。
  assert.equal(updateCalls.length, 0, 'トリム中は series へ現在足を入れない');
  assert.equal(renderer.getCandles().at(-1).time, 99, '基準 _baseCandles には反映（トリム解除後に復帰）');
  // トリム解除後は通常どおり series.update する。
  renderer.setCandleTrim(null);
  updateCalls.length = 0;
  renderer.updateLastCandle({ time: 100, open: 1, high: 2, low: 0, close: 1 });
  assert.equal(updateCalls.length, 1, 'トリム解除後はライブ更新を series へ反映');
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

test('crosshair readout: setSessionMP 供給時は time で当日 MP を DTO に載せる（sessions）', () => {
  const { renderer, chart, main, dtos } = newReadoutRenderer();
  const mp = { poc: 101, vah: 106, val: 98 };
  renderer.setSessionMP(new Map([[1277769600, mp]]));
  // 当日を指す（time 一致）→ DTO.sessionMP に当該 MP。
  chart.fireCrosshair({ time: 1277769600, seriesData: new Map([[main, { open: 1, high: 2, low: 0, close: 1 }]]) });
  assert.deepEqual(dtos.at(-1).sessionMP, mp);
  // 別 time（未登録）→ sessionMP は null。
  chart.fireCrosshair({ time: 999, seriesData: new Map([[main, { open: 1, high: 2, low: 0, close: 1 }]]) });
  assert.equal(dtos.at(-1).sessionMP, null);
  // setSessionMP(null) で解除 → null。
  renderer.setSessionMP(null);
  chart.fireCrosshair({ time: 1277769600, seriesData: new Map([[main, { open: 1, high: 2, low: 0, close: 1 }]]) });
  assert.equal(dtos.at(-1).sessionMP, null);
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

// ---------------------------------------------------------------------------
// スナップショットの読み取り欄整合（実機バグ修正の回帰）:
//   setCandleTrim(T) はトリム後の最終足（=T 時点の足）を読み取り欄の単一源（_lastBar）へ反映し
//   即時再発火する。解除（null）で全ローソクの最終足へ復元する。
//   これが無いとスナップショット中も左上読み取り欄がトリム前の最新足を表示し続ける（当時表示と矛盾）。
// ---------------------------------------------------------------------------
test('setCandleTrim(T) updates the readout last-bar to the trimmed bar and re-emits', () => {
  // Arrange: readout DTO を記録
  const chart = fakeChart();
  const main = fakeMainSeries();
  const readouts = [];
  const renderer = new ChartRenderer({
    chart, mainSeries: main, lwc: fakeLwc(),
    onCrosshairReadout: (dto) => readouts.push(dto),
  });
  const candles = [
    { time: 100, open: 1, high: 2, low: 0, close: 1.1 },
    { time: 200, open: 2, high: 3, low: 1, close: 2.2 },
    { time: 300, open: 3, high: 4, low: 2, close: 3.3 },
  ];
  renderer.setCandles(candles);
  readouts.length = 0;
  // Act: T=200 でトリム
  renderer.setCandleTrim(200);
  // Assert: 読み取り欄がトリム後の最終足（time=200・close=2.2）で再発火される
  assert.ok(readouts.length >= 1, 'トリムで読み取り欄が再発火される');
  const dto = readouts.at(-1);
  assert.equal(dto.time, 200);
  assert.equal(dto.ohlc.close, 2.2);
  // Act: 解除（null）→ 全ローソクの最終足（time=300）へ復元
  readouts.length = 0;
  renderer.setCandleTrim(null);
  assert.ok(readouts.length >= 1, '解除でも読み取り欄が再発火される');
  assert.equal(readouts.at(-1).time, 300);
  assert.equal(readouts.at(-1).ohlc.close, 3.3);
});

// ---------------------------------------------------------------------------
// MP プロファイル専用の右マージン（試作 PROFILE_FRAC 移植・バーとローソクの重なり回避）:
//   setRightMarginFraction(frac) → timeScale.applyOptions({rightOffset: width*frac/barSpacing}) ／
//   null → rightOffset:0（復元）。
// ---------------------------------------------------------------------------
test('setRightMarginFraction: sets rightOffset bars from width*frac/barSpacing and restores on null', () => {
  // Arrange: timeScale の width/options/applyOptions を fake
  const { renderer, chart } = newRenderer();
  const applied = [];
  chart.timeScale = () => ({
    width: () => 1200,
    options: () => ({ barSpacing: 6 }),
    applyOptions: (o) => applied.push(o),
  });
  // Act: frac=0.30 → 1200*0.30/6 = 60 bars
  renderer.setRightMarginFraction(0.30);
  assert.deepEqual(applied.at(-1), { rightOffset: 60 });
  // Act: null → 復元（rightOffset: 0）
  renderer.setRightMarginFraction(null);
  assert.deepEqual(applied.at(-1), { rightOffset: 0 });
});

// ===========================================================================
// zoomedPriceRange（純関数・価格軸ホイールズームの中核式）
//   f = 0.9^(-deltaY/100) を [0.5, 2] にクランプ。カーソル価格 p を中心に
//   newMin = p-(p-min)*f / newMax = p+(max-p)*f。span 最小クランプ・p 範囲外は中央基準。
// ===========================================================================

test('zoomedPriceRange: 1ノッチ上(deltaY=-100)でレンジ×0.90にズームイン（カーソル中心）', () => {
  // Arrange: p=100 を中心（min=0..max=200 の中央）→ 縮小は左右対称
  const out = zoomedPriceRange({ min: 0, max: 200 }, 100, -100);
  // Act/Assert: f=0.9 → newMin=100-100*0.9=10, newMax=100+100*0.9=190（span 200→180=×0.90）
  assert.ok(Math.abs(out.min - 10) < 1e-9);
  assert.ok(Math.abs(out.max - 190) < 1e-9);
});

test('zoomedPriceRange: カーソルが上寄りなら上側の縮み幅が小さい（p の位置に比例）', () => {
  // Arrange: p=150（min=0..max=200 の上寄り）
  const out = zoomedPriceRange({ min: 0, max: 200 }, 150, -100);
  // Act/Assert: newMin=150-150*0.9=15, newMax=150+50*0.9=195。下側の縮み(15) > 上側の縮み(5)
  assert.ok(Math.abs(out.min - 15) < 1e-9);
  assert.ok(Math.abs(out.max - 195) < 1e-9);
  assert.ok((out.min - 0) > (200 - out.max), '下側(カーソルから遠い)ほど大きく縮む');
});

test('zoomedPriceRange: 1ノッチ下(deltaY=+100)でレンジ×1/0.90にズームアウト', () => {
  // Arrange/Act: f=0.9^(-1)=1/0.9≈1.1111
  const out = zoomedPriceRange({ min: 0, max: 200 }, 100, 100);
  const f = 1 / 0.9;
  // Assert: newMin=100-100*f, newMax=100+100*f（span 拡大）
  assert.ok(Math.abs(out.min - (100 - 100 * f)) < 1e-9);
  assert.ok(Math.abs(out.max - (100 + 100 * f)) < 1e-9);
  assert.ok((out.max - out.min) > 200, 'ズームアウトで span が拡大する');
});

test('zoomedPriceRange: 微小デルタは比例した連続ズーム（トラックパッド）', () => {
  // Arrange: deltaY=-10 → f=0.9^(0.1)≈0.98952（微小縮小）
  const out = zoomedPriceRange({ min: 0, max: 200 }, 100, -10);
  const f = Math.pow(0.9, 0.1);
  assert.ok(Math.abs(out.min - (100 - 100 * f)) < 1e-9);
  assert.ok(Math.abs(out.max - (100 + 100 * f)) < 1e-9);
  // 微小変化＝1ノッチ(0.9)より1に近い
  assert.ok(f > 0.9 && f < 1);
});

test('zoomedPriceRange: f は下限 0.5 にクランプ（過大な負デルタでも暴れない）', () => {
  // Arrange: deltaY=-1000 → 0.9^10≈0.3487 < 0.5 → 0.5 にクランプ
  const out = zoomedPriceRange({ min: 0, max: 200 }, 100, -1000);
  // Assert: f=0.5 → newMin=100-100*0.5=50, newMax=100+100*0.5=150
  assert.ok(Math.abs(out.min - 50) < 1e-9);
  assert.ok(Math.abs(out.max - 150) < 1e-9);
});

test('zoomedPriceRange: f は上限 2 にクランプ（過大な正デルタでも暴れない）', () => {
  // Arrange: deltaY=+1000 → 0.9^-10≈2.867 > 2 → 2 にクランプ
  const out = zoomedPriceRange({ min: 0, max: 200 }, 100, 1000);
  // Assert: f=2 → newMin=100-100*2=-100, newMax=100+100*2=300
  assert.ok(Math.abs(out.min - (-100)) < 1e-9);
  assert.ok(Math.abs(out.max - 300) < 1e-9);
});

test('zoomedPriceRange: span を現 span×1e-4 以下に縮めない（最小 span クランプ）', () => {
  // Arrange: 既に極小 span を更にズームインしても最小 span を割らない
  const range = { min: 100, max: 100.0001 }; // span=1e-4
  // deltaY=-100 → f=0.9 で span=9e-5 < 1e-4*1=1e-4 相当。最小 span = span_current*1e-4
  const out = zoomedPriceRange(range, 100.00005, -100);
  const minSpan = (range.max - range.min) * 1e-4;
  assert.ok((out.max - out.min) >= minSpan - 1e-18, 'span は最小クランプ以上を維持する');
});

test('zoomedPriceRange: price が range 外なら中央基準でズームする', () => {
  // Arrange: p=1000（min=0..max=200 の外側）→ 中央 100 を基準に
  const out = zoomedPriceRange({ min: 0, max: 200 }, 1000, -100);
  // Assert: center=100, f=0.9 → newMin=100-100*0.9=10, newMax=100+100*0.9=190
  assert.ok(Math.abs(out.min - 10) < 1e-9);
  assert.ok(Math.abs(out.max - 190) < 1e-9);
});

test('zoomedPriceRange: deltaY が NaN/0 は無変化（NaN 伝播防止・レビュー🔵）', () => {
  assert.deepEqual(zoomedPriceRange({ min: 0, max: 200 }, 100, NaN), { min: 0, max: 200 });
  assert.deepEqual(zoomedPriceRange({ min: 0, max: 200 }, 100, 0), { min: 0, max: 200 });
});

test('setCandles: 手動スケール（ズーム）を破棄しない（解除はユーザーの dblclick のみ・足リビール回帰）', () => {
  // Arrange: 価格軸ズームで手動スケール（autoScale=OFF＋レンジ）を立てる。
  const { chart, mainSeries, psState } = fakeZoomChart(360);
  const renderer = new ChartRenderer({ chart, mainSeries, lwc: fakeLwc() });
  renderer.setPaneHeight(360);
  renderer.handlePriceWheel(610, 180, -100); // 軸領域でズーム → setVisibleRange（autoScale=OFF）
  const zoomedRange = { ...psState.range };
  assert.equal(psState.autoScale, false, 'ズームで手動スケールになる');
  // Act: 全置換（時間足切替・リプレイの足リビールの両方がこの経路）。
  renderer.setCandles([{ time: 1, open: 1, high: 2, low: 0, close: 1.5 }]);
  // Assert: 手動スケールは維持される（システムは勝手に解除しない＝解除点は dblclick のみ）。
  //   旧実装はここで override を破棄し、setCandles を毎バー呼ぶ replay_ui の足リビールで
  //   バー境界のたびにホイールズームだけが消えていた（本テストはその再発を禁止する回帰）。
  assert.equal(psState.autoScale, false, 'setCandles 後も手動スケールのまま');
  assert.deepEqual(psState.range, zoomedRange, 'setCandles 後もズームレンジ不変');
});

// ===========================================================================
// handlePriceWheel / resetPriceZoom / panPriceByPixels（価格軸ホイールズームの renderer 配線）
//   lwc v5.2 の priceScale ネイティブ API（getVisibleRange/setVisibleRange/autoScale）で実装する。
//   setVisibleRange は autoScale=false を設定する＝軸ドラッグと同一の内部状態（lwc 実装準拠）。
// ===========================================================================

// 価格軸ズーム用の Fake chart。priceScale はネイティブ API（getVisibleRange/setVisibleRange/
//   options/applyOptions）を状態付きで模す。setVisibleRange が autoScale=false を設定する点も
//   実 lwc（v5.2 bundle: setVisibleRange(t){this.setAutoScale(!1);...}）に合わせる。
function fakeZoomChart(paneHeight = 400) {
  const psState = { autoScale: true, range: { from: 0, to: 200 } };
  const priceScale = {
    getVisibleRange: () => ({ ...psState.range }),
    setVisibleRange(r) {
      psState.autoScale = false; // 実 lwc と同じ: 手動レンジ設定は自動スケール解除を伴う。
      psState.range = { from: r.from, to: r.to };
    },
    setAutoScale(on) { psState.autoScale = !!on; },
    applyOptions(o) { if (o && o.autoScale !== undefined) psState.autoScale = !!o.autoScale; },
    options: () => ({ autoScale: psState.autoScale }),
  };
  const mainSeries = {
    _def: undefined, setData() {}, update() {}, applyOptions() {},
    _priceRange: { min: 0, max: 200 },
    // 座標→価格: y=0 で max、y=paneHeight で min（lwc は上が高値）。
    coordinateToPrice(y) {
      if (y == null) return null;
      const t = y / paneHeight;
      return this._priceRange.max - t * (this._priceRange.max - this._priceRange.min);
    },
    priceScale: () => priceScale,
  };
  const chart = {
    addSeries: () => mainSeries,
    removeSeries() {},
    panes: () => [{ setStretchFactor() {}, paneIndex: () => 0 }],
    addPane: () => ({ addSeries: () => mainSeries, setStretchFactor() {}, paneIndex: () => 1, setPreserveEmptyPane() {} }),
    removePane() {},
    applyOptions() {},
    timeScale: () => ({ width: () => 600, height: () => 40, fitContent() {} }),
    subscribeCrosshairMove() {},
  };
  return { chart, mainSeries, priceScale, psState };
}

test('handlePriceWheel: 軸領域内(x>=timeScale width)で処理し true・setVisibleRange で新レンジ（autoScale=OFF）', () => {
  // Arrange
  const { chart, mainSeries, psState } = fakeZoomChart(360);
  const renderer = new ChartRenderer({ chart, mainSeries, lwc: fakeLwc() });
  renderer.setPaneHeight(360);
  // Act: x=610（>=600=軸領域）, y=180（中央→p=100）, deltaY=-100（ズームイン）
  const handled = renderer.handlePriceWheel(610, 180, -100);
  // Assert: 処理され、レンジは f=0.9・p=100 中心で 0..200 → 10..190、手動スケールになる。
  assert.equal(handled, true);
  assert.ok(Math.abs(psState.range.from - 10) < 1e-6);
  assert.ok(Math.abs(psState.range.to - 190) < 1e-6);
  assert.equal(psState.autoScale, false);
  assert.equal(renderer.isPriceZoomed(), true);
});

test('handlePriceWheel: 軸領域外(x<timeScale width)は false で何もしない（時間軸ズームを奪わない）', () => {
  // Arrange
  const { chart, mainSeries, psState } = fakeZoomChart(360);
  const renderer = new ChartRenderer({ chart, mainSeries, lwc: fakeLwc() });
  renderer.setPaneHeight(360);
  // Act: x=300（<600=チャート本体）
  const handled = renderer.handlePriceWheel(300, 180, -100);
  // Assert: 未処理・自動スケールのまま・レンジ不変。
  assert.equal(handled, false);
  assert.equal(psState.autoScale, true);
  assert.deepEqual(psState.range, { from: 0, to: 200 });
});

test('handlePriceWheel: getVisibleRange が null（データ無し）なら false で何もしない', () => {
  const { chart, mainSeries, priceScale, psState } = fakeZoomChart(360);
  priceScale.getVisibleRange = () => null;
  const renderer = new ChartRenderer({ chart, mainSeries, lwc: fakeLwc() });
  renderer.setPaneHeight(360);
  assert.equal(renderer.handlePriceWheel(610, 180, -100), false);
  assert.equal(psState.autoScale, true);
});

test('handlePriceWheel: priceScale がネイティブ API 非提供なら false（後方互換・安全側）', () => {
  const { chart, mainSeries } = fakeZoomChart(360);
  mainSeries.priceScale = () => ({ applyOptions() {} }); // getVisibleRange/setVisibleRange 無し
  const renderer = new ChartRenderer({ chart, mainSeries, lwc: fakeLwc() });
  renderer.setPaneHeight(360);
  assert.equal(renderer.handlePriceWheel(610, 180, -100), false);
});

test('resetPriceZoom: autoScale=true へ復帰する（手動スケールの唯一の解除点）', () => {
  // Arrange: 一旦ズームして手動スケールにする。
  const { chart, mainSeries, psState } = fakeZoomChart(360);
  const renderer = new ChartRenderer({ chart, mainSeries, lwc: fakeLwc() });
  renderer.setPaneHeight(360);
  renderer.handlePriceWheel(610, 180, -100);
  assert.equal(renderer.isPriceZoomed(), true);
  // Act: リセット（dblclick 相当）。
  renderer.resetPriceZoom();
  // Assert: 自動スケール復帰。
  assert.equal(psState.autoScale, true);
  assert.equal(renderer.isPriceZoomed(), false);
});

test('isPriceZoomed: 軸ドラッグ由来の手動スケール（autoScale=OFF）でも true（入力手段を区別しない）', () => {
  const { chart, mainSeries, priceScale } = fakeZoomChart(360);
  const renderer = new ChartRenderer({ chart, mainSeries, lwc: fakeLwc() });
  assert.equal(renderer.isPriceZoomed(), false);
  // 軸ドラッグ相当: lwc ネイティブが autoScale=false にする。
  priceScale.applyOptions({ autoScale: false });
  assert.equal(renderer.isPriceZoomed(), true, 'ドラッグ由来でもズーム中扱い（縦パン許可）');
});

// ---------------------------------------------------------------------------
// 発散防止の絶対クランプ（実機バグ修正の回帰）: ホイール連続入力（連打/慣性スクロール）で
//   レンジが複利増幅して 1e24 等へ発散した。clampPriceRange がデータ全幅由来の絶対範囲
//   （span ∈ [dataSpan×1e-4, dataSpan×5]・中心はデータ範囲 [min,max] 内）へ制限する。
// ---------------------------------------------------------------------------
test('clampPriceRange: span はデータ全幅の ×5 を超えず ×1e-4 未満に縮まない・中心はデータ範囲内', () => {
  const data = { min: 20000, max: 70000 }; // dataSpan=50000
  // 過大レンジ → maxSpan=250000（×5）に頭打ち・中心はデータ範囲内
  const big = clampPriceRange({ min: -1e24, max: 1e24 }, data);
  assert.ok((big.max - big.min) <= 250000 + 1e-6);
  const bigC = (big.min + big.max) / 2;
  assert.ok(bigC >= 20000 - 1e-6 && bigC <= 70000 + 1e-6, '中心はデータ範囲内');
  // 過小レンジ → minSpan=5 に下げ止め
  const tiny = clampPriceRange({ min: 45000, max: 45000.000001 }, data);
  assert.ok((tiny.max - tiny.min) >= 5 - 1e-9);
  // 正常レンジは不変
  const ok = clampPriceRange({ min: 40000, max: 60000 }, data);
  assert.deepEqual(ok, { min: 40000, max: 60000 });
  // データレンジ不明はそのまま（従来挙動）
  assert.deepEqual(clampPriceRange({ min: 1, max: 2 }, null), { min: 1, max: 2 });
});

test('handlePriceWheel: 連続 200 回のズームアウトでもレンジが発散しない（絶対クランプ）', () => {
  // Arrange: baseCandles（データ全幅 100..200）を持つ renderer
  const { chart, mainSeries, psState } = fakeZoomChart(360);
  const renderer = new ChartRenderer({ chart, mainSeries, lwc: fakeLwc() });
  renderer.setCandles([
    { time: 1, open: 120, high: 200, low: 100, close: 150 },
    { time: 2, open: 150, high: 180, low: 110, close: 160 },
  ]);
  renderer.setPaneHeight(360);
  // Act: ズームアウト（deltaY=+100）を 200 連打（毎回 getVisibleRange を読み直す＝実挙動）
  for (let i = 0; i < 200; i += 1) {
    renderer.handlePriceWheel(610, 180, +100);
  }
  // Assert: span はデータ全幅(100)×5=500 を超えない（発散しない）
  const span = psState.range.to - psState.range.from;
  assert.ok(span <= 500 + 1e-6, `span=${span} は 500 以下`);
  assert.ok(Number.isFinite(psState.range.from) && Number.isFinite(psState.range.to));
});

// ---------------------------------------------------------------------------
// 縦ドラッグによる価格パン（panPriceByPixels）: 本体ドラッグの縦成分で価格レンジを平行移動する。
//   span 不変・下げ(dy>0)で表示レンジが上へ・データ範囲内に中心クランプ・pane 高未供給時は false。
// ---------------------------------------------------------------------------
test('panPriceByPixels: 下げ(dy>0)で表示レンジが上へ平行移動し span は不変', () => {
  const { chart, mainSeries, psState } = fakeZoomChart(360);
  const renderer = new ChartRenderer({ chart, mainSeries, lwc: fakeLwc() });
  renderer.setCandles([
    { time: 1, open: 40, high: 200, low: 0, close: 100 },
    { time: 2, open: 100, high: 190, low: 10, close: 150 },
  ]);
  renderer.setPaneHeight(360);
  // 現表示 0..200（span=200）。dy=+36（=10% of paneHeight）→ shift=+20。
  const handled = renderer.panPriceByPixels(36);
  assert.equal(handled, true);
  const span = psState.range.to - psState.range.from;
  assert.ok(Math.abs(span - 200) < 1e-6, 'span 不変');
  assert.ok(psState.range.from > 0, '下げで from が上昇（表示レンジ上へ）');
});

test('panPriceByPixels: 中心はデータ範囲内にクランプ（無限にパンできない）', () => {
  const { chart, mainSeries, psState } = fakeZoomChart(360);
  const renderer = new ChartRenderer({ chart, mainSeries, lwc: fakeLwc() });
  renderer.setCandles([{ time: 1, open: 100, high: 120, low: 80, close: 110 }]); // データ 80..120
  renderer.setPaneHeight(360);
  // 大量にパン（dy=+1000 を連続）しても中心は [80,120] を超えない。
  for (let i = 0; i < 50; i += 1) {
    renderer.panPriceByPixels(1000);
  }
  const c = (psState.range.from + psState.range.to) / 2;
  assert.ok(c >= 80 - 1e-6 && c <= 120 + 1e-6, `中心 ${c} はデータ範囲内`);
});

test('panPriceByPixels: pane 高未供給/ dy=0 は false（何もしない）', () => {
  const { chart, mainSeries } = fakeZoomChart(360);
  const renderer = new ChartRenderer({ chart, mainSeries, lwc: fakeLwc() });
  renderer.setCandles([{ time: 1, open: 100, high: 120, low: 80, close: 110 }]);
  assert.equal(renderer.panPriceByPixels(20), false, 'pane 高未供給→false');
  renderer.setPaneHeight(360);
  assert.equal(renderer.panPriceByPixels(0), false, 'dy=0→false');
});
