// position_sizing_context_items.js（右クリックメニューの価格設定 3 項目・ISSUE-368 スライス 8-c）のテスト。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   「追加要件裁定 R-P3」（チャート上の contextmenu で「この価格を損切りに設定／建値に追加／
//    利確に設定」。価格解決は R-P2 と**同一のスナップ規則**を使う＝解決器の単一ソース）、
//   「ピッカー経路の実測検証」3（既存 `ChartContextMenu` へ**項目注入**する。自前 new は
//    二重リスナーになるため禁止・共有配線への無条件追加は replay を汚染するため禁止）、
//   同 7 裁定（下段ペインのクリックは確定させない）。
//
// 責務: 「座標 → 価格解決（8-c/8-d 共通の 1 本）→ 注入されたハンドラ呼び出し」の 3 段だけ。
//   価格の作り方も水準の持ち方も知らない（copy_bar_info_item.js と同じ項目の作法）。
// 構造: Arrange-Act-Assert（AAA）。解決器は注入 fake（座標変換の再検定はしない）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { createPriceContextItems } from '../js/adapter/front/position_sizing_context_items.js';
import { OTHER_PANE } from '../js/adapter/front/price_pick_resolver.js';

function build({ resolved = { price: 58650, snapped: false, candidate: null, reason: null } } = {}) {
  const calls = [];
  const toasts = [];
  const items = createPriceContextItems({
    resolvePrice: (ctx) => { calls.push(['resolve', ctx]); return resolved; },
    onSetStop: (p) => calls.push(['stop', p]),
    onAddEntry: (p) => calls.push(['entry', p]),
    onSetTake: (p) => calls.push(['take', p]),
    toast: { show: (m) => toasts.push(m) },
  });
  return { items, calls, toasts };
}

test('TC-CI01 項目は 3 つ・文言は裁定どおり（損切り／建値に追加／利確）', () => {
  // Arrange / Act
  const { items } = build();
  // Assert
  assert.deepEqual(items.map((i) => i.label), [
    'この価格を損切りに設定',
    'この価格を建値に追加',
    'この価格を利確に設定',
  ]);
});

test('TC-CI02 各項目は解決した価格を対応するハンドラへ渡す（R-P3）', () => {
  // Arrange
  const { items, calls } = build();
  // Act: メニューは context（コンテナ基準の x/y）を渡す。
  items[0].onSelect({ x: 120, y: 200 });
  items[1].onSelect({ x: 120, y: 200 });
  items[2].onSelect({ x: 120, y: 200 });
  // Assert
  assert.deepEqual(calls.filter(([k]) => k !== 'resolve'), [
    ['stop', 58650], ['entry', 58650], ['take', 58650],
  ]);
});

test('TC-CI03 価格解決は注入された 1 本に委ねる（項目側に座標変換を持たない）', () => {
  // Arrange
  const { items, calls } = build();
  // Act
  items[0].onSelect({ x: 7, y: 9 });
  // Assert
  assert.deepEqual(calls[0], ['resolve', { x: 7, y: 9 }]);
});

test('TC-CI04 下段ペインでは確定せず案内を出す（裁定 2026-08-20・価格を作らない）', () => {
  // Arrange
  const { items, calls, toasts } = build({
    resolved: { price: null, snapped: false, candidate: null, reason: OTHER_PANE },
  });
  // Act
  items[0].onSelect({ x: 120, y: 350 });
  // Assert
  assert.deepEqual(calls.filter(([k]) => k !== 'resolve'), [], '下段ペインでハンドラを呼んではならない');
  assert.equal(toasts.length, 1);
  assert.match(toasts[0], /価格チャート上で指定/, '案内文言は裁定どおり');
});

test('TC-CI05 価格が取れない座標では確定せず、成功したふりをしない', () => {
  // Arrange
  const { items, calls, toasts } = build({
    resolved: { price: null, snapped: false, candidate: null, reason: 'no_price' },
  });
  // Act
  items[1].onSelect({ x: 999, y: 10 });
  // Assert
  assert.deepEqual(calls.filter(([k]) => k !== 'resolve'), []);
  assert.equal(toasts.length, 1, '無言で何も起きない状態にしない');
});

test('TC-CI06 ハンドラ未注入でも例外にならない（配線前の押下）', () => {
  // Arrange
  const items = createPriceContextItems({
    resolvePrice: () => ({ price: 100, snapped: false, candidate: null, reason: null }),
  });
  // Act / Assert
  assert.doesNotThrow(() => items.forEach((i) => i.onSelect({ x: 1, y: 1 })));
});
