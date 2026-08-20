// symbol_spec_catalog.js の**フェイルクローズ**の検定（ISSUE-368 工程 4 の追補）。
//
// 対象（既存 symbol_spec_catalog.test.js が覆っていない 2 経路）:
//   (a) 3 段目「刻みが量子化に使えない台帳は、解決に成功したと名乗らない」
//       （`js/adapter/front/symbol_spec_catalog.js:52-54`。判定の定義源は
//        `js/domain/price_quantize.js:59-61` の `usableTick`）。
//       既存検定は生成物の**正常な**台帳しか通さないため、この段を外しても緑のまま
//       （実測 2026-08-20・工程 4）。
//   (b) 1 段目の `typeof datasetRef !== 'string'`（同 :47）。
//       既存 TC-SC05 は `Object.hasOwn` 側だけを狙っており、`typeof` 側を外しても
//       `'toString'` 等は 2 段目で塞がれるため緑のまま＝**変異非感応**（同実測）。
//       素の添字参照は `{toString(){return 'jp225_tick'}}` / `['jp225_tick']` /
//       `new String('jp225_tick')` の 3 例を JP225 として引き当ててしまう。
//
// 台帳の差し替え方法（実測に基づく選択・2026-08-20）:
//   1. catalog は台帳の注入口を持たない（`lookupSymbolSpec(datasetRef)` の 1 引数のみ）。
//   2. `node:test` の `mock.module` は本環境（node v24.16.0）では `undefined`
//      （`--experimental-test-module-mocks` が要る。起動は `package.json` の
//       `node --test` 固定で、フラグを足すには本検定の対象外ファイルの改変が要る）。
//   3. 生成物 `domain/symbol_spec_generated.js` は自動生成物であり、壊した状態が残る
//      危険があるため書き換えない（禁止事項）。
//   よって **catalog の実ソースを読み、import 指定子だけを差し替えて評価する**。
//   評価されるのは実ファイルの中身そのものなので、3 段目を実装から外せば本検定も赤くなる
//   （＝検出力がある）。`usableTick` は実物をそのまま使う（判定の第 2 定義を作らない）。
//
// 構造: Arrange-Act-Assert（AAA）。DOM・fetch・lwc 非依存の純関数検定。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { lookupSymbolSpec } from '../js/adapter/front/symbol_spec_catalog.js';
import { DATASET_SYMBOLS, SYMBOL_SPECS } from '../js/domain/symbol_spec_generated.js';

const CATALOG_URL = new URL('../js/adapter/front/symbol_spec_catalog.js', import.meta.url);
const QUANTIZE_URL = new URL('../js/domain/price_quantize.js', import.meta.url).href;
const GENERATED_SPECIFIER = "'../../domain/symbol_spec_generated.js'";
const QUANTIZE_SPECIFIER = "'../../domain/price_quantize.js'";

function occurrences(haystack, needle) {
  return haystack.split(needle).length - 1;
}

function dataModule(source) {
  return `data:text/javascript,${encodeURIComponent(source)}`;
}

/**
 * 刻みだけを差し替えた台帳を注入した catalog を読み込む。
 *
 * @param {string} tickExpr 台帳が持つ `tick` の**ソース表現**（'0' / 'NaN' / "'1.0'" 等）。
 *   値ではなくソースで受けるのは、NaN・Infinity・undefined が JSON で運べないため。
 * @returns {Promise<(ref:unknown)=>({symbol:string,tick:number,digits:number}|null)>}
 */
async function catalogWithTick(tickExpr) {
  const source = readFileSync(CATALOG_URL, 'utf8');
  // 差し替えが空振りしたまま「実台帳で null が出た」と誤読しないよう、指定子の在席を先に主張する。
  assert.equal(occurrences(source, GENERATED_SPECIFIER), 1, '生成物の import 指定子が 1 件でない（差し替え前提の崩れ）');
  assert.equal(occurrences(source, QUANTIZE_SPECIFIER), 1, 'price_quantize の import 指定子が 1 件でない（差し替え前提の崩れ）');
  const ledger = dataModule(
    `export const DATASET_SYMBOLS = Object.freeze({ 'jp225_tick': 'JP225' });\n`
    + `export const SYMBOL_SPECS = Object.freeze({ JP225: Object.freeze({ tick: ${tickExpr}, digits: 0 }) });\n`,
  );
  const injected = source
    .replace(GENERATED_SPECIFIER, JSON.stringify(ledger))
    .replace(QUANTIZE_SPECIFIER, JSON.stringify(QUANTIZE_URL));
  const mod = await import(dataModule(injected));
  return mod.lookupSymbolSpec;
}

test('TC-FC00 対照: 正常な刻みを注入した台帳は解決できる（null 一色ではない＝注入が生きている）', async () => {
  // Arrange
  const lookup = await catalogWithTick('0.5');
  // Act
  const got = lookup('jp225_tick');
  // Assert: これが緑でなければ以降の「null であること」は注入の壊れによる偽陽性になる。
  assert.deepEqual(got, { symbol: 'JP225', tick: 0.5, digits: 0 });
});

test('TC-FC01 刻みが 0 の台帳は解決成功にしない（境界: 0 は量子化に使えない）', async () => {
  // Arrange
  const lookup = await catalogWithTick('0');
  // Act / Assert: 0 で割った丸めは無音で NaN／生値を下流へ流す。ここで機能を落とす。
  assert.equal(lookup('jp225_tick'), null);
});

