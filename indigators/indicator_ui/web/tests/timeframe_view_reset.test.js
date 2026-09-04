// ISSUE-113/114: 時間足切替の価格スケールリセットとチャート右端の常設余白の回帰検証。
//
// 設計入力:
//   - ISSUE-113（ユーザー裁定）: 時間足切替で手動価格スケール（拡大/縦パン）を自動スケールへリセット。
//     解除点は「価格軸 dblclick または時間足切替」。setCandles（replay リビールが毎バー呼ぶ）では触れない。
//   - ISSUE-114: 右端に常設余白（BASE_RIGHT_OFFSET_BARS=5）。fitContent 直後は scrollToRealTime で反映。
//     MP プロファイル余白の解除は 0 でなく常設余白へ復元・適用時は max(計算値, 常設) 合成。
// 構造: Arrange-Act-Assert（AAA）。ports/chart は最小 Fake・DOM 非依存。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';
import { get } from '../js/usecase/catalog.js';

// ---- ISSUE-113: setTimeframe → resetPriceZoom ------------------------------

function tfController() {
  const noop = () => {};
  const calls = { resetPriceZoom: 0 };
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async (req) => ({ ok: true, generation: req.generation ?? 0, series: [] }) },
    persistence: { loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop, loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1 },
    renderer: {
      renderLine: noop, renderHorizontal: noop, setData: noop, setVisible: noop, remove: noop,
      setCandles: noop,
      resetPriceZoom: () => { calls.resetPriceZoom += 1; },
    },
    document: null, mode: 'b', datasetRef: 'jp225_m1', timeframe: '1D', recentBars: 1500,
    loadCandles: async () => [{ time: 1, open: 1, high: 1, low: 1, close: 1 }],
  });
  return { ctrl, calls };
}

test('ISSUE-113 setTimeframe: 切替時に resetPriceZoom（自動スケール復帰）を呼ぶ', async () => {
  const { ctrl, calls } = tfController();
  await ctrl.setTimeframe('4h');
  assert.equal(calls.resetPriceZoom, 1, '切替 1 回につきリセット 1 回');
});

test('ISSUE-113 setTimeframe: 同一足（no-op）ではリセットしない', async () => {
  const { ctrl, calls } = tfController();
  await ctrl.setTimeframe('1D'); // 既定と同一
  assert.equal(calls.resetPriceZoom, 0);
});

test('ISSUE-113 setTimeframe: renderer が resetPriceZoom 非対応（旧 Fake）でも例外なく切替完了', async () => {
  const noop = () => {};
  const ctrl = new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async (req) => ({ ok: true, generation: req.generation ?? 0, series: [] }) },
    persistence: { loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop, loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1 },
    renderer: { renderLine: noop, renderHorizontal: noop, setData: noop, setVisible: noop, remove: noop, setCandles: noop },
    document: null, mode: 'b', datasetRef: 'jp225_m1', timeframe: '1D',
    loadCandles: async () => [],
  });
  await assert.doesNotReject(() => ctrl.setTimeframe('1W'));
  assert.equal(ctrl._timeframe, '1W');
});

// ---- ISSUE-114: 常設右余白 --------------------------------------------------

function fakeTimeScale() {
  return {
    _options: {}, _fit: 0, _scrolled: 0, _order: [],
    applyOptions(o) { Object.assign(this._options, o); },
    options() { return { barSpacing: 6, ...this._options }; },
    width() { return 600; },
    fitContent() { this._fit += 1; this._order.push('fit'); },
    scrollToRealTime() { this._scrolled += 1; this._order.push('scroll'); },
  };
}

function marginRenderer() {
  const ts = fakeTimeScale();
  const chart = {
    timeScale: () => ts,
    panes: () => [],
    addSeries() { return { setData() {}, applyOptions() {} }; },
    subscribeCrosshairMove() {},
  };
  const main = { setData() {}, applyOptions() {}, priceScale: () => ({ applyOptions() {} }) };
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: {} });
  return { renderer, ts };
}

test('ISSUE-114 生成時: timeScale へ常設 rightOffset=5 を適用する', () => {
  const { ts } = marginRenderer();
  assert.equal(ts._options.rightOffset, 5);
});

