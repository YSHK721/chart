// replay_boundary_dim_theme.test.js — リプレイ減光境界がテーマの面（surface）に追従すること
//   （基本設計_指標カラーテーマ.md FR-C13・§4.2 #20・§7.4 段階 3 通過条件 6・§7.6 受入基準 6）。
//
// 減光色は「本番背景の各チャネル -10」という**派生**である（replay_boundary_dim.js の設計）。
//   モジュール定数として読むと、背景を変えたときに減光帯だけ旧色に残る（依頼者が指摘した破綻）。
//   よって色は注入で受け、ChartRenderer が保持する配信済みクロム色（#20 replayBoundaryDim）が
//   ReplayView 経由で届く経路を固定する。未注入時は現行リテラルのまま（挙動不変・D-11）。
//
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ReplayBoundaryDimPrimitive } from '../js/adapter/front/replay_boundary_dim.js';
import { ReplayView } from '../js/adapter/front/replay_view.js';
import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';
import { ChromeThemeApplier } from '../js/adapter/front/chrome_theme_applier.js';
import { resolveAllChrome } from '../js/usecase/color_resolver.js';
import { CHROME_CURRENT } from '../js/usecase/chrome_tokens.js';

const WHITE_SURFACE = {
  themeId: 'thm#1', name: 't', roleColors: { surface: '#ffffff' }, tfModifier: null,
};

function fakeTimeScale({ x = 100, barSpacing = 8 } = {}) {
  return { timeToCoordinate: () => x, options: () => ({ barSpacing }) };
}

// 塗った色を捕捉する描画ターゲット（lwc 実体には触れない）。
function fakeTarget() {
  const painted = [];
  return {
    painted,
    useBitmapCoordinateSpace(fn) {
      fn({
        bitmapSize: { width: 1000, height: 400 },
        horizontalPixelRatio: 1,
        context: { set fillStyle(v) { painted.push(v); }, fillRect() {} },
      });
    },
  };
}

function paintColorOf(primitive) {
  primitive.attached({ chart: { timeScale: () => fakeTimeScale() }, series: {}, requestUpdate: () => {} });
  primitive.setBoundaryTime(1000);
  const target = fakeTarget();
  primitive._draw(target);
  return target.painted.at(-1);
}

function fakeSeries() {
  const attached = [];
  return { attached, attachPrimitive(p) { attached.push(p); } };
}

function fakeDoc() {
  return { getElementById: () => null, createElement: () => ({}), querySelectorAll: () => [] };
}

// ChartRenderer の購読口だけを持つ最小 fake（ISP: ReplayView が要求するのはこの 1 メソッド）。
function fakeRenderer(initialSlots) {
  const observers = new Set();
  let held = initialSlots;
  return {
    addChromeObserver(fn) {
      observers.add(fn);
      fn(held);
      return () => observers.delete(fn);
    },
    deliver(slots) {
      held = slots;
      for (const fn of observers) { fn(held); }
    },
  };
}

// =========================================================================
// プリミティブ単体（色の注入）
// =========================================================================

test('D-11: 色を注入しなければ現行リテラルで塗る（挙動不変）', () => {
  // Arrange
  const p = new ReplayBoundaryDimPrimitive();
  // Act / Assert
  assert.equal(paintColorOf(p), CHROME_CURRENT.replayBoundaryDim);
});

test('FR-C13: 注入された色で塗る（コンストラクタ）', () => {
  // Arrange
  const { slots } = resolveAllChrome(WHITE_SURFACE);
  const p = new ReplayBoundaryDimPrimitive({ color: slots.replayBoundaryDim });
  // Act / Assert
  assert.equal(paintColorOf(p), slots.replayBoundaryDim);
  assert.notEqual(slots.replayBoundaryDim, CHROME_CURRENT.replayBoundaryDim, '前提: テーマで値が動く');
});

test('FR-C13: setColor で後から差し替えられ、再描画が要求される', () => {
  // Arrange
  const p = new ReplayBoundaryDimPrimitive();
  let updated = 0;
  p.attached({ chart: { timeScale: () => fakeTimeScale() }, series: {}, requestUpdate: () => { updated += 1; } });
  const { slots } = resolveAllChrome(WHITE_SURFACE);
  // Act
  p.setColor(slots.replayBoundaryDim);
  // Assert
  assert.equal(updated, 1, '色が変わったら塗り直しを要求する（さもないと旧色が残る）');
  assert.equal(paintColorOf(p), slots.replayBoundaryDim);
});

