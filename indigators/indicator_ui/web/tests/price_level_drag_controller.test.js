// price_level_drag_controller.js（水準線ドラッグ）の仕様検証（ISSUE-368 スライス 4）。
//
// 設計入力: 設計書 §6「Adapter: PriceLevelDragController」／出力 3 スライス 4／§4-C。
// 観点:
//   - 「掴む → 動かす → 離す」で PriceLevels（domain・E-02）が更新される
//   - drag 中は renderer.setUserInteraction(false) **と** 縦パンブロッカー ON の**両方**。
//     終了で両方復元（§4-C: setUserInteraction だけでは不十分＝アプリ自前の縦パンは
//     lwc オプションを見ずに priceScale.setVisibleRange を直叩きするため）
//   - 掴めない位置では何も奪わない（通常の縦パンが生きる）
//   - 範囲外価格（priceAtCoordinate が null）で例外を投げず、水準も更新しない
// 実 DOM 意味論（ISSUE-425 類型の回避）:
//   - fake container は addEventListener の capture 指定を解釈し、**capture 段階を先に**
//     発火させる（対象が子孫要素のときの実 DOM 伝播順）。登録順で発火する fake にすると
//     「実 UI では縦パンが先に始まる」破綻を見逃す。
//   - 座標は clientY − getBoundingClientRect().top（rect.top≠0 で検証する）。
//   - PriceLevels は **本物の domain 実体**を使う（fake にしか無い API に依存しない）。
// 構造: Arrange-Act-Assert。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { PriceLevelDragController } from '../js/adapter/front/price_level_drag_controller.js';
import { PriceLevelLinesPrimitive } from '../js/adapter/front/price_level_lines_primitive.js';
import { ChartInteractionController } from '../js/adapter/front/chart_interaction_controller.js';
import { createPriceLevels } from '../js/domain/price_levels.js';

const RECT_TOP = 20;

// 実 DOM の伝播順を模す container Fake（capture 段階 → bubble 段階）。
function fakeContainer() {
  const listeners = [];
  return {
    addEventListener(type, fn, opts) {
      const capture = opts === true || (!!opts && opts.capture === true);
      listeners.push({ type, fn, capture });
    },
    getBoundingClientRect() { return { left: 0, top: RECT_TOP }; },
    dispatch(type, ev) {
      const forType = listeners.filter((l) => l.type === type);
      for (const l of forType.filter((l) => l.capture)) { l.fn(ev); }
      for (const l of forType.filter((l) => !l.capture)) { l.fn(ev); }
    },
    isCapture(type) { return listeners.filter((l) => l.type === type).map((l) => l.capture); },
  };
}

// 価格 ⇄ y の一次写像（58700 が y=100・10pt で 1px）。
const Y_OF = (price) => 100 + (58700 - price) / 10;
const PRICE_AT = (y) => 58700 - (y - 100) * 10;

function fakeRenderer({ priceAtCoordinate = PRICE_AT } = {}) {
  const calls = { userInteraction: [], pan: [] };
  return {
    calls,
    priceAtCoordinate,
    setUserInteraction: (v) => { calls.userInteraction.push(v); },
    // 抑止は登録方式（ChartRenderer.suppressInteraction）。実物と同じく「最初の抑止で落ち、
    //   最後の解除で戻る」遷移を同じ配列へ記録するため、観測できる系列は従来と同一である。
    suppressInteraction() {
      calls.userInteraction.push(false);
      return () => { calls.userInteraction.push(true); };
    },
    panPriceByPixels: (dy) => { calls.pan.push(dy); },
    handlePriceWheel: () => false,
    isOverPriceAxis: () => false,
    // ISSUE-440: 幾何が動いたら凡例を引き直す面。ChartInteractionController が pointermove /
    //   pointerup で呼ぶので、renderer ダブルも契約を満たす（部分実装を通さない＝fail-close）。
    refreshPaneLegendIfGeometryChanged: () => false,
    resetPriceZoom() {},
    setPaneHeight() {},
  };
}

const LEVELS = createPriceLevels({
  direction: 'long',
  entryPrices: [58700, 59700],
  stopPrice: 58340,
  takePrice: 61500,
});

