// 「刻み上にない価格は存在できない」と謳う関門が、自分では刻みを検証していなかった（工程 5 🟡-3）。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md「追補: 工程 2」E-4
//   （量子化を **`PriceLevels` の構築・更新口**に置く。「刻み上にない価格は `PriceLevels` に
//    存在できない」を不変条件にする。drag 経路は resolver を通らないため、関門は domain で
//    なければ迂回される）、同「フェイルセーフ」（**無音の誤答を作らない**）。
//
// 是正前の実測（node v24・`price_levels.js:37-45` / `:123-135`）:
//   tick=0        → entryPrices=[NaN] / stopPrice=NaN      （無音の誤答）
//   tick=-1       → entryPrices=[58999]                     （**無音で丸まる**＝誤りが正常値の顔で通る）
//   tick=NaN      → entryPrices=[NaN]                       （無音の誤答）
//   tick=Infinity → entryPrices=[NaN]                       （無音の誤答）
//   tick=1e-101   → RangeError: toFixed() digits argument must be between 0 and 100
//                                                           （domain の契約でない例外が漏れる）
//   同じ `domain` に「使える刻みか」の唯一源 `usableTick`（`price_quantize.js`）があるのに、
//   関門を名乗る `PriceLevels` はそれを呼んでいなかった。
//
// 是正の形: `createPriceLevels` で `spec.tick != null && usableTick(spec.tick) === null` のとき
//   **`throw new Error(...)`**（`direction` / `entryPrices` の既存検証と同じ流儀）。
//   `quantize` の素通し契約（`null` / 未指定）は**変えない**＝既存 `price_levels.test.js` は
//   `tick` 未注入なので無改変で緑のまま。
//
// 構造: Arrange-Act-Assert。純関数のみ（DOM 非依存）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { createPriceLevels, LONG } from '../js/domain/price_levels.js';
import { usableTick } from '../js/domain/price_quantize.js';

const RAW = Object.freeze({
  direction: LONG, entryPrices: [58998.75], stopPrice: 58900.4, takePrice: 59200.6,
});
const levels = (tick) => createPriceLevels(tick === undefined ? { ...RAW } : { ...RAW, tick });

// 「使えない刻み」の一覧は台帳（`usableTick`）が定義する。ここに規則を書き写さず、
//   `usableTick` が `null` を返すことを前提として明示してから使う（前提の崩れを検定で見る）。
const UNUSABLE_TICKS = [0, -1, Number.NaN, Infinity, -Infinity, 1e-101, '1', true, {}];

test('TC-UT01 使えない刻みを注入したら構築時に落ちる（無音の誤答を作らない）', () => {
  // Arrange / Act / Assert
  for (const tick of UNUSABLE_TICKS) {
    assert.equal(usableTick(tick), null, `前提の崩れ: usableTick が使えると答えている: ${String(tick)}`);
    assert.throws(
      () => levels(tick),
      /刻み|tick/,
      `使えない刻み ${String(tick)} が無音で通っている`,
    );
  }
});

test('TC-UT02 落ちるのは domain の契約違反として（言語仕様の例外を漏らさない）', () => {
  // Arrange / Act / Assert: 1e-101 は従来 `toFixed` の RangeError が素通りしていた。
  assert.throws(() => levels(1e-101), (e) => e instanceof Error && !(e instanceof RangeError));
});

test('TC-UT03 無音で丸まる経路を塞ぐ（負の刻みが正常値の顔で入らない）', () => {
  // Arrange: 従来 tick=-1 は entryPrices=[58999] を返していた（誤りが検知されない）。
  // Act / Assert
  assert.throws(() => levels(-1));
});

test('TC-UT04 素通し契約は変えない（tick 未指定・null は従来と完全同一）', () => {
  // Arrange / Act
  const omitted = levels(undefined);
  const explicitNull = levels(null);
  // Assert: 量子化せず、生値をそのまま保持する（既存 price_levels.test.js の前提）。
  for (const l of [omitted, explicitNull]) {
    assert.deepEqual(l.entryPrices, RAW.entryPrices);
    assert.equal(l.stopPrice, RAW.stopPrice);
    assert.equal(l.takePrice, RAW.takePrice);
  }
});

test('TC-UT05 使える刻みは従来どおり量子化する（関門を閉じすぎない）', () => {
  // Arrange / Act
  const one = levels(1);
  const cent = levels(0.01);
  // Assert
  assert.deepEqual(one.entryPrices, [58999]);
  assert.equal(one.stopPrice, 58900);
  assert.deepEqual(cent.entryPrices, [58998.75]);
  assert.equal(cent.takePrice, 59200.6);
});

test('TC-UT06 更新口（withStop / withEntry / withTake）も刻みを保ったまま通る', () => {
  // Arrange
  const l = levels(1);
  // Act
  const next = l.withStop(58800.6).withEntry(0, 58997.2).withTake(59300.4);
  // Assert
  assert.equal(next.stopPrice, 58801);
  assert.deepEqual(next.entryPrices, [58997]);
  assert.equal(next.takePrice, 59300);
});

test('TC-UT07 判定は domain の唯一源を呼ぶ（第 2 実装を作らない）', () => {
  // Arrange
  const src = readFileSync(
    fileURLToPath(new URL('../js/domain/price_levels.js', import.meta.url)), 'utf8',
  );
  const code = src.replace(/\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '');
  // Act / Assert
  assert.match(code, /usableTick/, '「使える刻みか」の唯一源を呼んでいない');
  assert.equal(
    /Number\.isFinite\(\s*tick/.test(code), false,
    '刻みの判定を自前で書いている（規則が 1 つで実装が 2 つになる＝原因 β）',
  );
});
