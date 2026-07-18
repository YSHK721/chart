// chart_renderer.js のライブ追従向け additive 3メソッドの仕様検証（構築子・既存メソッドは無改変・末尾追加）。
//
// 設計入力（確定・additive・replay inert）:
//   - subscribeVisibleRange(cb): timeScale().subscribeVisibleLogicalRangeChange で
//       atRightEdge = (getVisibleLogicalRange().to >= (total-1) - EPS)（EPS≈1バー）を計算し cb(bool)。
//   - scrollToRealTime(): 最新足へスナップ（timeScale().scrollToRealTime へ委譲）。
//   - setAnalysisTint(on): chart.applyOptions({layout:{background:...}}) で背景 tint 適用/解除
//       （既定背景を保持し on で tint、off で復元）。
//   非提供 API（timeScale/subscribe/applyOptions 欠如）は no-op（後方互換）。
// 構造: Arrange-Act-Assert（AAA）。DOM 非依存・Fake chart 注入で観測。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';

// 可視範囲購読・scrollToRealTime・applyOptions を観測できる最小 Fake chart。
function fakeChartWithTimeScale({ visibleRange = { from: 0, to: 0 }, options = null } = {}) {
  let rangeHandler = null;
  const scrollCalls = { count: 0 };
  const applied = [];
  const ts = {
    fitContent() {},
    subscribeVisibleLogicalRangeChange(h) { rangeHandler = h; },
    getVisibleLogicalRange() { return visibleRange; },
    scrollToRealTime() { scrollCalls.count += 1; },
  };
  const chart = {
    addSeries: () => ({ setData() {}, applyOptions() {}, priceScale: () => ({ applyOptions() {} }) }),
    subscribeCrosshairMove() {},
    timeScale: () => ts,
    applyOptions(o) { applied.push(o); },
    options: () => options,
    _fireRange() { if (rangeHandler) { rangeHandler(); } },
    _scrollCalls: scrollCalls,
    _applied: applied,
    _setVisible(r) { visibleRange = r; },
  };
  return chart;
}

function makeRenderer(chart) {
  const mainSeries = { setData() {}, applyOptions() {}, priceScale: () => ({ applyOptions() {} }) };
  return new ChartRenderer({ chart, mainSeries, lwc: {} });
}

test('subscribeVisibleRange: 末尾右端で atRightEdge=true を cb へ渡す（to >= total-1-EPS）', () => {
  // Arrange: total=10 → 末尾 index=9。to=9.5（右端 rightOffset 余白）なら右端。
  const chart = fakeChartWithTimeScale({ visibleRange: { from: 4, to: 9.5 } });
  const renderer = makeRenderer(chart);
  renderer.setCandles(Array.from({ length: 10 }, (_, i) => ({ time: i + 1, open: 1, high: 1, low: 1, close: 1 })));
  const got = [];

  // Act
  renderer.subscribeVisibleRange((atRightEdge) => got.push(atRightEdge));
  chart._fireRange();

  // Assert
  assert.deepEqual(got, [true]);
});

test('subscribeVisibleRange: 左スクロール（to が小）で atRightEdge=false', () => {
  // Arrange: total=10 → 末尾 index=9。EPS=1 なので閾値 8。to=5 は右端でない。
  const chart = fakeChartWithTimeScale({ visibleRange: { from: 0, to: 5 } });
  const renderer = makeRenderer(chart);
  renderer.setCandles(Array.from({ length: 10 }, (_, i) => ({ time: i + 1, open: 1, high: 1, low: 1, close: 1 })));
  const got = [];

  renderer.subscribeVisibleRange((atRightEdge) => got.push(atRightEdge));
  chart._fireRange();

  assert.deepEqual(got, [false]);
});

test('subscribeVisibleRange: EPS 境界（to = total-1-EPS ちょうど）は右端扱い（>=）', () => {
  // Arrange: total=10 → 末尾 9・EPS=1 → 閾値 8。to=8 ちょうどは >= で true。
  const chart = fakeChartWithTimeScale({ visibleRange: { from: 3, to: 8 } });
  const renderer = makeRenderer(chart);
  renderer.setCandles(Array.from({ length: 10 }, (_, i) => ({ time: i + 1, open: 1, high: 1, low: 1, close: 1 })));
  const got = [];

  renderer.subscribeVisibleRange((atRightEdge) => got.push(atRightEdge));
  chart._fireRange();

  assert.deepEqual(got, [true]);
});

test('subscribeVisibleRange: EPS 境界の直下（to = total-1-EPS より小）は右端でない', () => {
  // Arrange: 閾値 8。to=7.99 は < 8 → false。
  const chart = fakeChartWithTimeScale({ visibleRange: { from: 3, to: 7.99 } });
  const renderer = makeRenderer(chart);
  renderer.setCandles(Array.from({ length: 10 }, (_, i) => ({ time: i + 1, open: 1, high: 1, low: 1, close: 1 })));
  const got = [];

  renderer.subscribeVisibleRange((atRightEdge) => got.push(atRightEdge));
  chart._fireRange();

  assert.deepEqual(got, [false]);
});

