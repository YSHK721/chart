// series_primitive_lifecycle.test.js — lwc ISeriesPrimitive の**ライフサイクル定型**を
//   単一の基底へ括り出したこと（ISSUE-479 Wave2b J-6）を構造で固定する。
//
// なぜ在るか:
//   `attached({chart,series,requestUpdate})` / `detached()` / `paneViews()` / 再描画要求 /
//   単一 paneView の保持は lwc が要求する定型であり、業務規則ではない。この定型が
//   pair_primitive_base.js・tickvol_bands_primitive.js・price_level_lines_primitive.js の
//   3 か所へ手書きで複製されていた（price_level_lines_primitive.js:11-16 が「共通基底が無い」
//   ことを理由として明記していた）。複製は必ず取り残しを生む。
//
// 固定するもの:
//   R1 継承      3 primitive が SeriesPrimitiveLifecycle の実体である（定型の単一ソース化）。
//   R2 ISP/LSP   ペア固有の公開面（setPairs / setHighlight）が、ペアでない primitive に生えない。
//                （PairPrimitiveBase を全員に継承させる案を採ると赤になる形で書く。）
//   R3 byte 等価 抽出の前後で paneView の**キーの有無まで**一致する（zOrder はサブクラスの
//                宣言があるときだけ生える）。renderer() が毎回新オブジェクトを返す現行契約も維持。
//   C1-C4 計算量 状態設定 1 回あたりの再描画要求が 1（浪費の不在）であること。
//
// R3 のオラクル値は**抽出前の実測**（2026-09-04・node v24）である:
//   PairLines ["renderer"] / Tickvol ["renderer","zOrder"] / PriceLevelLines ["renderer"]
//   いずれも paneViews().length=1・同一 paneView インスタンス・renderer() は毎回新オブジェクト。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { SeriesPrimitiveLifecycle } from '../js/adapter/front/series_primitive_lifecycle.js';
import { PairPrimitiveBase } from '../js/adapter/front/pair_primitive_base.js';
import { PairLinesPrimitive } from '../js/adapter/front/pair_lines_primitive.js';
import { TickvolBandsPrimitive } from '../js/adapter/front/tickvol_bands_primitive.js';
import { PriceLevelLinesPrimitive } from '../js/adapter/front/price_level_lines_primitive.js';

// --------------------------------------------------------------------------- //
// 測定用の最小 fake（座標源は使わない。ここで測るのは装着契約と再描画要求だけ）。
// --------------------------------------------------------------------------- //
function fakeChart() {
  return { timeScale: () => ({ options: () => ({ barSpacing: 6 }) }) };
}

function fakeSeries() {
  return { priceToCoordinate: () => 100 };
}

// requestUpdate の発行を数える Test Spy。返り値は発行ログ（配列長＝発行回数）。
function attachWithSpy(primitive) {
  const emits = [];
  primitive.attached({
    chart: fakeChart(),
    series: fakeSeries(),
    requestUpdate: () => { emits.push(1); },
  });
  return emits;
}

// 状態設定の対象（クラス・生成・状態設定・要素数の作り方）を 1 か所に持つ。
//   各検定がここを読む＝primitive を足すときに書き足す場所が 1 つで済む。
const STATE_SETTERS = [
  {
    name: 'PairLinesPrimitive.setPairs',
    make: () => new PairLinesPrimitive([]),
    set: (p, items) => p.setPairs(items),
    items: (n) => Array.from({ length: n }, (_, i) => ({
      i, side: 'buy', win: true,
      entry: { time: 100 + i, price: 10 }, exit: { time: 200 + i, price: 20 },
    })),
  },
  {
    name: 'TickvolBandsPrimitive.setRanges',
    make: () => new TickvolBandsPrimitive(),
    set: (p, items) => p.setRanges(items),
    items: (n) => Array.from({ length: n }, (_, i) => ({ from: 100 + i, to: 200 + i })),
  },
  {
    name: 'PriceLevelLinesPrimitive.setLevels',
    make: () => new PriceLevelLinesPrimitive(),
    set: (p, items) => p.setLevels({ direction: 'long', entryPrices: items, stopPrice: 1 }),
    items: (n) => Array.from({ length: n }, (_, i) => 10 + i),
  },
];

// --------------------------------------------------------------------------- //
// R1 継承 — 3 primitive が同じライフサイクル基底の実体である
// --------------------------------------------------------------------------- //

