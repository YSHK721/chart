// chart_renderer.priceAtCoordinate(y) の仕様検証（ISSUE-368 スライス 3）。
//
// 設計入力: 設計書 出力 3 スライス 3（「内部 coordinateToPrice の公開化。lwc API 名は隔離点に留まる」）。
// 由来: y 座標→価格の公開変換がチャート側に存在せず、水準線 drag（スライス 4）が
//   価格を得る手段を持たない。既存の `coordinateToPrice` 呼び出しは
//   `chart_renderer.js` の `_onCrosshairMove` 内と `scale_controller.js` の内部利用だけで、
//   いずれも外から呼べない（実測）。
// 観点: 既存 `_onCrosshairMove` と**同一の解決手順**（メイン系列の coordinateToPrice・
//   null は null のまま・数値化）を公開面でも守ること。upstream の API 名は本ファイル内に留まる
//   （呼び出し側は priceAtCoordinate しか知らない）。
// 構造: Arrange-Act-Assert。chart / mainSeries は Fake（DOM・実描画非依存）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ChartRenderer } from '../js/adapter/front/chart_renderer.js';

// 最小の Fake（本テストが触れる面だけを持つ。ChartRenderer は constructor で描画しない）。
function build({ mainSeries } = {}) {
  const chart = {
    subscribeCrosshairMove() {},
    applyOptions() {},
    timeScale: () => ({ width: () => 800, subscribeVisibleLogicalRangeChange() {} }),
  };
  return new ChartRenderer({ chart, mainSeries, lwc: {} });
}

test('priceAtCoordinate は メイン系列の座標→価格変換の結果を数値で返す', () => {
  // Arrange
  const seen = [];
  const renderer = build({
    mainSeries: { coordinateToPrice: (y) => { seen.push(y); return 58700.5; } },
  });
  // Act
  const price = renderer.priceAtCoordinate(120);
  // Assert
  assert.equal(price, 58700.5);
  assert.deepEqual(seen, [120], '受け取った y をそのまま渡す（補正を挟まない）');
});

test('priceAtCoordinate は 文字列を返す upstream 実装でも数値へ正規化する', () => {
  // Arrange（_onCrosshairMove :613 が Number(price) している契約と揃える）
  const renderer = build({ mainSeries: { coordinateToPrice: () => '58700.5' } });
  // Act / Assert
  assert.strictEqual(renderer.priceAtCoordinate(10), 58700.5);
});

test('可視範囲外（upstream が null）は null を返す（0 へ倒さない）', () => {
  // Arrange
  const renderer = build({ mainSeries: { coordinateToPrice: () => null } });
  // Act / Assert
  assert.strictEqual(renderer.priceAtCoordinate(-5), null);
});

test('メイン系列が無い／変換 API 非提供でも例外を投げず null を返す（防御）', () => {
  // Arrange / Act / Assert
  assert.strictEqual(build({ mainSeries: null }).priceAtCoordinate(10), null);
  assert.strictEqual(build({ mainSeries: {} }).priceAtCoordinate(10), null);
});

test('非有限な y は変換を呼ばず null（NaN 価格を下流へ流さない）', () => {
  // Arrange
  let calls = 0;
  const renderer = build({ mainSeries: { coordinateToPrice: () => { calls += 1; return 1; } } });
  // Act / Assert
  assert.strictEqual(renderer.priceAtCoordinate(Number.NaN), null);
  assert.strictEqual(renderer.priceAtCoordinate(Number.POSITIVE_INFINITY), null);
  assert.equal(calls, 0);
});
