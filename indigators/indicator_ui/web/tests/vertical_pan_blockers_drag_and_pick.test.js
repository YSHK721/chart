// 水準線 drag（スライス 4）とアーム式ピッカー（スライス 8-d）が縦パンブロッカーを**併用**しても
// 互いを壊さないことの検証（ISSUE-368）。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   §4-C（`isVerticalPanBlocked` は**単数スロット**でリプレイ root が使用中＝合成可能にするのが根治。
//    単数スロット競合は `setCandleObserver` / `setTfPeriodHoverHandler` で既に踏んだ破綻型）、
//   スライス 3 通過条件 2（ブロッカー 2 個を登録し、どちらか真で `panPriceByPixels` が呼ばれない）。
//
// 観点: 2 者が同じ登録口を使うため、「片方の解除がもう片方の抑止まで解除する」と、
//   ピッカー中にチャートが縦へ動く（＝狙った価格を押せない）。OR 合成の維持を実物で固定する。
// 構造: Arrange-Act-Assert。installSharedUi の実物を使う（登録口が本当に効くことまで見る）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { installSharedUi } from '../js/adapter/front/chart_app_wiring.js';
import { PriceLevelDragController } from '../js/adapter/front/price_level_drag_controller.js';
import { PricePickController } from '../js/adapter/front/price_pick_controller.js';

function fakeContainer() {
  const handlers = {};
  return {
    addEventListener(type, fn) { (handlers[type] ||= []).push(fn); },
    getBoundingClientRect() { return { left: 0, top: 0 }; },
    fire(type, ev) { (handlers[type] || []).forEach((fn) => fn(ev)); },
  };
}

function fakeRenderer() {
  const calls = { pan: [] };
  return {
    calls,
    panPriceByPixels: (dy) => { calls.pan.push(dy); },
    handlePriceWheel: () => false,
    isOverPriceAxis: () => false,
    resetPriceZoom() {},
    setPaneHeight() {},
    isLatestBarVisible: () => true,
    scrollToLatest() {},
    barInfoAt: () => null,
    setUserInteraction() {},
    priceAtCoordinate: (y) => 59000 - y,
    paneIndexAtCoordinate: (y) => (y >= 0 && y < 300 ? 0 : 1),
    snapCandidatesAt: () => [],
  };
}

// 掴める線が y=200 にだけ在る primitive（drag のホバー判定の入力）。
const fakePrimitive = {
  handleAt: (y, tol) => (Math.abs(y - 200) <= tol ? { kind: 'stop', index: null } : null),
};

function boot() {
  const container = fakeContainer();
  const renderer = fakeRenderer();
  const shared = installSharedUi({
    container, renderer, doc: null, getController: () => null, updatePaneHeight: () => {},
  });
  const drag = new PriceLevelDragController({
    container,
    renderer,
    primitive: fakePrimitive,
    getLevels: () => null,
    registerVerticalPanBlocker: shared.registerVerticalPanBlocker,
  });
  drag.install();
  const picker = new PricePickController({
    container,
    renderer,
    document: null,
    registerVerticalPanBlocker: shared.registerVerticalPanBlocker,
  });
  picker.install();
  return {
    container, renderer, drag, picker,
  };
}

// 縦パンが起きたか（線から離れた y で操作する＝drag の掴み判定に触れない）。
function pans({ container, renderer }) {
  const before = renderer.calls.pan.length;
  container.fire('pointerdown', { button: 0, clientX: 10, clientY: 50 });
  container.fire('pointermove', { buttons: 1, clientX: 10, clientY: 80 });
  container.fire('pointerup', {});
  return renderer.calls.pan.length > before;
}

test('TC-VB01 どちらも非活性なら従来どおり縦パンする（機能追加で既存操作を奪わない）', () => {
  // Arrange / Act / Assert
  assert.equal(pans(boot()), true);
});

test('TC-VB02 ピッカーがアーム中は縦パンしない（drag は非活性でも止まる）', () => {
  // Arrange
  const ctx = boot();
  // Act
  ctx.picker.arm('stop');
  // Assert
  assert.equal(pans(ctx), false);
});

test('TC-VB03 ピッカー解除で縦パンが戻る（解除漏れを作らない）', () => {
  // Arrange
  const ctx = boot();
  ctx.picker.arm('stop');
  // Act
  ctx.picker.disarm();
  // Assert
  assert.equal(pans(ctx), true);
});

test('TC-VB04 drag のホバー中は縦パンしない（ピッカー非アームでも止まる＝OR 合成）', () => {
  // Arrange
  const ctx = boot();
  // Act: 掴める線（y=200）の上をホバーする。
  ctx.container.fire('pointermove', { clientX: 10, clientY: 200 });
  // Assert
  assert.equal(pans(ctx), false, 'drag 側のブロッカーが独立に効く');
});

test('TC-VB05 両方が活性のとき、片方だけ解除しても抑止は残る（もう片方の抑止を巻き添えにしない）', () => {
  // Arrange: ピッカーをアームし、かつ drag は線上をホバー中。
  const ctx = boot();
  ctx.picker.arm('stop');
  ctx.container.fire('pointermove', { clientX: 10, clientY: 200 });
  assert.equal(pans(ctx), false);
  // Act: ピッカーだけ解除する（drag はホバーしたまま）。
  ctx.picker.disarm();
  ctx.container.fire('pointermove', { clientX: 10, clientY: 200 });
  // Assert
  assert.equal(pans(ctx), false, '片方の解除が他方の抑止まで消してはならない');
});