test('subscribeVisibleRange: timeScale 非提供なら no-op（例外を出さない）', () => {
  const mainSeries = { setData() {}, applyOptions() {}, priceScale: () => ({ applyOptions() {} }) };
  const chart = { addSeries: () => mainSeries, subscribeCrosshairMove() {} };
  const renderer = new ChartRenderer({ chart, mainSeries, lwc: {} });

  assert.doesNotThrow(() => renderer.subscribeVisibleRange(() => {}));
});

test('scrollToRealTime: timeScale().scrollToRealTime へ委譲する', () => {
  const chart = fakeChartWithTimeScale();
  const renderer = makeRenderer(chart);

  renderer.scrollToRealTime();

  assert.equal(chart._scrollCalls.count, 1);
});

test('scrollToRealTime: scrollToRealTime 非提供なら no-op（例外を出さない）', () => {
  const mainSeries = { setData() {}, applyOptions() {}, priceScale: () => ({ applyOptions() {} }) };
  const chart = { addSeries: () => mainSeries, subscribeCrosshairMove() {}, timeScale: () => ({}) };
  const renderer = new ChartRenderer({ chart, mainSeries, lwc: {} });

  assert.doesNotThrow(() => renderer.scrollToRealTime());
});

test('setAnalysisTint(true): chart.applyOptions({layout:{background}}) で tint を適用する', () => {
  const chart = fakeChartWithTimeScale({
    options: { layout: { background: { type: 'solid', color: '#131722' } } },
  });
  const renderer = makeRenderer(chart);

  renderer.setAnalysisTint(true);

  const last = chart._applied.at(-1);
  assert.ok(last && last.layout && last.layout.background, 'layout.background を適用');
  assert.notEqual(last.layout.background.color, '#131722', 'tint 色は既定と異なる');
});

test('setAnalysisTint(false): 既定背景（options 由来）へ復元する', () => {
  const chart = fakeChartWithTimeScale({
    options: { layout: { background: { type: 'solid', color: '#131722' } } },
  });
  const renderer = makeRenderer(chart);

  renderer.setAnalysisTint(true);
  renderer.setAnalysisTint(false);

  const last = chart._applied.at(-1);
  assert.equal(last.layout.background.color, '#131722', 'off で既定背景色へ復元');
});

test('setAnalysisTint: applyOptions 非提供なら no-op（例外を出さない）', () => {
  const mainSeries = { setData() {}, applyOptions() {}, priceScale: () => ({ applyOptions() {} }) };
  const chart = { addSeries: () => mainSeries, subscribeCrosshairMove() {}, timeScale: () => ({}) };
  const renderer = new ChartRenderer({ chart, mainSeries, lwc: {} });

  assert.doesNotThrow(() => renderer.setAnalysisTint(true));
});

// ---- ISSUE-119: 既定背景の参照エイリアシング回帰（in-place マージする実 lwc 相当） ----

test('ISSUE-119 setAnalysisTint: options() が内部参照を返し applyOptions が in-place マージでも復元できる', () => {
  // Arrange: 実 lwc 相当の fake — options() は内部 options オブジェクトへの「参照」を返し、
  //   applyOptions は同一オブジェクトへ再帰マージ（in-place 書き換え）する。
  const internal = { layout: { background: { type: 'solid', color: '#131722' } } };
  const merge = (dst, src) => {
    for (const k of Object.keys(src)) {
      if (src[k] && typeof src[k] === 'object' && dst[k] && typeof dst[k] === 'object') {
        merge(dst[k], src[k]);
      } else {
        dst[k] = src[k];
      }
    }
  };
  const chart = {
    addSeries: () => ({ setData() {}, applyOptions() {} }),
    subscribeCrosshairMove() {},
    timeScale: () => ({}),
    options: () => internal, // 参照をそのまま返す（コピーしない）
    applyOptions: (o) => merge(internal, o),
  };
  const mainSeries = { setData() {}, applyOptions() {}, priceScale: () => ({ applyOptions() {} }) };
  const renderer = new ChartRenderer({ chart, mainSeries, lwc: {} });

  // Act: 既定捕捉（false）→ tint ON → 復元。
  renderer.setAnalysisTint(false); // 初回捕捉（この時点の色 #131722 を snapshot）
  renderer.setAnalysisTint(true);
  assert.equal(internal.layout.background.color, '#1b1a24', 'tint ON で内部色が tint 色へ');
  renderer.setAnalysisTint(false);

  // Assert: 参照エイリアシングがあると tint 色のまま（旧バグ）。snapshot 化により既定色へ戻る。
  assert.equal(internal.layout.background.color, '#131722', 'FOLLOW 復帰で既定色へ復元される');
  assert.equal(internal.layout.background.type, 'solid', 'type も維持');
});
