// ISSUE-116: 「最新のバーまでスクロール」ボタン（ScrollToLatestButton）の回帰検証。
//
// 仕様: 右下ホットゾーン（右 20%×下 50%）ホバー かつ 最新足が可視範囲外のときのみ表示。
//   クリックで renderer.scrollToRealTime() → 非表示。pointerleave で非表示。
// 構造: Arrange-Act-Assert（AAA）。jsdom を避けた最小 DOM スタブ（C-2）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ScrollToLatestButton } from '../js/adapter/front/scroll_to_latest_button.js';
import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';

function fakeEl() {
  const el = {
    className: '', textContent: '', title: '', type: '', children: [],
    _handlers: {},
    _cls: new Set(['is-hidden']),
    classList: {
      toggle(c, on) {
        const has = el._cls.has(c);
        const next = on === undefined ? !has : on;
        if (next) el._cls.add(c); else el._cls.delete(c);
      },
      contains(c) { return el._cls.has(c); },
    },
    addEventListener(ev, fn) { el._handlers[ev] = fn; },
    fire(ev, arg) { if (el._handlers[ev]) el._handlers[ev](arg); },
  };
  return el;
}

function fakeContainer() {
  const c = fakeEl();
  c.appended = [];
  c.appendChild = (child) => c.appended.push(child);
  c.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1000, height: 500 });
  return c;
}

function build({ latestVisible = false } = {}) {
  const container = fakeContainer();
  const calls = { scroll: 0 };
  const renderer = {
    isLatestBarVisible: () => latestVisible,
    scrollToRealTime: () => { calls.scroll += 1; },
  };
  const doc = { createElement: () => fakeEl() };
  const ui = new ScrollToLatestButton({ container, renderer, document: doc });
  ui.install();
  const btn = container.appended[0];
  return { container, renderer, btn, calls, ui };
}

// ホットゾーン内座標（右 20%: x>=800・下 50%: y>=250）。
const IN_ZONE = { clientX: 900, clientY: 400 };
const OUT_ZONE = { clientX: 500, clientY: 100 };

test('ISSUE-116 install: ボタンを container へ追加（既定は非表示・title/ラベル設定）', () => {
  const { btn } = build();
  assert.ok(btn);
  assert.equal(btn.textContent, '»');
  assert.equal(btn.title, '最新のバーまでスクロール');
  assert.equal(btn.classList.contains('is-hidden'), true, '既定は非表示');
});

test('ISSUE-116 表示条件: ホットゾーン内ホバー かつ 最新足が範囲外 → 表示', () => {
  const { container, btn } = build({ latestVisible: false });
  container.fire('pointermove', IN_ZONE);
  assert.equal(btn.classList.contains('is-hidden'), false, '表示される');
});

test('ISSUE-116 表示条件: 最新足が可視（戻る必要なし）はホットゾーン内でも非表示', () => {
  const { container, btn } = build({ latestVisible: true });
  container.fire('pointermove', IN_ZONE);
  assert.equal(btn.classList.contains('is-hidden'), true);
});

test('ISSUE-116 表示条件: ホットゾーン外は最新足が範囲外でも非表示', () => {
  const { container, btn } = build({ latestVisible: false });
  container.fire('pointermove', OUT_ZONE);
  assert.equal(btn.classList.contains('is-hidden'), true);
});

test('ISSUE-116 pointerleave: チャート外へ退出で非表示に戻る', () => {
  const { container, btn } = build({ latestVisible: false });
  container.fire('pointermove', IN_ZONE);
  assert.equal(btn.classList.contains('is-hidden'), false);
  container.fire('pointerleave');
  assert.equal(btn.classList.contains('is-hidden'), true);
});

test('ISSUE-116 クリック: scrollToRealTime を呼び非表示へ', () => {
  const { container, btn, calls } = build({ latestVisible: false });
  container.fire('pointermove', IN_ZONE);
  btn.fire('click');
  assert.equal(calls.scroll, 1);
  assert.equal(btn.classList.contains('is-hidden'), true);
});

test('ISSUE-116 防御: DOM 不在（container/doc 無し）でも install は例外を投げない', () => {
  assert.doesNotThrow(() => new ScrollToLatestButton({ container: {}, renderer: {}, document: null }).install());
  assert.doesNotThrow(() => new ScrollToLatestButton({ container: null, renderer: {}, document: { createElement: () => ({}) } }).install());
});

// ---- renderer.isLatestBarVisible（表示判定の実体） --------------------------

