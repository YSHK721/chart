// price_quantize_usable_tick.test.js — 「量子化に使える刻みか」の判定（`usableTick`）の直接検定。
//
// なぜ独立した 1 本が要るか（工程 4 の穴）:
//   工程 4 のリファクタで `js/domain/price_quantize.js` に `usableTick` が新設され、
//   「使える刻みか」の規則は**この 1 定義だけ**になった。実際に import して使う front は 3 か所:
//     - `js/adapter/front/price_pick_resolver.js:21,120`（spec から取り出して使えなければ null）
//     - `js/adapter/front/chart_bootstrap.js:18,34`（価格軸の priceFormat を設定するかの関門）
//     - `js/adapter/front/symbol_spec_catalog.js:20,55`（引き当てが「解決できた」と名乗る関門）
//   さらに 2 か所（`position_sizing_controller.js:61` / `position_sizing_dialog.js:311`）は
//   「自分では検算せず唯一源に委ねる」という不変条件を**コメントで宣言**して自前の式を持たない。
//   つまり front 全体が「この 1 関数の判定が正しいこと」に依存している。
//   にもかかわらず、本ファイル追加前は `usableTick` を名指しで検定するスイートが 0 件だった
//   （`symbol_spec_catalog_fail_closed.test.js` が引き当て経由で間接的に踏むのみ）。
//   規則を変えても何も落ちない状態を塞ぐのが本ファイルの責務である。
//
// 検定する契約（`price_quantize.js:44-61` の jsdoc に明記されたもの）:
//   - 戻り値は真偽値ではなく**刻みそのもの**（使えなければ `null`）。呼び出し側が値を取り直さない。
//   - 使える = 正の有限数。`0` / 負 / 非有限 / 非数（型強制なし）はすべて `null`。
//   - 判定と適用が食い違わない: 使えると判定した刻みでは `quantize` が有限値を返す。
//
// 対象外（意図的に検定しない）:
//   `tick < 1e-100` の極小刻み。`usableTick` は正の有限数として**使えると判定する**が、
//   `quantize` は `toFixed(101)` 以上になり RangeError を投げる（実測は報告に添付）。
//   ここは唯一源の契約自体が割れている領域で、どちらへ倒すかは実装側の判定を要する。
//   現状の挙動をここで期待値として固定すると、割れたままの契約を検定で追認することになるため
//   本ファイルでは期待値を置かない（実カタログの刻みは 1〜0.001 の範囲で、この領域には入らない）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { quantize, usableTick } from '../js/domain/price_quantize.js';

test('TC-UT01 usableTick は 0 を使えない刻みとして落とす（境界値）', () => {
  // Arrange / Act / Assert: 0 を通すと下流の量子化が無音で NaN を作る（TC-UT07 で前提を固定）
  assert.equal(usableTick(0), null);
  assert.equal(usableTick(-0), null);
});

test('TC-UT02 usableTick は負の刻みを落とす（丸まってしまう＝無音の誤答の入口）', () => {
  // Arrange / Act / Assert
  assert.equal(usableTick(-1), null);
  assert.equal(usableTick(-0.1), null);
  assert.equal(usableTick(-Number.MIN_VALUE), null);
});

test('TC-UT03 usableTick は非有限（NaN・±Infinity）を落とす', () => {
  // Arrange / Act / Assert
  assert.equal(usableTick(NaN), null);
  assert.equal(usableTick(Infinity), null);
  assert.equal(usableTick(-Infinity), null);
});

test('TC-UT04 usableTick は非数を落とす（型強制しない＝数字に見える文字列も通さない）', () => {
  // Arrange: `'1' > 0` は true になるため、有限数判定が無いと文字列が刻みとして通る
  assert.ok('1' > 0, '前提: 文字列は比較演算子で型強制され正と評価される');
  // Act / Assert
  assert.equal(usableTick('1'), null);
  assert.equal(usableTick('0.1'), null);
  assert.equal(usableTick(''), null);
  assert.equal(usableTick(null), null);
  assert.equal(usableTick(undefined), null);
  assert.equal(usableTick({}), null);
  assert.equal(usableTick({ tick: 1 }), null);   // spec をそのまま渡す誤りも落ちる
  assert.equal(usableTick([]), null);
  assert.equal(usableTick([1]), null);
  assert.equal(usableTick(true), null);
  assert.equal(usableTick(1n), null);            // BigInt も Number.isFinite は false
});

test('TC-UT05 usableTick は使える刻みを真偽値ではなく刻みそのものとして返す', () => {
  // Arrange: 呼び出し側（price_pick_resolver.js:120）は戻り値を刻みとしてそのまま下流へ渡す。
  //   true/false を返す契約に変えると、その 1 か所が無音で壊れる。
  // Act / Assert: 実カタログの刻み（JP225=1・FX=0.1/0.001）と刻み幅の上下
  assert.equal(usableTick(1), 1);
  assert.equal(usableTick(0.1), 0.1);
  assert.equal(usableTick(0.25), 0.25);
  assert.equal(usableTick(0.001), 0.001);
  assert.equal(usableTick(100), 100);
  assert.equal(usableTick(1e-6), 1e-6);
  assert.equal(usableTick(1e-7), 1e-7);
  assert.equal(usableTick(Number.MAX_VALUE), Number.MAX_VALUE);
});

test('TC-UT06 usableTick が使えると判定した刻みでは quantize が有限値を返す（判定と適用が食い違わない）', () => {
  // Arrange: 関門を通った刻みだけが下流の量子化へ入る（front 3 か所の使い方）
  const ticks = [1, 0.1, 0.25, 0.001, 100, 1e-6, 1e-7];
  const prices = [58998.75, 62707.710070965324, 8568.89, 0, -1234.5];
  for (const tick of ticks) {
    // Act
    const passed = usableTick(tick);
    assert.notEqual(passed, null, `前提: ${tick} は使える刻み`);
    for (const price of prices) {
      const actual = quantize(price, passed);
      // Assert: 有限であり、かつ刻みの倍数（丸めたつもりで丸まっていない状態を作らない）
      assert.ok(Number.isFinite(actual), `quantize(${price}, ${tick}) が有限でない: ${actual}`);
      assert.ok(
        Math.abs(actual - Math.round(actual / passed) * passed) <= Math.abs(actual) * Number.EPSILON * 8,
        `quantize(${price}, ${tick}) = ${actual} が刻みの倍数でない`,
      );
    }
  }
});

test('TC-UT07 usableTick が落とす刻みを quantize へ直接渡すと無音の誤答になる（関門が要る理由の前提固定）', () => {
  // Arrange: `quantize` の素通し条件は null / 未指定のみ（後方互換の契約）。
  //   使えない刻みを弾く責務は quantize ではなく usableTick 側にある、という分担を固定する。
  //   ここが緑のままなら「関門を外しても平気」には決してならない（node v24 実測）。
  // Act / Assert
  assert.ok(Number.isNaN(quantize(58998.75, 0)), 'tick=0 は NaN（Infinity*0）');
  assert.equal(quantize(58998.75, -1), 58999, 'tick=-1 は丸まってしまう＝誤りが正常値の顔をする');
  assert.ok(Number.isNaN(quantize(58998.75, NaN)), 'tick=NaN は NaN');
  assert.ok(Number.isNaN(quantize(58998.75, Infinity)), 'tick=Infinity は NaN');
});