// primitive を attach し、水準を描いて y 表を確定させる（掴み判定の根拠）。
function preparedPrimitive(losscutPrice = 57000) {
  const primitive = new PriceLevelLinesPrimitive();
  primitive.attached({
    chart: { timeScale: () => ({ width: () => 800 }) },
    series: { priceToCoordinate: (p) => Y_OF(p) },
    requestUpdate: () => {},
  });
  primitive.setLevels({
    direction: LEVELS.direction,
    entryPrices: [...LEVELS.entryPrices],
    stopPrice: LEVELS.stopPrice,
    takePrice: LEVELS.takePrice,
    losscutPrice,
  });
  primitive.draw({ useBitmapCoordinateSpace: (fn) => fn({ context: stubCtx() }) });
  return primitive;
}

function stubCtx() {
  return {
    save() {}, restore() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, setLineDash() {},
    set strokeStyle(_v) {}, get strokeStyle() { return null; },
    set lineWidth(_v) {}, get lineWidth() { return null; },
  };
}

function build({ renderer, withInteractionController = false } = {}) {
  const container = fakeContainer();
  const r = renderer || fakeRenderer();
  const primitive = preparedPrimitive();
  let levels = LEVELS;
  const changes = [];
  let blocked = false;

  // 本番の結線順（共有配線が先・drag は後）を再現する。
  let interaction = null;
  if (withInteractionController) {
    interaction = new ChartInteractionController({
      container, renderer: r, getController: () => ({}), updatePaneHeight: () => {},
    });
    interaction.install();
  }

  const ctl = new PriceLevelDragController({
    container,
    renderer: r,
    primitive,
    getLevels: () => levels,
    onLevelsChange: (next) => { levels = next; changes.push(next); },
    registerVerticalPanBlocker: (predicate) => {
      const wrapped = () => { blocked = predicate(); return blocked; };
      if (interaction) { return interaction.addVerticalPanBlocker(wrapped); }
      // controller 非同席のときも述語を評価できるようにフックだけ保持する。
      return (() => { const p = predicate; return () => { blocked = p(); }; })();
    },
    grabTolerancePx: 6,
  });
  ctl.install();
  return {
    container, renderer: r, primitive, ctl, changes, interaction,
    levels: () => levels,
    blockedNow: () => ctl.isDragging() || ctl.hoveredHandle() != null,
  };
}

// y（コンテナ基準）→ clientY
const clientYOf = (y) => y + RECT_TOP;

test('pointerdown は capture 段階で登録する（実 DOM で共有配線の縦パンより先に走る）', () => {
  // Arrange / Act
  const { container } = build();
  // Assert
  assert.ok(container.isCapture('pointerdown').includes(true),
    'capture 指定の pointerdown が無い＝実 DOM では縦パンが先に始まりうる');
});

test('掴む → 動かす → 離す で損切り価格が更新される', () => {
  // Arrange
  const ctx = build();
  const stopY = Y_OF(58340);
  // Act
  ctx.container.dispatch('pointerdown', { button: 0, clientY: clientYOf(stopY) });
  ctx.container.dispatch('pointermove', { buttons: 1, clientY: clientYOf(stopY + 12) });
  ctx.container.dispatch('pointerup', {});
  // Assert
  assert.equal(ctx.changes.length, 1);
  assert.equal(ctx.levels().stopPrice, PRICE_AT(stopY + 12));
  assert.deepEqual([...ctx.levels().entryPrices], [58700, 59700], '他の水準は動かない');
});

test('建値は番号どおりに更新される（#2 を掴んだら #2 だけ動く）', () => {
  // Arrange
  const ctx = build();
  const entry2Y = Y_OF(59700);
  // Act
  ctx.container.dispatch('pointerdown', { button: 0, clientY: clientYOf(entry2Y) });
  ctx.container.dispatch('pointermove', { buttons: 1, clientY: clientYOf(entry2Y - 20) });
  ctx.container.dispatch('pointerup', {});
  // Assert
  assert.deepEqual([...ctx.levels().entryPrices], [58700, PRICE_AT(entry2Y - 20)]);
});

test('利確も掴んで動かせる', () => {
  // Arrange
  const ctx = build();
  const takeY = Y_OF(61500);
  // Act
  ctx.container.dispatch('pointerdown', { button: 0, clientY: clientYOf(takeY) });
  ctx.container.dispatch('pointermove', { buttons: 1, clientY: clientYOf(takeY + 5) });
  ctx.container.dispatch('pointerup', {});
  // Assert
  assert.equal(ctx.levels().takePrice, PRICE_AT(takeY + 5));
});