test('R1 3 つの primitive は SeriesPrimitiveLifecycle の実体である（定型の単一ソース化）', () => {
  // Arrange
  const primitives = [
    ['PairLinesPrimitive', new PairLinesPrimitive([])],
    ['TickvolBandsPrimitive', new TickvolBandsPrimitive()],
    ['PriceLevelLinesPrimitive', new PriceLevelLinesPrimitive()],
  ];
  // Act
  const notInherited = primitives
    .filter(([, p]) => !(p instanceof SeriesPrimitiveLifecycle))
    .map(([name]) => name);
  // Assert
  assert.deepEqual(notInherited, [],
    `ライフサイクル定型を基底から継承していません: ${notInherited.join(', ')}`);
});

test('R1 ペア系基底もライフサイクル基底の上に乗る（ペア固有の状態だけを足す）', () => {
  // Arrange / Act
  const base = new PairPrimitiveBase([]);
  // Assert
  assert.ok(base instanceof SeriesPrimitiveLifecycle,
    'PairPrimitiveBase がライフサイクル定型を自前で持ち続けている');
});

test('R1 基底は装着契約（attached / detached / paneViews）を提供する', () => {
  // Arrange
  const primitive = new SeriesPrimitiveLifecycle();
  // Act
  const emits = attachWithSpy(primitive);
  const views = primitive.paneViews();
  primitive.detached();
  // Assert
  assert.equal(emits.length, 0, 'attach 自体が再描画を要求している');
  assert.equal(views.length, 1, 'paneViews() は単一 paneView を返す契約');
  assert.doesNotThrow(() => views[0].renderer().draw(null),
    '基底の描画フックは no-op でなければならない（サブクラスが override する）');
});

// --------------------------------------------------------------------------- //
// R2 ISP/LSP — ペア固有の公開面がペアでない primitive に生えない
// --------------------------------------------------------------------------- //

// 「ペア固有の公開面」の有無を判定する述語（合成クラスで検出力を測れるよう純関数にする）。
function pairOnlyMembers(primitive) {
  return ['setPairs', 'setHighlight'].filter((name) => typeof primitive[name] === 'function');
}

test('R2 ペアでない primitive に setPairs / setHighlight が生えていない（ISP/LSP）', () => {
  // Arrange
  const nonPairPrimitives = [
    ['TickvolBandsPrimitive', new TickvolBandsPrimitive()],
    ['PriceLevelLinesPrimitive', new PriceLevelLinesPrimitive()],
  ];
  // Act
  const leaked = nonPairPrimitives
    .map(([name, p]) => [name, pairOnlyMembers(p)])
    .filter(([, members]) => members.length > 0)
    .map(([name, members]) => `${name}: ${members.join(', ')}`);
  // Assert
  assert.deepEqual(leaked, [],
    `ペア固有の公開面が意味の無い primitive に生えています（PairPrimitiveBase を継承させる案の症状）:\n  ${leaked.join('\n  ')}`);
});

test('R2 検出力: PairPrimitiveBase を継承させた合成クラスは述語に捕捉される（空振りしていない）', () => {
  // Arrange — 却下した「全員に PairPrimitiveBase を継承させる」案の再現。
  class ForcedPairHeir extends PairPrimitiveBase {}
  // Act
  const members = pairOnlyMembers(new ForcedPairHeir([]));
  // Assert
  assert.deepEqual(members, ['setPairs', 'setHighlight'],
    'ペア固有の公開面の漏れを検出できていない（R2 が空振りしている）');
  assert.deepEqual(pairOnlyMembers(new SeriesPrimitiveLifecycle()), [],
    'ライフサイクル基底そのものがペア固有の公開面を持っている');
});

// --------------------------------------------------------------------------- //
// R3 byte 等価 — 抽出の前後で paneView の形（キーの有無まで）が一致する
// --------------------------------------------------------------------------- //

// 抽出前の実測（2026-09-04）。キーの**有無と順序**まで固定する＝zOrder を全員に生やす実装を落とす。
const PANE_VIEW_KEYS = Object.freeze({
  PairLinesPrimitive: ['renderer'],
  TickvolBandsPrimitive: ['renderer', 'zOrder'],
  PriceLevelLinesPrimitive: ['renderer'],
});

test('R3 paneView のキー集合が抽出前の実測と一致する（zOrder はサブクラス宣言のときだけ生える）', () => {
  // Arrange
  const primitives = {
    PairLinesPrimitive: new PairLinesPrimitive([]),
    TickvolBandsPrimitive: new TickvolBandsPrimitive(),
    PriceLevelLinesPrimitive: new PriceLevelLinesPrimitive(),
  };
  // Act
  const observed = Object.fromEntries(
    Object.entries(primitives).map(([name, p]) => [name, Object.keys(p.paneViews()[0])]));
  // Assert
  assert.deepEqual(observed, { ...PANE_VIEW_KEYS },
    'paneView の形が抽出前と食い違っています（キーの有無まで byte 再現する契約）');
});

