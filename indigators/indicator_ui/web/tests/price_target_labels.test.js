// price_target_labels.js（価格水準の表示名の単一ソース）の仕様検証（ISSUE-435）。
//
// 設計入力（唯一の仕様源）:
//   - モーダルの価格欄・アーム中バーが既に使っていた表 `position_sizing_dialog.js:247-257`
//     （`'損切り'` / `'利確'` / `建値 ${n+1}`）。**文言はこの表が正解を定義する**。
//   - `'ロスカット'` は参照実装 integrated_position_sizing_calculator.html:781
//     （`marker(adLc, ..., 'ロスカット', ...)`）が定義している。モーダルに欄が無い
//     （＝入力ではない）ため上の表には無く、水準線ラベル（ISSUE-435 実装 2）で初めて要る。
//
// 観点: 表示名の使用点は 4 つ（モーダルの欄 / アーム中バー / 水準線ラベル / 右クリックの解除項目）。
//   表が 2 か所に割れると、文言を直したとき片方が取り残される（プロジェクト規約「同じコードを
//   手書き複製するな」）。**表が front 配下に 1 つしか無いこと**を機械的に固定する。
// 構造: Arrange-Act-Assert。純関数なので fake は要らない。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { priceTargetLabel } from '../js/adapter/front/price_format.js';

const FRONT = new URL('../js/adapter/front/', import.meta.url);

test('TC-LB01 対象名 → 表示名（モーダルの表 :247-257 と厳密に同一）', () => {
  // Arrange / Act / Assert
  assert.equal(priceTargetLabel('stop'), '損切り');
  assert.equal(priceTargetLabel('take'), '利確');
  assert.equal(priceTargetLabel('entry:0'), '建値 1');
  assert.equal(priceTargetLabel('entry:2'), '建値 3');
});

test('TC-LB02 ロスカットは参照実装 :781 の文言（モーダルに欄が無い＝水準線だけが使う）', () => {
  // Arrange / Act / Assert
  assert.equal(priceTargetLabel('losscut'), 'ロスカット');
});

test('TC-LB03 未知・不正な対象名は空文字（例外を投げない＝呼び出し側で分岐を増やさない）', () => {
  // Arrange / Act / Assert: 元の表（:256 の `m ? ... : ''`）と同じ全域性。
  for (const bad of [null, undefined, '', 'entry:', 'entry:x', 'nope', 42, {}]) {
    assert.equal(priceTargetLabel(bad), '', `${String(bad)} が空文字にならない`);
  }
});

test('TC-LB04 表示名の表は front 配下に 1 か所だけ（第 2 実装を作らない）', () => {
  // Arrange: 表示名そのもの（＝クォートで閉じた完全一致の文字列・建値の連番テンプレート）を探す。
  //   「この価格を損切りに設定」のような**別の文言**は対象外（部分一致で数えない）。
  const NAME_LITERAL = /'損切り'|'利確'|'ロスカット'|`建値 \$\{/;
  const dir = fileURLToPath(FRONT);
  // Act
  const owners = readdirSync(dir)
    .filter((f) => f.endsWith('.js'))
    .filter((f) => NAME_LITERAL.test(readFileSync(`${dir}${f}`, 'utf8')));
  // Assert
  assert.deepEqual(owners, ['price_format.js'], `表示名が複数の実装に散っている: ${owners.join(', ')}`);
});
