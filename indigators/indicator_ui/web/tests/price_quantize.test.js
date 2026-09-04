// price_quantize.test.js — 価格の量子化（呼び値の刻みへの丸め）の検定（ISSUE-368 スライス S-3）。
//
// 設計入力: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md「追補: 工程 2」E-4 / S-3。
//   除去する原因 β＝「丸めの適用点が 7 経路に散っている」。量子化の式はこの 1 本だけに置き、
//   PriceLevels（E-02）の構築・更新口から呼ばれる（関門を domain に置く）。
//
// 固定した規則（S-3 通過条件）:
//   - quantize(p, tick) = 最近傍の刻み。浮動小数残差を残さない（8568.900000000001 を出さない）。
//   - null / undefined / 非有限（NaN・±Infinity）は素通し。
//   - tick が null / 未指定のときも素通し（後方互換の既定＝既存の呼び出し側を壊さない）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { quantize } from '../js/domain/price_quantize.js';

test('TC-Q01 quantize は tick=1 のとき最近傍の刻みへ丸める（58998.75 → 58999）', () => {
  // Arrange / Act
  const actual = quantize(58998.75, 1);
  // Assert
  assert.equal(actual, 58999);
});

test('TC-Q02 quantize は tick=1 のときチャート由来の生値を表示値と一致させる（62707.710070965324 → 62708）', () => {
  // Arrange: 本ブランチで実際に食い違った値（ラベルは 62,708・モデルには生値が入っていた）
  const raw = 62707.710070965324;
  // Act
  const actual = quantize(raw, 1);
  // Assert
  assert.equal(actual, 62708);
});

test('TC-Q03 quantize は tick=0.1 のとき浮動小数残差を残さない（8568.89 → 8568.9 に厳密一致）', () => {
  // Act / Assert: 指示の明示要件
  assert.equal(quantize(8568.89, 0.1), 8568.9);

  // Arrange: 素朴な式 Math.round(p / tick) * tick が残差を出す実測値（node v24 実測）
  //   Math.round(8568.84 / 0.1) * 0.1 === 8568.800000000001
  //   Math.round(70.12   / 0.1) * 0.1 === 70.10000000000001
  assert.notEqual(Math.round(8568.84 / 0.1) * 0.1, 8568.8, '前提: 素朴な式には残差がある');
  assert.notEqual(Math.round(70.12 / 0.1) * 0.1, 70.1, '前提: 素朴な式には残差がある');
  // Act / Assert: 量子化後は残差が無い（文字列化にも現れない）
  assert.equal(quantize(8568.84, 0.1), 8568.8);
  assert.equal(String(quantize(8568.84, 0.1)), '8568.8');
  assert.equal(quantize(70.12, 0.1), 70.1);
  assert.equal(String(quantize(70.12, 0.1)), '70.1');
});

test('TC-Q04 quantize は刻み上の値を変えない（冪等）', () => {
  // Arrange / Act / Assert
  assert.equal(quantize(58999, 1), 58999);
  assert.equal(quantize(quantize(58998.75, 1), 1), 58999);
  assert.equal(quantize(8568.9, 0.1), 8568.9);
});

test('TC-Q05 quantize は刻みのちょうど半分の境界で上側へ丸める（Math.round と同じ規則）', () => {
  // Arrange / Act / Assert（境界値: 0.5 / -0.5）
  assert.equal(quantize(58998.5, 1), 58999);
  assert.equal(quantize(58997.5, 1), 58998);
  assert.equal(quantize(0.05, 0.1), 0.1);
});

test('TC-Q06 quantize は 0 を 0 のまま返す（境界値）', () => {
  assert.equal(quantize(0, 1), 0);
  assert.equal(quantize(0, 0.1), 0);
});

test('TC-Q07 quantize は null / undefined を素通しする（無音で 0 へ倒さない）', () => {
  assert.equal(quantize(null, 1), null);
  assert.equal(quantize(undefined, 1), undefined);
});

test('TC-Q08 quantize は非有限（NaN・±Infinity）を素通しする', () => {
  assert.ok(Number.isNaN(quantize(NaN, 1)), 'NaN は NaN のまま');
  assert.equal(quantize(Infinity, 1), Infinity);
  assert.equal(quantize(-Infinity, 1), -Infinity);
});

test('TC-Q09 quantize は tick が null / 未指定なら素通しする（後方互換の既定）', () => {
  assert.equal(quantize(58998.75, null), 58998.75);
  assert.equal(quantize(58998.75, undefined), 58998.75);
  assert.equal(quantize(58998.75), 58998.75);
});

test('TC-Q10 quantize は指数表記になる細かい刻みでも最近傍の刻みへ丸める（無音で誤答しない）', () => {
  // Arrange: JS は 1e-6 未満の数を指数表記で文字列化する（String(1e-7) === '1e-7'・node v24 実測）。
  //   小数桁数を String から導く実装は、この境界で桁数 0 と誤り「1.23456789 → 1」を無音で返す。
  assert.equal(String(0.000001), '0.000001', '前提: 1e-6 は通常表記');
  assert.equal(String(0.0000001), '1e-7', '前提: 1e-7 は指数表記');
  // Act / Assert: 刻みの倍数であること（規則「最近傍の刻み」は tick の大きさに依らない）
  assert.equal(quantize(1.23456789, 0.000001), 1.234568);
  assert.equal(quantize(1.23456789, 0.0000001), 1.2345679);
  assert.equal(quantize(0.00000015, 0.0000001), 0.0000002);
});