test('R3 zOrder を宣言した primitive だけが zOrder を返す（宣言フックであること）', () => {
  // Arrange / Act / Assert
  assert.equal(new TickvolBandsPrimitive().paneViews()[0].zOrder(), 'bottom',
    '背景側へ塗る宣言が失われている');
  assert.equal(new PairLinesPrimitive([]).paneViews()[0].zOrder, undefined,
    '宣言していない primitive に zOrder キーが生えている');
  assert.equal(new PriceLevelLinesPrimitive().paneViews()[0].zOrder, undefined,
    '宣言していない primitive に zOrder キーが生えている');
});

test('R3 paneView は単一インスタンス・renderer() は毎回新オブジェクト（現行契約の維持）', () => {
  for (const { name, make } of STATE_SETTERS) {
    // Arrange
    const primitive = make();
    // Act
    const first = primitive.paneViews();
    const second = primitive.paneViews();
    // Assert
    assert.equal(first.length, 1, `${name}: paneViews() が単一でない`);
    assert.equal(first[0], second[0], `${name}: paneView が呼ぶたびに作り直されている`);
    assert.notEqual(first[0].renderer(), first[0].renderer(),
      `${name}: renderer() が使い回されている（現行は毎回新オブジェクト）`);
  }
});

// --------------------------------------------------------------------------- //
// 計算量: 状態設定 1 回あたりの再描画要求 − 必要描画回数(=1) = 0
// --------------------------------------------------------------------------- //
// なぜ在るか（絶対命令 2026-08-28）: 描画結果が正しいままでも、要素ごとに再描画を要求する実装は
//   「作ってから捨てる」浪費であり、状態検証では原理的に落ちない。発行回数そのものは期待値に
//   焼き込まず、**無駄の不在**（発行 − 必要描画 = 0）だけを固定する。

const REQUIRED_REDRAWS_PER_SET = 1; // 状態が 1 回変われば描き直しは 1 回で足りる。

for (const { name, make, set, items } of STATE_SETTERS) {
  test(`C1 計算量: ${name} 1 回の発行 − 必要描画回数 = 0`, () => {
    // Arrange
    const primitive = make();
    const emits = attachWithSpy(primitive);
    // Act
    set(primitive, items(3));
    // Assert
    assert.equal(emits.length - REQUIRED_REDRAWS_PER_SET, 0,
      `捨てられる再描画要求が出ている: emits=${emits.length}`);
  });
}

for (const itemCount of [1, 8]) {
  test(`C2 計算量: 要素を ${itemCount} 件に増やしても発行は増えない（オーダーの表明）`, () => {
    for (const { name, make, set, items } of STATE_SETTERS) {
      // Arrange
      const primitive = make();
      const emits = attachWithSpy(primitive);
      // Act
      set(primitive, items(itemCount));
      // Assert
      assert.equal(emits.length - REQUIRED_REDRAWS_PER_SET, 0,
        `${name}: 発行が要素数に連動している（emits=${emits.length} items=${itemCount}）`);
    }
  });
}

test('C3 計算量: attach 前の状態設定は 1 度も発行しない（座標源が無いので描けない）', () => {
  for (const { name, make, set, items } of STATE_SETTERS) {
    // Arrange
    const primitive = make();
    // Act — attach 前に状態を入れ、そのあとで spy を装着する。
    set(primitive, items(4));
    const emits = attachWithSpy(primitive);
    // Assert
    assert.equal(emits.length, 0,
      `${name}: attach 前の設定が溜め込まれて発行されている（emits=${emits.length}）`);
  }
});

test('C4 計算量ゲートの検出力: 要素ごとに再描画を要求する変異で赤になる', () => {
  // Arrange — 浪費実装の再現（要素の数だけ再描画を要求する）。
  class PerItemRedraw extends SeriesPrimitiveLifecycle {
    setItems(items) {
      for (const _item of items) {
        this._update();
      }
    }
  }
  const primitive = new PerItemRedraw();
  const emits = attachWithSpy(primitive);
  // Act
  primitive.setItems([1, 2, 3, 4, 5, 6, 7, 8]);
  // Assert
  assert.notEqual(emits.length - REQUIRED_REDRAWS_PER_SET, 0,
    '変異を検出できていない（計算量ゲートが空振り）');
});
