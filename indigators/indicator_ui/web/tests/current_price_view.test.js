// current_price_view.js（CurrentPriceView）の仕様検証。
//
// 設計入力: 左上凡例スタック先頭の現在値大型表示（48px は CSS 規定・ユーザー指示 2026-07-23）。
//   - render(value): fmtValue で整形した現在値を #current-price へ描画する。
//   - カラースキーム: 前回表示値との比較で is-up / is-down を付け替え、同値は方向を据え置く。
//     初回・値なしは方向クラス無し（中立）。
//   - 値 null/非有限・対象要素不在でも安全（クラッシュしない・空表示）。
// 構造: Arrange-Act-Assert（AAA）。実 DOM 非依存（crosshair_readout_view.test.js の fake 流儀）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CurrentPriceView } from '../js/adapter/front/current_price_view.js';

function fakeDoc(rootId = 'current-price') {
  const root = { textContent: '', className: '' };
  return {
    _root: root,
    getElementById(id) { return id === rootId ? root : null; },
  };
}

test('render は現在値を整形して描画する（初回は方向クラス無し）', () => {
  const doc = fakeDoc();
  const view = new CurrentPriceView({ document: doc, elementId: 'current-price' });
  view.render(65477.27);
  assert.equal(doc._root.textContent, Number(65477.27).toLocaleString(undefined, { maximumFractionDigits: 3 }));
  assert.equal(doc._root.className, '', '初回は方向未確定＝中立（クラス無し）');
});

test('上げで is-up・下げで is-down を付け替える', () => {
  const doc = fakeDoc();
  const view = new CurrentPriceView({ document: doc, elementId: 'current-price' });
  view.render(100);
  view.render(101);
  assert.equal(doc._root.className, 'is-up');
  view.render(99.5);
  assert.equal(doc._root.className, 'is-down');
});

test('同値は直前の方向を据え置く', () => {
  const doc = fakeDoc();
  const view = new CurrentPriceView({ document: doc, elementId: 'current-price' });
  view.render(100);
  view.render(101);
  view.render(101);
  assert.equal(doc._root.className, 'is-up', '同値でも上げ色を維持する');
});

test('null/非有限は空表示へ戻し方向状態もリセットする', () => {
  const doc = fakeDoc();
  const view = new CurrentPriceView({ document: doc, elementId: 'current-price' });
  view.render(100);
  view.render(101);
  view.render(null);
  assert.equal(doc._root.textContent, '');
  assert.equal(doc._root.className, '');
  // リセット後の再表示は初回扱い（前回値比較を持ち越さない）。
  view.render(100.5);
  assert.equal(doc._root.className, '', 'リセット後の初回は中立');
});

test('対象要素不在・document 不在でもクラッシュしない', () => {
  const view = new CurrentPriceView({ document: fakeDoc('other-id'), elementId: 'current-price' });
  assert.doesNotThrow(() => view.render(100));
  const noDoc = new CurrentPriceView({ document: null, elementId: 'current-price' });
  assert.doesNotThrow(() => noDoc.render(100));
});
