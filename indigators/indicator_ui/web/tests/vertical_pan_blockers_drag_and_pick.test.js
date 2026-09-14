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
    // ISSUE-440: 幾何が動いたら凡例を引き直す面。ChartInteractionController が pointermove /
    //   pointerup で呼ぶので、renderer ダブルも契約を満たす（部分実装を通さない＝fail-close）。
    refreshPaneLegendIfGeometryChanged: () => false, syncPaneGeometry: () => false,
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

// ---------------------------------------------------------------------------
// アーム中の drag 競合（工程 5 レビュー 🔴-2・node で再現済み）
//
//   再現ログ: `arm('take')` 中に既存の損切り線の近傍（掴み許容 6px）をクリックすると
//   `STOP MUTATED -> 58798`（＝**入力先でない水準が動く**。R-P1「入力先が常に一意」の破綻）。
//   さらに drag の `_end()` が `setUserInteraction(true)` を出すため、**アーム継続中なのに
//   lwc 操作が復帰**する（単数スロットの奪い合い＝`setCandleObserver` と同型の再発）。
//
//   根治は 2 つ:
//     (1) `setUserInteraction` の単数スロットを**登録方式**（`suppressInteraction()` →
//         解除関数）へ合成する。抑止を持つ者が 1 人でも居る間は復帰しない。
//         実測で本スロットは 3 者が奪い合っている（MP スワイプ捕捉・drag・ピッカー）。
//     (2) drag に「ピッカーがアーム中なら掴まない」述語を注入する（同型の登録口）。
// ---------------------------------------------------------------------------

import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';

// 実物の ChartRenderer を使う（合成の成否は renderer の実装で決まるため、fake で写すと
//   「fake だけ正しい」状態を作る）。lwc chart は applyOptions を記録する最小 fake。
function bootReal() {
  const applied = [];
  const chart = {
    applyOptions: (o) => applied.push(o),
    addSeries: () => ({ setData() {}, applyOptions() {}, data: () => [] }),
    subscribeCrosshairMove() {},
  };
  // 価格変換まで働く mainSeries（これが無いと priceAtCoordinate が null を返し、
  //   「掴めていないから動かない」のか「価格が取れないから動かない」のか区別できない）。
  const mainSeries = {
    applyOptions() {}, setData() {}, data: () => [], coordinateToPrice: (y) => 59000 - y,
  };
  const renderer = new ChartRenderer({ chart, mainSeries, lwc: {} });
  const container = fakeContainer();
  const shared = installSharedUi({
    container, renderer, doc: null, getController: () => null, updatePaneHeight: () => {},
  });
  let levels = {
    stopPrice: 58340,
    withStop(p) { return { ...this, stopPrice: p }; },
    withEntry() { return this; },
    withTake(p) { return { ...this, takePrice: p }; },
  };
  const picker = new PricePickController({
    container, renderer, document: null, registerVerticalPanBlocker: shared.registerVerticalPanBlocker,
  });
  picker.install();
  const drag = new PriceLevelDragController({
    container,
    renderer,
    primitive: fakePrimitive,
    getLevels: () => levels,
    onLevelsChange: (next) => { levels = next; },
    registerVerticalPanBlocker: shared.registerVerticalPanBlocker,
    isGrabBlocked: () => picker.isArmed(),
  });
  drag.install();
  // 直近に適用された lwc 操作可否（handleScroll）。未適用なら既定 true。
  const interactionOn = () => {
    for (let i = applied.length - 1; i >= 0; i -= 1) {
      if (applied[i] && 'handleScroll' in applied[i]) { return applied[i].handleScroll; }
    }
    return true;
  };
  return {
    container, renderer, drag, picker, interactionOn, levels: () => levels,
  };
}

test('TC-VB06 アーム中は線近傍を押しても水準が動かない（入力先は常に一意・R-P1）', () => {
  // Arrange: 掴める線は y=200。ピッカーは別の欄（利確）をアーム中。
  const ctx = bootReal();
  ctx.picker.arm('take');
  const before = ctx.levels().stopPrice;
  // Act: 線の真上を押して動かす（アームしていなければ掴めてしまう座標）。
  ctx.container.fire('pointerdown', { button: 0, clientY: 200 });
  ctx.container.fire('pointermove', { buttons: 1, clientY: 240 });
  ctx.container.fire('pointerup', {});
  // Assert
  assert.equal(ctx.drag.isDragging(), false, 'アーム中に掴んでしまっている');
  assert.equal(ctx.levels().stopPrice, before, '入力先でない損切り線が動いた（R-P1 の破綻）');
});

test('TC-VB07 drag が終わってもアーム中は lwc 操作が復帰しない（抑止の合成）', () => {
  // Arrange
  const ctx = bootReal();
  ctx.picker.arm('stop');
  assert.equal(ctx.interactionOn(), false, 'アームで lwc 操作が落ちる');
  // Act: drag を成立させて終わらせる（アーム中は掴めないので述語を外した状態で観測するため、
  //   ここでは drag 単体の抑止・解除を直接動かす）。
  const release = ctx.renderer.suppressInteraction();
  release();
  // Assert: drag 側が解除しても、ピッカーの抑止が残っている間は復帰しない。
  assert.equal(ctx.interactionOn(), false, '他者の解除でアーム中の抑止まで解けている');
});

test('TC-VB08 抑止を持つ者が全員解除して初めて復帰する（片方だけでは戻らない）', () => {
  // Arrange
  const ctx = bootReal();
  const a = ctx.renderer.suppressInteraction();
  ctx.picker.arm('stop');
  assert.equal(ctx.interactionOn(), false);
  // Act / Assert: 片方ずつ解除する。
  a();
  assert.equal(ctx.interactionOn(), false, '1 人残っているのに復帰した');
  ctx.picker.disarm();
  assert.equal(ctx.interactionOn(), true, '全員解除しても復帰しない');
});