test('drag 中は setUserInteraction(false) と縦パンブロッカーが両方立ち、終了で両方戻る', () => {
  // Arrange
  const ctx = build();
  const stopY = Y_OF(58340);
  // Act / Assert
  ctx.container.dispatch('pointerdown', { button: 0, clientY: clientYOf(stopY) });
  assert.deepEqual(ctx.renderer.calls.userInteraction, [false]);
  assert.equal(ctx.ctl.isDragging(), true);
  ctx.container.dispatch('pointermove', { buttons: 1, clientY: clientYOf(stopY + 4) });
  ctx.container.dispatch('pointerup', {});
  assert.deepEqual(ctx.renderer.calls.userInteraction, [false, true], '終了で lwc 操作を復帰');
  assert.equal(ctx.ctl.isDragging(), false);
});

test('掴んでいる間は共有配線の縦パンが起きない（結線順が後でも効く）', () => {
  // Arrange — 共有配線を先に install した本番同順
  const ctx = build({ withInteractionController: true });
  const stopY = Y_OF(58340);
  // Act
  ctx.container.dispatch('pointerdown', { button: 0, clientY: clientYOf(stopY) });
  ctx.container.dispatch('pointermove', { buttons: 1, clientY: clientYOf(stopY + 30) });
  // Assert
  assert.deepEqual(ctx.renderer.calls.pan, [], 'チャートが縦にずれていない');
  assert.equal(ctx.changes.length, 1, '水準は動いている');
});

test('線から離れた位置では何も奪わない（通常の縦パンが生きる）', () => {
  // Arrange
  const ctx = build({ withInteractionController: true });
  const farY = Y_OF(58340) + 60;
  // Act
  ctx.container.dispatch('pointerdown', { button: 0, clientY: clientYOf(farY) });
  ctx.container.dispatch('pointermove', { buttons: 1, clientY: clientYOf(farY + 25) });
  // Assert
  assert.equal(ctx.ctl.isDragging(), false);
  assert.deepEqual(ctx.renderer.calls.userInteraction, [], 'lwc 操作を触っていない');
  assert.deepEqual(ctx.renderer.calls.pan, [25], '通常の縦パンが起きる');
  assert.equal(ctx.changes.length, 0);
});

test('ロスカット線は掴めない（読み取り専用）', () => {
  // Arrange
  const ctx = build({ withInteractionController: true });
  const lcY = Y_OF(57000);
  // Act
  ctx.container.dispatch('pointerdown', { button: 0, clientY: clientYOf(lcY) });
  // Assert
  assert.equal(ctx.ctl.isDragging(), false);
  assert.equal(ctx.changes.length, 0);
});

test('左ボタン以外では掴まない', () => {
  // Arrange
  const ctx = build();
  const stopY = Y_OF(58340);
  // Act
  ctx.container.dispatch('pointerdown', { button: 2, clientY: clientYOf(stopY) });
  // Assert
  assert.equal(ctx.ctl.isDragging(), false);
});

test('ボタンを離した状態の pointermove は drag を継続しない', () => {
  // Arrange
  const ctx = build();
  const stopY = Y_OF(58340);
  ctx.container.dispatch('pointerdown', { button: 0, clientY: clientYOf(stopY) });
  // Act
  ctx.container.dispatch('pointermove', { buttons: 0, clientY: clientYOf(stopY + 10) });
  // Assert
  assert.equal(ctx.ctl.isDragging(), false);
  assert.deepEqual(ctx.renderer.calls.userInteraction, [false, true], '復元も行う');
  assert.equal(ctx.changes.length, 0);
});

test('pointerleave / pointercancel でも終了して復元する', () => {
  for (const endType of ['pointerleave', 'pointercancel']) {
    // Arrange
    const ctx = build();
    const stopY = Y_OF(58340);
    ctx.container.dispatch('pointerdown', { button: 0, clientY: clientYOf(stopY) });
    // Act
    ctx.container.dispatch(endType, {});
    // Assert
    assert.equal(ctx.ctl.isDragging(), false, endType);
    assert.deepEqual(ctx.renderer.calls.userInteraction, [false, true], endType);
  }
});

