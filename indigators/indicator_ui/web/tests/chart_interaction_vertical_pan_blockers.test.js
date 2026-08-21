// ChartInteractionController の縦パンブロッカー**合成**の検証（ISSUE-368 スライス 3）。
//
// 設計入力: 設計書 出力 3 スライス 3 ／ §4-C。
// 由来（実測された破綻型）: 縦パンを外部条件で止める口は `isVerticalPanBlocked` の **単数スロット**
//   しか無く、リプレイ root が既に使用中（`composition_roots_share_wiring.test.js:98` が固定）。
//   水準線 drag（スライス 4）が同じ口を要求するため、単純に流用すると
//   リプレイ側の「MP リプレイ中は縦パンしない」を上書きして壊す。単数スロット競合は
//   `setCandleObserver` / `setTfPeriodHoverHandler` で既に起きている再発型なので、
//   **合成（複数ブロッカーの OR）**にして構造的に潰す。
// 観点:
//   - 未注入・未登録は従来と完全に同一（常にブロックなし）
//   - constructor 注入（既存 API）と登録ブロッカーの **OR**
//   - install 後に登録しても効く（drag の結線は controller 生成より後）
//   - 解除すると元に戻る（drag 終了で復元できる）
//   - 判定は pointerdown のたびに評価される（登録時点の値を焼き付けない）
// 構造: Arrange-Act-Assert。container / renderer は Fake（DOM 非依存）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartInteractionController } from '../js/adapter/front/chart_interaction_controller.js';

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
    refreshPaneLegendIfGeometryChanged: () => false,
    resetPriceZoom() {},
    setPaneHeight() {},
  };
}

function build({ isVerticalPanBlocked } = {}) {
  const container = fakeContainer();
  const renderer = fakeRenderer();
  const ctl = new ChartInteractionController({
    container,
    renderer,
    getController: () => ({}),
    updatePaneHeight: () => {},
    isVerticalPanBlocked,
  });
  ctl.install();
  return { container, renderer, ctl };
}

// 「掴む → 動かす」を 1 回行い、価格パンが起きたかを返す。
function dragOnce({ container, renderer }) {
  const before = renderer.calls.pan.length;
  container.fire('pointerdown', { button: 0, clientX: 10, clientY: 100 });
  container.fire('pointermove', { buttons: 1, clientX: 10, clientY: 130 });
  container.fire('pointerup', {});
  return renderer.calls.pan.length > before;
}

test('未注入・未登録なら従来どおり縦パンする（既定の挙動を変えない）', () => {
  // Arrange
  const ctx = build();
  // Act / Assert
  assert.equal(dragOnce(ctx), true);
});

test('登録ブロッカー 2 個のどちらかが真なら縦パンしない（OR 合成）', () => {
  // Arrange
  const ctx = build();
  let a = false;
  let b = false;
  ctx.ctl.addVerticalPanBlocker(() => a);
  ctx.ctl.addVerticalPanBlocker(() => b);

  // Act / Assert — 真理値表（デシジョンテーブル）
  assert.equal(dragOnce(ctx), true, 'F,F → パンする');
  a = true; b = false;
  assert.equal(dragOnce(ctx), false, 'T,F → パンしない');
  a = false; b = true;
  assert.equal(dragOnce(ctx), false, 'F,T → パンしない');
  a = true; b = true;
  assert.equal(dragOnce(ctx), false, 'T,T → パンしない');
  a = false; b = false;
  assert.equal(dragOnce(ctx), true, '両方戻れば再びパンする（状態を焼き付けない）');
});

test('constructor 注入（既存 API）と登録ブロッカーも OR で合成される', () => {
  // Arrange — リプレイ root が使っている単数スロットは温存したまま追加できること
  let injected = false;
  const ctx = build({ isVerticalPanBlocked: () => injected });
  let added = false;
  ctx.ctl.addVerticalPanBlocker(() => added);

  // Act / Assert
  assert.equal(dragOnce(ctx), true);
  injected = true;
  assert.equal(dragOnce(ctx), false, '既存注入だけでも止まる（リプレイ挙動の不変）');
  injected = false; added = true;
  assert.equal(dragOnce(ctx), false, '追加登録だけでも止まる');
});

test('install 後に登録しても効く（drag の結線は controller 生成より後）', () => {
  // Arrange
  const ctx = build();
  assert.equal(dragOnce(ctx), true);
  // Act
  ctx.ctl.addVerticalPanBlocker(() => true);
  // Assert
  assert.equal(dragOnce(ctx), false);
});

test('登録の戻り値で解除でき、解除後は元の挙動に戻る（drag 終了時の復元）', () => {
  // Arrange
  const ctx = build();
  const off = ctx.ctl.addVerticalPanBlocker(() => true);
  assert.equal(dragOnce(ctx), false);
  // Act
  off();
  // Assert
  assert.equal(dragOnce(ctx), true);
  off();  // 二重解除は無害
  assert.equal(dragOnce(ctx), true);
});

test('関数でない登録は無視する（未定義を真として扱わない）', () => {
  // Arrange
  const ctx = build();
  // Act
  const off = ctx.ctl.addVerticalPanBlocker(null);
  // Assert
  assert.equal(typeof off, 'function', '解除関数は常に返す（呼び出し側に分岐を作らせない）');
  assert.equal(dragOnce(ctx), true);
});
