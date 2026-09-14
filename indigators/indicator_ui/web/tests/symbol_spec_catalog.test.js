// symbol_spec_catalog.js（datasetRef → 銘柄仕様の引き当て・ISSUE-368 スライス S-5）のテスト。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   「追補: 工程 2」E-2 / E-3（呼び値の定義は Python 台帳ただ 1 つ。JS は生成物
//    `domain/symbol_spec_generated.js` を**読むだけ**。HTTP route は作らない）、
//   「フェイルセーフ」（未知 ref は `reason='no_symbol_spec'` へ倒す。**静的既定への無音
//    フォールバックは採らない**＝`catalog_client.js:47-50` 型の事故形 ISSUE-278 #8 の再演禁止）。
//
// 責務: 生成物の 2 段（ref→銘柄／銘柄→{tick,digits}）を 1 回の引き当てに畳む薄い変換。
//   ここが「front 配下で唯一の銘柄仕様の解決点」であることは
//   position_sizing_symbol_spec_wiring.test.js の構造ガードが固定する。
// 構造: Arrange-Act-Assert（AAA）。生成物以外の入力を持たない純関数（DOM・fetch 非依存）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { lookupSymbolSpec } from '../js/adapter/front/symbol_spec_catalog.js';
import { DATASET_SYMBOLS, SYMBOL_SPECS } from '../js/domain/symbol_spec_generated.js';

test('TC-SC01 既知の datasetRef から銘柄と呼び値・表示桁を引く（JP225 系）', () => {
  // Arrange / Act
  const got = lookupSymbolSpec('jp225_tick');
  // Assert: 値の権威は Python 台帳（生成物）。ここで別の数値を書くと第 2 定義になるため、
  //   「生成物と一致すること」を主張する（台帳が変われば本検定も追随する）。
  assert.deepEqual(got, { symbol: 'JP225', tick: SYMBOL_SPECS.JP225.tick, digits: SYMBOL_SPECS.JP225.digits });
});

test('TC-SC02 別銘柄の datasetRef は別の呼び値を返す（ref ごとに解決している）', () => {
  // Arrange / Act
  const got = lookupSymbolSpec('sample');
  // Assert
  assert.deepEqual(got, { symbol: 'TSLA', tick: SYMBOL_SPECS.TSLA.tick, digits: SYMBOL_SPECS.TSLA.digits });
  assert.notEqual(got.tick, SYMBOL_SPECS.JP225.tick, '銘柄ごとに刻みが違うことが引き当てで反映される');
});

test('TC-SC03 生成物の全 ref が解決できる（台帳が増えたときの取り残しを検出する）', () => {
  // Arrange
  const refs = Object.keys(DATASET_SYMBOLS);
  // Act / Assert
  assert.ok(refs.length > 0, '生成物が空（走査が空振りしている）');
  for (const ref of refs) {
    const got = lookupSymbolSpec(ref);
    assert.notEqual(got, null, `台帳にある ref が解決できない: ${ref}`);
    assert.ok(Number.isFinite(got.tick) && got.tick > 0, `刻みが正の有限数でない: ${ref}`);
  }
});

test('TC-SC04 未知の datasetRef は null（静的既定へ無音フォールバックしない・ISSUE-278 #8）', () => {
  // Arrange / Act / Assert: 「それらしい既定」を返すと、呼び側は失敗に気づけないまま
  //   別銘柄の刻みで価格を丸める（無音の誤答）。機能を落とすために null を返す。
  assert.equal(lookupSymbolSpec('does_not_exist'), null);
  assert.equal(lookupSymbolSpec(''), null);
  assert.equal(lookupSymbolSpec(null), null);
  assert.equal(lookupSymbolSpec(undefined), null);
});

test('TC-SC05 Object の継承プロパティ名を ref として渡しても引き当てない（自前キーだけを見る）', () => {
  // Arrange / Act / Assert: `DATASET_SYMBOLS['toString']` は関数を返す。素の添字参照だと
  //   「解決できた」と誤判定し、tick=undefined のまま量子化が素通しになる（無音の生値）。
  assert.equal(lookupSymbolSpec('toString'), null);
  assert.equal(lookupSymbolSpec('constructor'), null);
  assert.equal(lookupSymbolSpec('__proto__'), null);
});

test('TC-SC06 引き当て結果を書き換えても生成物は汚れない（台帳は読むだけ）', () => {
  // Arrange
  const got = lookupSymbolSpec('jp225_tick');
  const before = SYMBOL_SPECS.JP225.tick;
  // Act: 呼び側が返り値を触っても台帳が変わらないこと（生成物は Object.freeze だが、
  //   引き当て結果が同一参照だと「呼び側の事故が全銘柄へ波及する」構造になる）。
  got.tick = 999;
  // Assert
  assert.equal(SYMBOL_SPECS.JP225.tick, before);
  assert.equal(lookupSymbolSpec('jp225_tick').tick, before, '次の引き当てが汚染されている');
});