test('範囲外価格（priceAtCoordinate が null）では水準を更新せず例外も投げない', () => {
  // Arrange
  const ctx = build({ renderer: fakeRenderer({ priceAtCoordinate: () => null }) });
  const stopY = Y_OF(58340);
  // Act / Assert
  assert.doesNotThrow(() => {
    ctx.container.dispatch('pointerdown', { button: 0, clientY: clientYOf(stopY) });
    ctx.container.dispatch('pointermove', { buttons: 1, clientY: clientYOf(stopY + 10) });
    ctx.container.dispatch('pointerup', {});
  });
  assert.equal(ctx.changes.length, 0);
  assert.equal(ctx.levels().stopPrice, 58340);
});

test('掴み位置の近傍判定はコンテナ矩形基準（rect.top を引く）', () => {
  // Arrange — clientY から rect.top を引かない実装は 20px ずれて掴めない
  const ctx = build();
  const stopY = Y_OF(58340);
  // Act
  ctx.container.dispatch('pointerdown', { button: 0, clientY: clientYOf(stopY) });
  // Assert
  assert.equal(ctx.ctl.isDragging(), true);
});

test('container 不在／addEventListener 非対応でも install は例外を投げない（SSR 防御）', () => {
  // Arrange / Act / Assert
  assert.doesNotThrow(() => {
    new PriceLevelDragController({ container: null, renderer: fakeRenderer() }).install();
    new PriceLevelDragController({ container: {}, renderer: fakeRenderer() }).install();
  });
});

// ---- 防御分岐（自己レビューのカバレッジ計測で未検定と判明した経路）----
//   これら 4 分岐は先に実装が書かれていた（テスト先行に反した＝TDD の順序違反）。
//   検出は node --experimental-test-coverage の未到達行による。撤去せず検定を足したのは、
//   同種の防御が本コードベースの既定の流儀だからである
//   （chart_interaction_controller.install の no-op・installChartToolbar の null 返し・
//   primitive の attach 前 no-op）。ここだけ流儀を変えると読み手の予測を裏切る。

test('掴んでいないときの pointerup は lwc 操作を触らない（無関係な操作を奪わない）', () => {
  // Arrange
  const ctx = build();
  // Act — 線から離れた場所で押して離す
  ctx.container.dispatch('pointerdown', { button: 0, clientY: clientYOf(Y_OF(58340) + 80) });
  ctx.container.dispatch('pointerup', {});
  // Assert
  assert.deepEqual(ctx.renderer.calls.userInteraction, [],
    'setUserInteraction(true) を余計に呼ばない（他の機能が落とした抑止を勝手に戻さない）');
});

test('primitive 未注入では掴み判定を行わない（配線前でも例外を投げない）', () => {
  // Arrange
  const container = fakeContainer();
  const renderer = fakeRenderer();
  const ctl = new PriceLevelDragController({ container, renderer, primitive: null });
  ctl.install();
  // Act / Assert
  assert.doesNotThrow(() => container.dispatch('pointerdown', { button: 0, clientY: 100 }));
  assert.equal(ctl.isDragging(), false);
});

test('renderer が座標変換を持たない場合は水準を更新しない', () => {
  // Arrange
  const ctx = build({ renderer: { setUserInteraction() {}, panPriceByPixels() {} } });
  const stopY = Y_OF(58340);
  // Act
  ctx.container.dispatch('pointerdown', { button: 0, clientY: clientYOf(stopY) });
  ctx.container.dispatch('pointermove', { buttons: 1, clientY: clientYOf(stopY + 10) });
  // Assert
  assert.equal(ctx.changes.length, 0);
});

test('水準が未設定（getLevels が null）でも掴み操作で落ちない', () => {
  // Arrange
  const container = fakeContainer();
  const renderer = fakeRenderer();
  const ctl = new PriceLevelDragController({
    container, renderer, primitive: preparedPrimitive(), getLevels: () => null,
  });
  ctl.install();
  const stopY = Y_OF(58340);
  // Act / Assert
  assert.doesNotThrow(() => {
    container.dispatch('pointerdown', { button: 0, clientY: clientYOf(stopY) });
    container.dispatch('pointermove', { buttons: 1, clientY: clientYOf(stopY + 10) });
    container.dispatch('pointerup', {});
  });
});