function rendererWithRange(range, candles) {
  const ts = {
    applyOptions() {}, options: () => ({ barSpacing: 6 }), width: () => 600,
    fitContent() {}, getVisibleLogicalRange: () => range,
  };
  const chart = {
    timeScale: () => ts, panes: () => [],
    addSeries() { return { setData() {}, applyOptions() {} }; },
    subscribeCrosshairMove() {},
  };
  const main = { setData() {}, applyOptions() {}, priceScale: () => ({ applyOptions() {} }) };
  const r = new ChartRenderer({ chart, mainSeries: main, lwc: {} });
  if (candles) {
    r._baseCandles = candles; // setCandles 相当（fitContent 副作用を避け直接供給）
  }
  return r;
}

const CANDLES_10 = Array.from({ length: 10 }, (_, i) => ({ time: i + 1, open: 1, high: 2, low: 0.5, close: 1.5 }));

test('ISSUE-116 isLatestBarVisible: 可視範囲 to が末尾 index 以上なら true（右余白込みの最新表示）', () => {
  const r = rendererWithRange({ from: 0, to: 9.5 }, CANDLES_10);
  assert.equal(r.isLatestBarVisible(), true);
});

test('ISSUE-116 isLatestBarVisible: 過去へ遡り末尾が範囲外なら false', () => {
  const r = rendererWithRange({ from: 0, to: 5 }, CANDLES_10);
  assert.equal(r.isLatestBarVisible(), false);
});

test('ISSUE-116 isLatestBarVisible: レンジ不明・データ無しは true（安全側＝ボタン非表示）', () => {
  assert.equal(rendererWithRange(null, CANDLES_10).isLatestBarVisible(), true);
  assert.equal(rendererWithRange({ from: 0, to: 5 }, null).isLatestBarVisible(), true);
});

// ---- ISSUE-116 追記3: 速度 x2（自前イージングスクロール） -------------------

test('ISSUE-116 速度x2: speed>1 は scrollToPosition を毎フレーム刻み 500ms で目標へ到達（animated=false）', () => {
  const positions = [];
  const ts = {
    applyOptions() {}, options: () => ({ barSpacing: 6, rightOffset: 5 }), width: () => 600,
    fitContent() {},
    scrollToRealTime() { positions.push('native'); },
    scrollPosition: () => -100, // 過去へ 100 バー遡っている状態
    scrollToPosition: (pos, animated) => positions.push([pos, animated]),
  };
  const chart = {
    timeScale: () => ts, panes: () => [],
    addSeries() { return { setData() {}, applyOptions() {} }; },
    subscribeCrosshairMove() {},
  };
  const main = { setData() {}, applyOptions() {}, priceScale: () => ({ applyOptions() {} }) };
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: {} });
  // rAF スタブ: 125ms 刻みで同期実行（500ms ぶん＝4 フレームで完了する想定）
  let now = 0;
  const orig = globalThis.requestAnimationFrame;
  globalThis.requestAnimationFrame = (fn) => { now += 125; fn(now); };
  try {
    renderer.scrollToRealTime({ speed: 2 });
  } finally {
    if (orig === undefined) delete globalThis.requestAnimationFrame;
    else globalThis.requestAnimationFrame = orig;
  }
  assert.ok(positions.length >= 2, '複数フレーム刻む');
  assert.ok(!positions.includes('native'), 'lwc 既定アニメは使わない');
  const last = positions.at(-1);
  assert.equal(last[1], false, '毎フレーム animated=false');
  assert.ok(Math.abs(last[0] - 5) < 1e-9, '最終位置は rightOffset（=5）');
  // 500ms（speed=2）で完了: t0=125ms 起点 + 500ms = 625ms 時点のフレームが最終
  assert.equal(now, 625, '完了後に余計なフレームを積まない');
});

test('ISSUE-116 速度x2: speed 省略/1 や API 欠落は lwc 既定 scrollToRealTime へフォールバック', () => {
  const calls = [];
  const ts = {
    applyOptions() {}, options: () => ({ barSpacing: 6 }), width: () => 600,
    fitContent() {}, scrollToRealTime: () => calls.push('native'),
    // scrollPosition/scrollToPosition 無し＝API 欠落
  };
  const chart = {
    timeScale: () => ts, panes: () => [],
    addSeries() { return { setData() {}, applyOptions() {} }; },
    subscribeCrosshairMove() {},
  };
  const main = { setData() {}, applyOptions() {}, priceScale: () => ({ applyOptions() {} }) };
  const renderer = new ChartRenderer({ chart, mainSeries: main, lwc: {} });
  renderer.scrollToRealTime(); // speed 省略 → 既定
  renderer.scrollToRealTime({ speed: 2 }); // API 欠落 → フォールバック
  assert.deepEqual(calls, ['native', 'native']);
});
