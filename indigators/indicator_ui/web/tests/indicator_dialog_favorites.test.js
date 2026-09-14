// ISSUE-220: 「★ お気に入り」が常に 0 件になる不具合の回帰検証。
//
// 原因: サイドバーの data-category="__favorites__"（カテゴリ名ではなくセンチネル）を
//   category チャネルへも入れていたため、facade の `d.category.nameKey !== category` が
//   全件真になり全指標が除外されていた。
// 構造: Arrange-Act-Assert（AAA）。DOM は最小の Fake（addEventListener/dataset のみ）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { FAVORITES_SENTINEL, IndicatorDialogController } from '../js/adapter/front/indicator_dialog_controller.js';
import { listForView } from '../js/usecase/facade.js';
import { list } from '../js/usecase/catalog.js';

function fakeButton(category) {
  const handlers = [];
  return {
    dataset: { category },
    classList: { add() {}, remove() {} },
    addEventListener(_type, fn) { handlers.push(fn); },
    click() { for (const fn of handlers) fn(); },
  };
}

function newController(cats) {
  const captured = { renders: 0 };
  const host = {
    _setActive() {},
    _renderDialogList() { captured.renders += 1; },
  };
  const controller = new IndicatorDialogController(host);
  controller.bindElements({ cats, tabs: [], search: null });
  return { controller, captured };
}

test('ISSUE-220 センチネルは category チャネルへ入らない（お気に入りが 0 件にならない）', () => {
  const favBtn = fakeButton(FAVORITES_SENTINEL);
  const { controller } = newController([favBtn]);

  favBtn.click();

  assert.equal(controller._filter.favoriteOnly, true, 'favoriteOnly が立つこと');
  assert.equal(controller._filter.category, null, 'category はカテゴリ名のみを運ぶ');

  // 実際に facade へ通して 0 件にならないことを確認する（単体の filter 値だけでは不足）。
  const ids = list().slice(0, 2).map((d) => d.id);
  const rows = listForView({ ...controller._filter, tab: 'indicator', favorites: ids });
  assert.ok(rows.length > 0, 'お気に入りが 0 件になっている');
  assert.deepEqual(rows.map((d) => d.id).sort(), [...ids].sort());
});

test('ISSUE-220 通常のカテゴリボタンは従来どおり category を運ぶ（非波及）', () => {
  const catBtn = fakeButton('cat.technical');
  const { controller } = newController([catBtn]);

  catBtn.click();

  assert.equal(controller._filter.category, 'cat.technical');
  assert.equal(controller._filter.favoriteOnly, false);
  const rows = listForView({ ...controller._filter, tab: 'indicator' });
  assert.ok(rows.length > 0);
  assert.ok(rows.every((d) => d.category.nameKey === 'cat.technical'));
});

test('ISSUE-220 「すべて」（data-category 空）は全件を返す（非波及）', () => {
  const allBtn = fakeButton('');
  const { controller } = newController([allBtn]);

  allBtn.click();

  assert.equal(controller._filter.category, null);
  assert.equal(controller._filter.favoriteOnly, false);
  assert.equal(listForView({ ...controller._filter, tab: 'indicator' }).length,
    list().filter((d) => d.tab === 'indicator').length);
});