test('不正な色（null・非文字列）は無視して現在の色を保つ（全域的）', () => {
  // Arrange
  const p = new ReplayBoundaryDimPrimitive();
  p.attached({ chart: { timeScale: () => fakeTimeScale() }, series: {}, requestUpdate: () => {} });
  // Act
  p.setColor(null);
  p.setColor(123);
  // Assert
  assert.equal(paintColorOf(p), CHROME_CURRENT.replayBoundaryDim);
});

// =========================================================================
// 配信経路（ChartRenderer の保持値 → ReplayView → プリミティブ）
// =========================================================================

test('FR-C13: 起動時に保持されている色がメインの減光境界へ届く（購読の順序に依存しない）', () => {
  // Arrange
  const { slots } = resolveAllChrome(WHITE_SURFACE);
  const mainSeries = fakeSeries();
  // Act
  new ReplayView({
    chart: { timeScale: () => ({}) }, mainSeries, renderer: fakeRenderer(slots), document: fakeDoc(),
  });
  // Assert
  assert.equal(paintColorOf(mainSeries.attached[0]), slots.replayBoundaryDim);
});

test('FR-C13: テーマ適用の配信でメインの減光境界の色が更新される', () => {
  // Arrange
  const renderer = fakeRenderer(resolveAllChrome(null).slots);
  const mainSeries = fakeSeries();
  new ReplayView({
    chart: { timeScale: () => ({}) }, mainSeries, renderer, document: fakeDoc(),
  });
  const { slots } = resolveAllChrome(WHITE_SURFACE);
  // Act
  renderer.deliver(slots);
  // Assert
  assert.equal(paintColorOf(mainSeries.attached[0]), slots.replayBoundaryDim);
});

test('FR-C13: pane の減光境界も、配信済みの色・後から作られた分の双方が追従する', () => {
  // Arrange
  const renderer = fakeRenderer(resolveAllChrome(null).slots);
  const paneSeries = fakeSeries();
  const chart = {
    timeScale: () => ({}),
    panes: () => [{ getSeries: () => [fakeSeries()] }, { getSeries: () => [paneSeries] }],
  };
  const view = new ReplayView({
    chart, mainSeries: fakeSeries(), renderer, document: fakeDoc(),
  });
  const { slots } = resolveAllChrome(WHITE_SURFACE);
  // Act: 配信 → その**後**に pane が作られる（指標追加の順序）
  renderer.deliver(slots);
  view.syncBoundary({ replayStart: 1, candles: [{ time: 100 }, { time: 200 }] });
  // Assert
  assert.equal(paintColorOf(paneSeries.attached[0]), slots.replayBoundaryDim, '後から作られた pane も現在の色で塗る');

  // Act: 既に作られている pane へ後から配信する（テーマ切替の順序）
  const back = resolveAllChrome(null).slots;
  renderer.deliver(back);
  // Assert
  assert.equal(paintColorOf(paneSeries.attached[0]), back.replayBoundaryDim, '既存 pane も配信で追従する');
});

test('端から端まで: 実体（ChromeThemeApplier → ChartRenderer → ReplayView → プリミティブ）で色が届く', () => {
  // Arrange: fake を挟まず実クラスだけで結ぶ（受け口の名前・形の食い違いを検出する）。
  const mainSeriesLwc = {
    setData() {}, applyOptions() {}, priceScale: () => ({ applyOptions() {} }),
    attached: [], attachPrimitive(p) { this.attached.push(p); },
  };
  const chart = {
    addSeries: () => mainSeriesLwc, addPane: () => ({}), panes: () => [],
    applyOptions() {}, subscribeCrosshairMove() {}, timeScale: () => ({}),
  };
  const renderer = new ChartRenderer({ chart, mainSeries: mainSeriesLwc, lwc: {} });
  const applier = new ChromeThemeApplier({ chromeSink: renderer, rootStyle: null });
  new ReplayView({
    chart: { timeScale: () => ({}) }, mainSeries: mainSeriesLwc, renderer, document: fakeDoc(),
  });
  const { slots } = resolveAllChrome(WHITE_SURFACE);
  // Act
  applier.apply(resolveAllChrome(WHITE_SURFACE));
  // Assert
  assert.equal(paintColorOf(mainSeriesLwc.attached.at(-1)), slots.replayBoundaryDim);
});

test('D-11: 購読口を持たない renderer（後方互換 Fake）では現行リテラルのまま', () => {
  // Arrange
  const mainSeries = fakeSeries();
  // Act
  new ReplayView({
    chart: { timeScale: () => ({}) }, mainSeries, renderer: {}, document: fakeDoc(),
  });
  // Assert
  assert.equal(paintColorOf(mainSeries.attached[0]), CHROME_CURRENT.replayBoundaryDim);
});