test('TC-FC02 刻みが負の台帳は解決成功にしない', async () => {
  // Arrange
  const lookup = await catalogWithTick('-1');
  // Act / Assert
  assert.equal(lookup('jp225_tick'), null);
});

test('TC-FC03 刻みが NaN の台帳は解決成功にしない', async () => {
  // Arrange
  const lookup = await catalogWithTick('NaN');
  // Act / Assert
  assert.equal(lookup('jp225_tick'), null);
});

test('TC-FC04 刻みが Infinity の台帳は解決成功にしない', async () => {
  // Arrange
  const lookup = await catalogWithTick('Infinity');
  // Act / Assert
  assert.equal(lookup('jp225_tick'), null);
});

test('TC-FC05 刻みが数値でない台帳は解決成功にしない（文字列・null・undefined）', async () => {
  // Arrange / Act / Assert: 台帳が壊れる形は「欠落」と「型違い」の両方があり得る。
  for (const tickExpr of ["'1.0'", 'null', 'undefined', '{}', 'true']) {
    const lookup = await catalogWithTick(tickExpr);
    assert.equal(lookup('jp225_tick'), null, `tick=${tickExpr} が解決成功になっている`);
  }
});

test('TC-FC06 境界: 量子化できる限界までの極小刻みは解決できる（判定は「0 より大」であって「1 以上」ではない）', async () => {
  // Arrange: 下限側を締めすぎていないことの回帰ガード。
  //   入力は `usableTick` が通す上限そのもの（`decimalsOf(1e-100) === 100` ＝ `toFixed` の受付上限）。
  //   実測（node v24.16.0・2026-08-20）: `usableTick(1e-100) === 1e-100` / `quantize(58998.75, 1e-100) === 58998.75`。
  //   当初は `Number.MIN_VALUE` を使っていたが、それは量子化不能な値であり期待値が事実に反していた
  //   （根拠は TC-FC06B）。本ケースの宣言意図「下限を締めすぎない」は変えず、入力だけを正しい境界へ差し替えている。
  const lookup = await catalogWithTick('1e-100');
  // Act
  const got = lookup('jp225_tick');
  // Assert
  assert.notEqual(got, null, '量子化できる極小刻みが落とされている（下限側の締めすぎ）');
  assert.equal(got.tick, 1e-100);
});

test('TC-FC06B 境界: Number.MIN_VALUE は解決成功にしない（量子化が原理的に不能）', async () => {
  // Arrange: 「正の有限数なら使える」では足りないことの根拠（すべて node v24.16.0 実測・2026-08-20）:
  //   1. `58998.75 / 5e-324 === Infinity` → `Math.round(58998.75 / 5e-324) * 5e-324 === Infinity`。
  //      つまり `toFixed` の有無に関わらず、実勢価格では量子化そのものが成立しない。
  //   2. `decimalsOf(5e-324) === 324` で `toFixed` の受付上限 100 を超えるため、
  //      `quantize(58998.75, 5e-324)` は `RangeError: toFixed() digits argument must be between 0 and 100`。
  //   よってこの刻みは「使えない」と判定するのが正しい。解決成功として通すと、
  //   例外で止まるか、下流へ `Infinity` という**無音の誤答**が流れる。
  //   （`5e-324 === Number.MIN_VALUE` 。判定の定義源は `js/domain/price_quantize.js` の `usableTick`。）
  const lookup = await catalogWithTick('Number.MIN_VALUE');
  // Act / Assert
  assert.equal(lookup('jp225_tick'), null);
});

test('TC-FC07 文字列でない ref は引き当てない（暗黙の文字列化で別銘柄に化けない）', () => {
  // Arrange: いずれも素の添字参照だと 'jp225_tick' に化けて JP225 を引き当てる 3 例。
  const coercing = [
    { toString() { return 'jp225_tick'; } },
    ['jp225_tick'],
    // eslint-disable-next-line no-new-wrappers
    new String('jp225_tick'),
  ];
  // Act / Assert
  for (const ref of coercing) {
    assert.equal(lookupSymbolSpec(ref), null, `文字列化で引き当ててしまう ref: ${String(ref)}`);
  }
  for (const ref of [null, undefined, 0, 1, Number.NaN, true, {}, () => 'jp225_tick']) {
    assert.equal(lookupSymbolSpec(ref), null, `ref が文字列でないのに解決している: ${String(ref)}`);
  }
});

test('TC-FC08 正常系の回帰: 生成物の全 ref が解決でき、未知 ref は null', () => {
  // Arrange
  const refs = Object.keys(DATASET_SYMBOLS);
  // Act / Assert: 期待値は台帳から引く（本ファイルに刻み・桁の数値を書かない＝第 2 定義を作らない）。
  assert.ok(refs.length > 0, '生成物が空（走査が空振りしている）');
  for (const ref of refs) {
    const symbol = DATASET_SYMBOLS[ref];
    assert.deepEqual(
      lookupSymbolSpec(ref),
      { symbol, tick: SYMBOL_SPECS[symbol].tick, digits: SYMBOL_SPECS[symbol].digits },
      `台帳にある ref が解決できない: ${ref}`,
    );
  }
  assert.equal(lookupSymbolSpec('no_such_dataset_ref'), null);
});