test('ISSUE-114 setCandles: fitContent 直後に scrollToRealTime で余白を反映する（順序保証）', () => {
  const { renderer, ts } = marginRenderer();
  renderer.setCandles([{ time: 1, open: 1, high: 2, low: 0.5, close: 1.5 }]);
  assert.equal(ts._fit, 1);
  assert.equal(ts._scrolled, 1);
  assert.deepEqual(ts._order, ['fit', 'scroll'], 'fit → scroll の順');
});

test('ISSUE-114 setRightMarginFraction(null): 復元先は 0 でなく常設余白 5', () => {
  const { renderer, ts } = marginRenderer();
  renderer.setRightMarginFraction(0.3);
  renderer.setRightMarginFraction(null);
  assert.equal(ts._options.rightOffset, 5, '0 へ戻さない（右端張り付き防止）');
});

test('ISSUE-114 setRightMarginFraction: プロファイル余白は max(計算値, 常設 5) 合成', () => {
  const { renderer, ts } = marginRenderer();
  renderer.setRightMarginFraction(0.3); // width600×0.3/bs6 = 30 バー > 5
  assert.equal(ts._options.rightOffset, 30);
  renderer.setRightMarginFraction(0.01); // 600×0.01/6 = 1 バー < 5 → 常設 5 を維持
  assert.equal(ts._options.rightOffset, 5);
});

// ---- ISSUE-115: px 基準の右余白（ズーム変化に追従） -------------------------

function zoomableRenderer() {
  let barSpacing = 6;
  const subs = [];
  const ts = {
    _options: {}, _order: [],
    applyOptions(o) { Object.assign(this._options, o); },
    options() { return { barSpacing, ...({}) }; },
    width() { return 600; },
    fitContent() { this._order.push('fit'); },
    scrollToRealTime() { this._order.push('scroll'); },
    subscribeVisibleLogicalRangeChange(fn) { subs.push(fn); },
  };
  const chart = {
    timeScale: () => ts,
    panes: () => [],
    addSeries() { return { setData() {}, applyOptions() {} }; },
    subscribeCrosshairMove() {},
  };
  const main = { setData() {}, applyOptions() {}, priceScale: () => ({ applyOptions() {} }) };
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: {} });
  return { renderer, ts, setBarSpacing: (v) => { barSpacing = v; }, fireRangeChange: () => subs.forEach((f) => f()) };
}

test('ISSUE-115 生成時: rightOffset = width×5% ÷ barSpacing（600×0.05/6 = 5 バー）', () => {
  const { ts } = zoomableRenderer();
  assert.equal(ts._options.rightOffset, 5);
});

// ISSUE-164（ユーザー裁定 2026-07-23・旧 ISSUE-115 ズーム追従仕様を廃止）:
//   ズーム（可視範囲変化）イベントで rightOffset を再適用しない。ユーザーの拡大縮小操作と
//   無関係な余白の再適用は lwc で「右端スクロール」副作用を持ち、過去閲覧中のジャンプの根本原因
//   だった。余白の適用点は明示イベント（初期表示・時間足切替・MP 余白率変更・最新足へ戻る）のみ。
test('ISSUE-164 ズーム非反応: barSpacing 変化の可視範囲イベントでは rightOffset を再適用しない', () => {
  const { ts, setBarSpacing, fireRangeChange } = zoomableRenderer();
  const before = ts._options.rightOffset;
  setBarSpacing(0.5);
  fireRangeChange();
  assert.equal(ts._options.rightOffset, before, 'ズームで余白を書き換えない（ユーザー操作優先）');
  setBarSpacing(15);
  fireRangeChange();
  assert.equal(ts._options.rightOffset, before, '拡大でも書き換えない');
});

test('ISSUE-115 ループ防止: barSpacing 不変の可視範囲イベントでは applyOptions を再発行しない', () => {
  const { ts, fireRangeChange } = zoomableRenderer();
  const before = ts._options.rightOffset;
  let applies = 0;
  const orig = ts.applyOptions.bind(ts);
  ts.applyOptions = (o) => { applies += 1; orig(o); };
  fireRangeChange();
  fireRangeChange();
  assert.equal(applies, 0, '同値（±0.01 バー）はスキップ');
  assert.equal(ts._options.rightOffset, before);
});
