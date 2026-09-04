// 右クリックメニューの「解除」項目（ISSUE-435 実装 1）の仕様検証。
//
// 設計入力（唯一の仕様源）: ISSUE.md ISSUE-435「抜本的解決 1」＋依頼者指示（2026-08-21）
//   「右クリックメニューに解除項目を出す。**その水準が設定済みのときだけ**出す
//    （未設定なら項目自体を出さない）」「建値は K 本のうち設定済みの本を解除できる。
//    どの本を解除するかが利用者に分かる形にする」「既存 3 項目の文言・挙動は 1 バイトも変えない」。
//
// 観点:
//   - 出す / 出さないの判定は**いまの水準**だけで決まる（設定済み＝有限な価格）
//   - 建値は本ごとに独立（設定済みの本だけが、その本と分かる名前で出る）
//   - 解除は**座標に依存しない**（価格解決を呼ばない・下段ペインでも効く）
//   - 既存 3 項目（文言・順序・挙動）が動いていない
// 構造: Arrange-Act-Assert。水準は値で注入（domain も協働子も要らない）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { createPriceContextItems } from '../js/adapter/front/position_sizing_context_items.js';

const SET_ITEMS = ['この価格を損切りに設定', 'この価格を建値に追加', 'この価格を利確に設定'];

// 既定の水準は「K=3 本すべて未設定・損切りも利確も未設定」（`defaultLevels()` と同じ形）。
const EMPTY = Object.freeze({ entryPrices: [null, null, null], stopPrice: null, takePrice: null });

function build({ levels = EMPTY } = {}) {
  const calls = [];
  const items = createPriceContextItems({
    resolvePrice: (ctx) => { calls.push(['resolve', ctx]); return { price: 58650, reason: null }; },
    onSetStop: (p) => calls.push(['stop', p]),
    onAddEntry: (p) => calls.push(['entry', p]),
    onSetTake: (p) => calls.push(['take', p]),
    onClear: (t) => calls.push(['clear', t]),
    getLevels: () => levels,
  });
  return { items, calls, labels: items.map((i) => i.label) };
}

test('TC-CL01 未設定なら解除項目は 1 つも出ない（既存 3 項目のまま）', () => {
  // Arrange / Act
  const { labels } = build();
  // Assert
  assert.deepEqual(labels, SET_ITEMS);
});

test('TC-CL02 損切りが設定済みなら「損切りを解除」が出る', () => {
  // Arrange / Act
  const { labels } = build({ levels: { ...EMPTY, stopPrice: 58340 } });
  // Assert
  assert.deepEqual(labels, [...SET_ITEMS, '損切りを解除']);
});

test('TC-CL03 利確が設定済みなら「利確を解除」が出る', () => {
  // Arrange / Act
  const { labels } = build({ levels: { ...EMPTY, takePrice: 61500 } });
  // Assert
  assert.deepEqual(labels, [...SET_ITEMS, '利確を解除']);
});

test('TC-CL04 建値は設定済みの本だけが本数の分かる名前で出る（K 本のうち 2 本目だけ）', () => {
  // Arrange / Act
  const { labels } = build({ levels: { ...EMPTY, entryPrices: [null, 59700, null] } });
  // Assert: 「建値 2」＝モーダルの欄と同じ表示名（1 始まり）。接尾辞「を解除」は単純連結
  //   （表示名側に助詞を持たせない＝接尾辞の置き場を 1 か所に保つ）。
  assert.deepEqual(labels, [...SET_ITEMS, '建値 2を解除']);
});

test('TC-CL05 複数設定済みならモーダルの欄と同じ並び（建値 1..K → 損切り → 利確）', () => {
  // Arrange / Act
  const { labels } = build({
    levels: { entryPrices: [58700, null, 59700], stopPrice: 58340, takePrice: 61500 },
  });
  // Assert
  assert.deepEqual(labels, [
    ...SET_ITEMS, '建値 1を解除', '建値 3を解除', '損切りを解除', '利確を解除',
  ]);
});

test('TC-CL06 解除を選ぶと対象名で解除が呼ばれる（水準を作らない＝価格解決を通らない）', () => {
  // Arrange
  const { items, calls } = build({
    levels: { entryPrices: [58700, null, null], stopPrice: 58340, takePrice: 61500 },
  });
  // Act: 建値 1 / 損切り / 利確 の解除を順に選ぶ（座標は下段ペイン相当でも同じ）。
  items[3].onSelect({ x: 120, y: 900 });
  items[4].onSelect({ x: 120, y: 900 });
  items[5].onSelect({ x: 120, y: 900 });
  // Assert: 解決器は 1 度も呼ばれない（解除は座標に依存しない）。
  assert.deepEqual(calls, [['clear', 'entry:0'], ['clear', 'stop'], ['clear', 'take']]);
});

test('TC-CL07 既存 3 項目の文言・挙動は不変（解除項目を足しても動かない）', () => {
  // Arrange
  const { items, calls } = build({ levels: { ...EMPTY, stopPrice: 58340 } });
  // Act
  items[0].onSelect({ x: 7, y: 9 });
  items[1].onSelect({ x: 7, y: 9 });
  items[2].onSelect({ x: 7, y: 9 });
  // Assert: 従来どおり「解決 → 対応ハンドラ」の 2 段（TC-CI02/TC-CI03 と同じ形）。
  assert.deepEqual(calls, [
    ['resolve', { x: 7, y: 9 }], ['stop', 58650],
    ['resolve', { x: 7, y: 9 }], ['entry', 58650],
    ['resolve', { x: 7, y: 9 }], ['take', 58650],
  ]);
});

test('TC-CL08 水準の供給が無い構成では解除項目を出さない（従来の呼び出しを壊さない）', () => {
  // Arrange / Act: getLevels 未注入（既存の呼び出し側・単体検定と同じ形）。
  const items = createPriceContextItems({
    resolvePrice: () => ({ price: 1, reason: null }),
  });
  // Assert
  assert.deepEqual(items.map((i) => i.label), SET_ITEMS);
});

test('TC-CL09 非有限・null の価格は「設定済み」ではない（欄に打ちかけの値でも項目を出さない）', () => {
  // Arrange / Act: NaN は `_emitLevels` が null にするが、domain 経由でも非有限は入りうる。
  const { labels } = build({
    levels: { entryPrices: [NaN, undefined], stopPrice: Number.POSITIVE_INFINITY, takePrice: null },
  });
  // Assert
  assert.deepEqual(labels, SET_ITEMS);
});
