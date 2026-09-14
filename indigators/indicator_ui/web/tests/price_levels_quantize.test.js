// price_levels_quantize.test.js — PriceLevels（E-02）の量子化不変条件の検定（ISSUE-368 スライス S-4）。
//
// 設計入力: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md「追補: 工程 2」E-4 / S-4。
//   不変条件: **刻み上にない価格は PriceLevels に存在できない**。
//   関門を domain（構築・更新口）に置く理由: 丸めの適用点は 7 経路あり、うち水準線 drag
//   （`adapter/front/price_level_drag_controller.js:157-176`）は resolver を通らないため、
//   関門が adapter 側にあると迂回される（設計 §丸めの適用点 経路 6）。
//
// 後方互換: `tick` は**任意注入・既定は素通し**。既定（未注入）では従来と完全に同一に振る舞い、
//   既存 tests/price_levels.test.js を 1 バイトも変えずに緑になる。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { createPriceLevels } from '../js/domain/price_levels.js';

// チャートのピックが返す生の浮動小数（本ブランチで実際に食い違った型の値）
const RAW = {
  direction: 'long',
  entryPrices: [58700.4, 59700.6, 60700.5],
  stopPrice: 58340.71,
  takePrice: 61500.29,
};

test('TC-PLQ01 tick 未注入なら価格は一切変わらない（既定は素通し＝従来の振る舞いと同一）', () => {
  // Arrange / Act
  const levels = createPriceLevels(RAW);
  // Assert
  assert.deepEqual([...levels.entryPrices], [58700.4, 59700.6, 60700.5]);
  assert.equal(levels.stopPrice, 58340.71);
  assert.equal(levels.takePrice, 61500.29);
});

test('TC-PLQ02 経路 1/4: create に tick を注入すると建値・損切り・利確が刻み上になる', () => {
  // Arrange / Act
  const levels = createPriceLevels({ ...RAW, tick: 1 });
  // Assert
  assert.deepEqual([...levels.entryPrices], [58700, 59701, 60701]);
  assert.equal(levels.stopPrice, 58341);
  assert.equal(levels.takePrice, 61500);
});

test('TC-PLQ03 経路 2/4: withEntry で入れた生値も刻み上になる', () => {
  // Arrange
  const levels = createPriceLevels({ ...RAW, tick: 1 });
  // Act
  const moved = levels.withEntry(1, 59999.87);
  // Assert
  assert.equal(moved.entryPrices[1], 60000);
  assert.deepEqual([...moved.entryPrices], [58700, 60000, 60701], '他の建値も刻み上のまま');
});

test('TC-PLQ04 経路 3/4: withStop で入れた生値も刻み上になる（drag 経路の関門）', () => {
  // Arrange
  const levels = createPriceLevels({ ...RAW, tick: 1 });
  // Act
  const moved = levels.withStop(58000.62);
  // Assert
  assert.equal(moved.stopPrice, 58001);
});

test('TC-PLQ05 経路 4/4: withTake で入れた生値も刻み上になる（null は素通し）', () => {
  // Arrange
  const levels = createPriceLevels({ ...RAW, tick: 1 });
  // Act / Assert
  assert.equal(levels.withTake(62000.44).takePrice, 62000);
  assert.equal(levels.withTake(null).takePrice, null, '利確の無効化は従来どおり null');
});

test('TC-PLQ06 tick=0.1 でも浮動小数残差を持ち込まない（PriceLevels 経由でも厳密一致）', () => {
  // Arrange / Act
  const levels = createPriceLevels({
    direction: 'long', entryPrices: [8568.84], stopPrice: 70.12, takePrice: 8568.89, tick: 0.1,
  });
  // Assert
  assert.equal(levels.entryPrices[0], 8568.8);
  assert.equal(String(levels.entryPrices[0]), '8568.8');
  assert.equal(levels.stopPrice, 70.1);
  assert.equal(String(levels.stopPrice), '70.1');
  assert.equal(levels.takePrice, 8568.9);
});

test('TC-PLQ07 tick は with* に引き継がれる（1 度注入すれば以後の更新口も迂回できない）', () => {
  // Arrange
  const levels = createPriceLevels({ ...RAW, tick: 1 });
  // Act: 更新を連鎖させる
  const chained = levels.withStop(58000.62).withEntry(0, 58700.4).withTake(62000.44);
  // Assert
  assert.equal(chained.stopPrice, 58001);
  assert.equal(chained.entryPrices[0], 58700);
  assert.equal(chained.takePrice, 62000);
});

test('TC-PLQ08 tick を注入しても保持するのは価格 4 種のみ（状態を増やさない）', () => {
  // Arrange / Act
  const levels = createPriceLevels({ ...RAW, tick: 1 });
  // Assert: 既存検定「保持するのは価格のみ」と同じ列挙結果であること
  assert.deepEqual(Object.keys(levels).sort(),
    ['direction', 'entryPrices', 'stopPrice', 'takePrice'].sort());
});

test('TC-PLQ09 派生距離は量子化後の価格から計算される（表示とモデルが食い違わない）', () => {
  // Arrange / Act
  const levels = createPriceLevels({ ...RAW, tick: 1 });
  // Assert: 58700 − 58341 = 359（生値 58700.4 − 58340.71 = 359.69 ではない）
  assert.equal(levels.stopDistance(), 359);
  assert.equal(levels.takeDistance(), 2800);   // 61500 − 58700
  assert.deepEqual(levels.entryDistances(), [359, 1360, 2360]);
});

test('TC-PLQ10 量子化の式は web/js 配下に 1 つしか存在しない（第 2 実装を作らない）', async () => {
  // Arrange
  const { readdirSync, readFileSync, statSync } = await import('node:fs');
  const { join } = await import('node:path');
  const walk = (dir) => readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    return statSync(full).isDirectory() ? walk(full) : [full];
  });
  // Act: 「/ tick を含む丸め」の実装を持つファイルを数える
  const pattern = /Math\.(round|floor|ceil|trunc)\s*\([^;\n]*\/\s*tick/;
  const hits = walk('js').filter((f) => f.endsWith('.js') && pattern.test(readFileSync(f, 'utf8')));
  // Assert
  assert.deepEqual(hits, ['js/domain/price_quantize.js']);
});
