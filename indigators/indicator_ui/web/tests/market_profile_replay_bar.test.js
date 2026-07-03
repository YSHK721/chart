// market_profile_replay_bar.js のロジック検証（下部リプレイスライダバー）。
//
// 設計入力: 依頼（増分1）「replay=ON でチャート下部にスライダバーを表示（◀リプレイ ラベル＋
//   <input type=range> min=0/max=足数-1/既定=右端＋選択日時ラベル）。スライダ input で T=対応足 time
//   を決め onScrub(T) を呼ぶ。OFF で非表示」。移植元 prototype_260630-01（#asof/#asoft スライダ・asofIdx）。
//   DOM は最小 fake（要素・イベントリスナ）で注入。lightweight-charts 非依存。構造: AAA。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { MarketProfileReplayBar } from '../js/adapter/front/market_profile_replay_bar.js';

// --- 最小 fake DOM（createElement / addEventListener / dispatch）---------------
function fakeElement(tag) {
  return {
    tagName: tag,
    style: {},
    className: '',
    textContent: '',
    attrs: {},
    children: [],
    _listeners: {},
    value: '',
    min: '',
    max: '',
    type: '',
    disabled: false,
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return this.attrs[k]; },
    appendChild(c) { this.children.push(c); return c; },
    addEventListener(ev, fn) { (this._listeners[ev] ||= []).push(fn); },
    dispatch(ev) { (this._listeners[ev] || []).forEach((fn) => fn({ target: this })); },
  };
}

function fakeDocument() {
  return { createElement: (tag) => fakeElement(tag) };
}

const CANDLES = [
  { time: 1000, open: 1, high: 2, low: 1, close: 2 },
  { time: 2000, open: 2, high: 3, low: 2, close: 3 },
  { time: 3000, open: 3, high: 4, low: 3, close: 4 },
];

function makeBar({ onScrub } = {}) {
  const doc = fakeDocument();
  const container = fakeElement('div');
  const scrubs = [];
  const bar = new MarketProfileReplayBar({
    document: doc, container, onScrub: onScrub ?? ((t) => scrubs.push(t)),
  });
  return { bar, container, scrubs };
}

// スライダ input（range）を bar の内部から取り出す（子孫探索）。
function findRange(el) {
  if (el.type === 'range') return el;
  for (const c of el.children) {
    const found = findRange(c);
    if (found) return found;
  }
  return null;
}

test('replay bar is hidden by default (未表示コンテナ)', () => {
  // Arrange / Act
  const { bar } = makeBar();
  // Assert: 既定は非表示
  assert.equal(bar.isVisible(), false);
});

test('setVisible(true) shows the bar; setVisible(false) hides it', () => {
  // Arrange
  const { bar } = makeBar();
  // Act / Assert
  bar.setVisible(true);
  assert.equal(bar.isVisible(), true);
  bar.setVisible(false);
  assert.equal(bar.isVisible(), false);
});

test('setCandles wires the slider range min=0/max=足数-1 and defaults to the right end (最新)', () => {
  // Arrange
  const { bar, container } = makeBar();
  // Act
  bar.setCandles(CANDLES);
  const range = findRange(container);
  // Assert
  assert.ok(range, 'range 入力が生成される');
  assert.equal(Number(range.min), 0);
  assert.equal(Number(range.max), CANDLES.length - 1); // 2
  assert.equal(Number(range.value), CANDLES.length - 1); // 既定=右端=最新
});

test('currentTime(): スクラブ前は最新（右端）の time を返し、スクラブ後は選択足の time を返す', () => {
  // Arrange
  const { bar, container } = makeBar();
  assert.equal(bar.currentTime(), null, '空 candles は null');
  bar.setCandles(CANDLES);
  // Assert: 既定=右端=最新（3000）。
  assert.equal(bar.currentTime(), 3000, '既定は最新足の time');
  // Act: idx=0 へ移動 → currentTime=1000。
  const range = findRange(container);
  range.value = '0';
  range.dispatch('input');
  assert.equal(bar.currentTime(), 1000, 'スクラブ後は選択足の time');
});

test('currentIndex(): スクラブ前は最新(右端)の index、スクラブ後は選択 index を返す（スワイプの startIdx 源）', () => {
  const { bar, container } = makeBar();
  assert.equal(bar.currentIndex(), 0, '空 candles は 0');
  bar.setCandles(CANDLES);
  assert.equal(bar.currentIndex(), CANDLES.length - 1, '既定は右端 index');
  const range = findRange(container);
  range.value = '1';
  range.dispatch('input');
  assert.equal(bar.currentIndex(), 1, 'スクラブ後は選択 index');
});

test('moving the slider calls onScrub with the time of the corresponding candle (index→time)', () => {
  // Arrange
  const { bar, container, scrubs } = makeBar();
  bar.setCandles(CANDLES);
  const range = findRange(container);
  // Act: idx=1 へ移動 → 対応足 time=2000
  range.value = '1';
  range.dispatch('input');
  // Assert
  assert.deepEqual(scrubs, [2000]);
});

