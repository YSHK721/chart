// snap_price_resolver.js（domain）— クリック価格のスナップ解決（ISSUE-368 スライス 8-a）のテスト。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   「追加要件裁定 R-P2」（クリック点の近傍（px 許容内）にある表示中の全指標系列の値（当該足）と
//    ローソク OHLC（当該足）を候補とし、最も近い値へスナップ。近傍に候補が無ければ**素の
//    クリック価格**を採用＝任意の場所で入力できる）、
//   「R-P3」（右クリックメニューも同一のスナップ規則を使う＝解決器の単一ソース）、
//   §10 YAGNI（`SnapPolicy` 抽象は削除済み＝戦略化しない純関数 1 本）。
//
// 構造: Arrange-Act-Assert（AAA）。依存ゼロ（DOM・lwc・fetch を触らない純関数）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { resolveSnappedPrice } from '../js/domain/snap_price_resolver.js';

const CANDIDATES = [
  { kind: 'series', label: 'sma20', price: 58800 },
  { kind: 'ohlc', label: '高値', price: 58900 },
  { kind: 'level', label: 'POC', price: 58650 },
];

test('TC-SP01 近傍の候補が複数あるとき最も近い価格へスナップする（R-P2）', () => {
  // Arrange: クリック価格 58790 に最も近いのは sma20（58800・距離 10）。
  // Act
  const got = resolveSnappedPrice(CANDIDATES, 58790, 50);
  // Assert
  assert.equal(got.price, 58800);
  assert.equal(got.snapped, true);
  assert.equal(got.candidate.label, 'sma20');
});

test('TC-SP02 候補 0 件は素のクリック価格を返す（任意の場所で入力できる・R-P2）', () => {
  // Arrange: 指標も水準線も無い（ローソクの無い座標は列挙側が null を返すため候補配列は空）。
  // Act
  const got = resolveSnappedPrice([], 58790, 50);
  // Assert
  assert.equal(got.price, 58790);
  assert.equal(got.snapped, false, '候補が無いのにスナップ扱いにしてはならない');
  assert.equal(got.candidate, null);
});

test('TC-SP03 最近傍が許容を超えるときは素のクリック価格（近傍に候補が無い扱い・R-P2）', () => {
  // Arrange: 最寄り（58800）まで 60・許容 50。
  // Act
  const got = resolveSnappedPrice(CANDIDATES, 58740, 50);
  // Assert
  assert.equal(got.price, 58740);
  assert.equal(got.snapped, false);
  assert.equal(got.candidate, null);
});

test('TC-SP04 境界値: 距離が許容ちょうどはスナップする（許容は閉区間）', () => {
  // Arrange: 58750 → sma20（58800）までちょうど 50。
  // Act
  const got = resolveSnappedPrice(CANDIDATES, 58750, 50);
  // Assert
  assert.equal(got.snapped, true, '許容ちょうどを落とすと「1px 手前でしか吸わない」挙動になる');
  assert.equal(got.price, 58800);
});

test('TC-SP05 非有限のクリック価格は null（黙って 0 や NaN を下流へ流さない）', () => {
  // Arrange: priceAtCoordinate は可視範囲外で null を返す＝呼び出し側が null/NaN を渡し得る。
  // Act / Assert
  assert.equal(resolveSnappedPrice(CANDIDATES, NaN, 50), null);
  assert.equal(resolveSnappedPrice(CANDIDATES, null, 50), null);
});

test('TC-SP10 有限のクリック価格なら必ず非 null を返す（呼び出し側に null 分岐を作らせない契約）', () => {
  // 契約の明文化: null になるのは TC-SP05 の非有限入力のときだけ。price_pick_resolver は
  //   自分で有限性を確かめてから本関数を呼ぶため、戻り値の null 判定は到達不能な死んだ分岐になる。
  //   ここが崩れる（有限入力で null を返す）変更が入ったら、その分岐の不在が実バグになる。
  // Arrange: 候補あり／なし・許容の内外・許容が非有限、の全経路を有限価格で通す。
  const cases = [
    [CANDIDATES, 58790, 50], [CANDIDATES, 58740, 50], [[], 58790, 50],
    [null, 58790, 50], [CANDIDATES, 58800, NaN], [CANDIDATES, 0, 0],
  ];
  // Act / Assert
  for (const [candidates, price, tolerance] of cases) {
    const got = resolveSnappedPrice(candidates, price, tolerance);
    assert.notEqual(got, null, `有限価格 ${price} で null が返った（呼び出し側の前提が崩れる）`);
    assert.equal(Number.isFinite(got.price), true);
  }
});

test('TC-SP06 候補が配列でないときは素のクリック価格（例外にしない）', () => {
  // Arrange: 列挙側（renderer）が非対応環境で null を返す経路。
  // Act
  const got = resolveSnappedPrice(null, 58790, 50);
  // Assert
  assert.deepEqual(got, { price: 58790, snapped: false, candidate: null });
});

test('TC-SP07 許容が非有限のときはスナップしない（フェイルクローズ）', () => {
  // Arrange: px→価格の換算が失敗すると NaN が来る（priceAtCoordinate が null のとき）。
  // Act
  const got = resolveSnappedPrice(CANDIDATES, 58800, NaN);
  // Assert
  assert.equal(got.snapped, false, '換算不能な許容で吸わせると、意図しない価格が入力される');
  assert.equal(got.price, 58800);
});

test('TC-SP08 値の無い候補（非有限 price）は候補にしない（warmup 中の指標）', () => {
  // Arrange: その足に値が無い指標は undefined（barInfoAt と同じ規約＝最新値へ落ちない）。
  const withHole = [{ kind: 'series', label: 'sma200', price: undefined }, ...CANDIDATES];
  // Act
  const got = resolveSnappedPrice(withHole, 58790, 50);
  // Assert
  assert.equal(got.price, 58800);
  assert.equal(got.candidate.label, 'sma20');
});

test('TC-SP09 同距離は候補配列の先頭が勝つ（順序規則を固定＝解決が毎回同じになる）', () => {
  // Arrange: クリック 58800 の左右に等距離（±20）で 2 候補。並び順だけが優劣を決める。
  const tie = [
    { kind: 'series', label: '先に列挙', price: 58820 },
    { kind: 'level', label: '後に列挙', price: 58780 },
  ];
  // Act
  const got = resolveSnappedPrice(tie, 58800, 50);
  // Assert
  assert.equal(got.candidate.label, '先に列挙', '同距離の勝者が並び順で決まらないと、同じ操作が別の価格を入れる');
  assert.equal(got.price, 58820);
  // 逆順に列挙すれば逆の候補が勝つ（規則が「先頭優先」であって値の大小ではないことの固定）。
  assert.equal(resolveSnappedPrice([tie[1], tie[0]], 58800, 50).candidate.label, '後に列挙');
});
