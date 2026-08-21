// installSharedUi が縦パンブロッカーの登録口を配るこ­との検証（ISSUE-368 スライス 3）。
//
// 設計入力: 設計書 出力 3 スライス 3（`chart_app_wiring.js` で `isVerticalPanBlocked` を合成可能にする）。
// 由来: `ChartInteractionController` は共有配線が `new` して `install()` した後に**捨てて**おり
//   （`installSharedUi` 内・`composition_roots_share_wiring.test.js` の SHARED_OWNED が
//   root 側での再 new を禁じている）、外から追加ブロッカーを登録する手段が無い。
//   水準線 drag（スライス 4）は controller 生成より後に結線されるため、登録口を戻り値で配る。
// 観点: 戻り値の `registerVerticalPanBlocker` が **実際にその controller の縦パンを止める**こと
//   （関数が生えているだけでは、どこにも繋がっていない偽の口を見逃す）。
// 構造: Arrange-Act-Assert。DOM は使わない（doc=null＝各 install の DOM 不在防御に委ねる）。
//   fake にしか無い API に依存しない（ISSUE-425 類型の回避）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { installSharedUi } from '../js/adapter/front/chart_app_wiring.js';

// container Fake（pointer 系だけ記録する。addEventListener は実 DOM と同じ 3 引数を受ける）。
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
    isLatestBarVisible: () => true,
    scrollToLatest() {},
    barInfoAt: () => null,
  };
}

function boot() {
  const container = fakeContainer();
  const renderer = fakeRenderer();
  const shared = installSharedUi({
    container,
    renderer,
    doc: null,                     // DOM 不在＝各 View の install は no-op（既存の防御）
    getController: () => null,
    updatePaneHeight: () => {},
  });
  return { container, renderer, shared };
}

function dragOnce({ container, renderer }) {
  const before = renderer.calls.pan.length;
  container.fire('pointerdown', { button: 0, clientX: 10, clientY: 100 });
  container.fire('pointermove', { buttons: 1, clientX: 10, clientY: 130 });
  container.fire('pointerup', {});
  return renderer.calls.pan.length > before;
}

test('installSharedUi の戻り値が縦パンブロッカーの登録口を配る', () => {
  // Arrange / Act
  const { shared } = boot();
  // Assert
  assert.equal(typeof shared.registerVerticalPanBlocker, 'function');
});

test('配られた登録口は installSharedUi が作った controller の縦パンを実際に止める', () => {
  // Arrange
  const ctx = boot();
  assert.equal(dragOnce(ctx), true, '登録前は従来どおり縦パンする');
  let blocked = false;
  // Act
  const off = ctx.shared.registerVerticalPanBlocker(() => blocked);
  blocked = true;
  // Assert
  assert.equal(dragOnce(ctx), false, '登録した述語が真なら止まる（口が繋がっている）');
  blocked = false;
  assert.equal(dragOnce(ctx), true, '偽に戻れば再開する');
  blocked = true;
  off();
  assert.equal(dragOnce(ctx), true, '解除すると述語が真でも止まらない');
});