test('slider input updates the selected datetime label', () => {
  // Arrange
  const { bar, container } = makeBar();
  bar.setCandles(CANDLES);
  const range = findRange(container);
  // Act
  range.value = '0';
  range.dispatch('input');
  // Assert: 日時ラベルが選択足の time を反映（空でない）
  assert.ok(bar.currentLabel().length > 0, '日時ラベルが更新される');
  assert.ok(bar.currentLabel().includes('1970'), 'UNIX 1000s は 1970 の日付');
});

test('no onScrub before candles are set (空 candles ガード)', () => {
  // Arrange
  const { bar, scrubs } = makeBar();
  // Act: candles 未設定でも例外を投げない（no-op）
  assert.doesNotThrow(() => bar.setVisible(true));
  // Assert
  assert.deepEqual(scrubs, []);
});

// ===========================================================================
// 増分2: モードトグル（アンカー/ローリング）・スナップショット・スワイプ→index→T 変換
//   移植元 prototype_260630-01（asofmode アンカー⇄ローリング・asoftrim スナップショット・
//   updateCaptureMode スワイプ・coordinateToLogical→index）。
// ===========================================================================

// onChange（モード/スナップショット変更）を記録できる makeBar 拡張。
function makeBar2({ onScrub, onChange } = {}) {
  const doc = fakeDocument();
  const container = fakeElement('div');
  const scrubs = [];
  const changes = [];
  const bar = new MarketProfileReplayBar({
    document: doc, container,
    onScrub: onScrub ?? ((t) => scrubs.push(t)),
    onChange: onChange ?? (() => changes.push({ mode: bar.mode(), snapshot: bar.isSnapshot() })),
  });
  return { bar, container, scrubs, changes };
}

// segmented のモードボタン（アンカー/ローリング）を探す。
function findByText(el, text) {
  if (el.textContent === text) return el;
  for (const c of el.children) {
    const f = findByText(c, text);
    if (f) return f;
  }
  return null;
}
function findCheckbox(el) {
  if (el.type === 'checkbox') return el;
  for (const c of el.children) {
    const f = findCheckbox(c);
    if (f) return f;
  }
  return null;
}

test('mode defaults to anchor; toggling to rolling updates mode() and fires onChange', () => {
  // Arrange
  const { bar, container, changes } = makeBar2();
  // Assert: 既定はアンカー
  assert.equal(bar.mode(), 'anchor');
  // Act: ローリングボタンを押す
  const rollingBtn = findByText(container, 'ローリング');
  assert.ok(rollingBtn, 'ローリングボタンが生成される');
  rollingBtn.dispatch('click');
  // Assert
  assert.equal(bar.mode(), 'rolling');
  assert.deepEqual(changes.at(-1), { mode: 'rolling', snapshot: false });
});

test('snapshot defaults to OFF; checking it flips isSnapshot() and fires onChange', () => {
  // Arrange
  const { bar, container, changes } = makeBar2();
  assert.equal(bar.isSnapshot(), false);
  // Act
  const cb = findCheckbox(container);
  assert.ok(cb, 'スナップショット checkbox が生成される');
  cb.checked = true;
  cb.dispatch('change');
  // Assert
  assert.equal(bar.isSnapshot(), true);
  assert.deepEqual(changes.at(-1), { mode: 'anchor', snapshot: true });
});

test('scrubToLogical clamps logical to candle index and emits the corresponding candle time', () => {
  // Arrange
  const { bar, scrubs } = makeBar2();
  bar.setCandles(CANDLES); // 3 本（index 0..2）
  scrubs.length = 0;
  // Act: logical=1.4 → round→1（time=2000）
  bar.scrubToLogical(1.4);
  // Assert
  assert.deepEqual(scrubs.at(-1), 2000);
  // Act: logical=99（範囲外）→ 末尾 index 2（time=3000）へ clamp
  bar.scrubToLogical(99);
  assert.deepEqual(scrubs.at(-1), 3000);
  // Act: logical=-5 → 先頭 index 0（time=1000）へ clamp
  bar.scrubToLogical(-5);
  assert.deepEqual(scrubs.at(-1), 1000);
});

test('scrubToLogical also syncs the slider value (双方向同期)', () => {
  // Arrange
  const { bar, container } = makeBar2();
  bar.setCandles(CANDLES);
  const range = findRange(container);
  // Act: logical=0 → index0
  bar.scrubToLogical(0);
  // Assert: スライダ値が index に追従
  assert.equal(Number(range.value), 0);
});

test('scrubToLogical is a no-op with null/NaN or before candles (no throw, no scrub)', () => {
  // Arrange
  const { bar, scrubs } = makeBar2();
  // Act / Assert: candles 未設定
  assert.doesNotThrow(() => bar.scrubToLogical(1));
  assert.deepEqual(scrubs, []);
  bar.setCandles(CANDLES);
  scrubs.length = 0;
  bar.scrubToLogical(null);
  bar.scrubToLogical(NaN);
  assert.deepEqual(scrubs, []);
});
