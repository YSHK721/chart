// pair_lines_primitive.js（PairLinesPrimitive・ISeriesPrimitive）の仕様検証。
//
// 設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §10.1（ペア線結合）/§10.2（hover 減光）/
//   §10.4（fake scale で単体検証・canvas 実描画はブラウザ）。フェーズ2 v5 API 事実:
//   attached({chart,series,requestUpdate})・paneViews()→renderer().draw(target)→
//   target.useBitmapCoordinateSpace(scope => scope.context で描画)・timeToCoordinate/priceToCoordinate（null 返却あり）。
// 構造: Arrange-Act-Assert。座標変換・useBitmapCoordinateSpace は fake を注入し、描かれた線分の
//   始終点・色・alpha を観測する（実 canvas/実 lwc には依存しない＝C3）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { PairLinesPrimitive } from '../js/adapter/front/pair_lines_primitive.js';

// fake series: priceToCoordinate を price→y の単純写像（null 返却は priceNulls 集合で表現）。
function fakeSeries(priceNulls = new Set()) {
  return {
    priceToCoordinate(price) {
      if (priceNulls.has(price)) return null;
      return price; // y = price（恒等写像で十分・順序のみ検証）
    },
  };
}

// fake chart: timeScale().timeToCoordinate を time→x の単純写像（timeNulls で null 返却）。
function fakeChart(timeNulls = new Set()) {
  return {
    timeScale() {
      return {
        timeToCoordinate(time) {
          if (timeNulls.has(time)) return null;
          return time; // x = time（恒等写像）
        },
      };
    },
  };
}

// fake target: useBitmapCoordinateSpace に scope を渡し、context が記録する描画呼び出しを収集する。
function fakeTarget() {
  const segments = []; // { x1,y1,x2,y2,color,alpha }
  let cur = null;
  const context = {
    set strokeStyle(v) { cur.color = v; },
    set globalAlpha(v) { cur.alpha = v; },
    beginPath() { cur = { alpha: 1 }; },
    moveTo(x, y) { cur.x1 = x; cur.y1 = y; },
    lineTo(x, y) { cur.x2 = x; cur.y2 = y; },
    stroke() { segments.push({ ...cur }); },
    save() {}, restore() {}, set lineWidth(_v) {},
  };
  return {
    segments,
    useBitmapCoordinateSpace(fn) {
      fn({ context, bitmapSize: { width: 800, height: 600 }, horizontalPixelRatio: 1, verticalPixelRatio: 1 });
    },
  };
}

// PairLinesPrimitive を attach 済み状態にして renderer.draw(target) を 1 回実行し segments を返す。
function drawOnce(prim, { chart, series, target }) {
  let updates = 0;
  prim.attached({ chart, series, requestUpdate: () => { updates += 1; } });
  const views = prim.paneViews();
  views.forEach((v) => v.renderer().draw(target));
  return updates;
}

const PAIRS = [
  { i: 0, side: 'buy', win: true, entry: { time: 10, price: 100 }, exit: { time: 20, price: 130 } },
  { i: 1, side: 'sell', win: false, entry: { time: 30, price: 200 }, exit: { time: 40, price: 180 } },
];

test('draws one line segment per pair from (entryTime,entryPrice) to (exitTime,exitPrice)', () => {
  // Arrange
  const prim = new PairLinesPrimitive(PAIRS);
  const target = fakeTarget();
  // Act
  drawOnce(prim, { chart: fakeChart(), series: fakeSeries(), target });
  // Assert: 2 ペア → 2 線分。座標は (time,price) 恒等写像。
  assert.equal(target.segments.length, 2);
  assert.deepEqual(
    target.segments.map((s) => [s.x1, s.y1, s.x2, s.y2]),
    [[10, 100, 20, 130], [30, 200, 40, 180]],
  );
});

test('colors win pairs green and loss pairs red', () => {
  // Arrange
  const prim = new PairLinesPrimitive(PAIRS);
  const target = fakeTarget();
  // Act
  drawOnce(prim, { chart: fakeChart(), series: fakeSeries(), target });
  // Assert: win → #26a69a / loss → #ef5350
  assert.equal(target.segments[0].color, '#26a69a');
  assert.equal(target.segments[1].color, '#ef5350');
});

test('C3: skips a pair when timeToCoordinate returns null for one of its points', () => {
  // Arrange: pair0 の entry time=10 を null にする → pair0 は描画されない
  const prim = new PairLinesPrimitive(PAIRS);
  const target = fakeTarget();
  // Act
  drawOnce(prim, { chart: fakeChart(new Set([10])), series: fakeSeries(), target });
  // Assert: pair0 はスキップ、pair1 のみ描画
  assert.equal(target.segments.length, 1);
  assert.deepEqual([target.segments[0].x1, target.segments[0].x2], [30, 40]);
});

test('C3: skips a pair when priceToCoordinate returns null for one of its points', () => {
  // Arrange: pair1 の exit price=180 を null にする → pair1 は描画されない
  const prim = new PairLinesPrimitive(PAIRS);
  const target = fakeTarget();
  // Act
  drawOnce(prim, { chart: fakeChart(), series: fakeSeries(new Set([180])), target });
  // Assert: pair1 はスキップ、pair0 のみ描画
  assert.equal(target.segments.length, 1);
  assert.deepEqual([target.segments[0].x1, target.segments[0].x2], [10, 20]);
});

test('setHighlight(i) dims non-highlighted lines with low alpha and keeps highlighted at full alpha', () => {
  // Arrange
  const prim = new PairLinesPrimitive(PAIRS);
  const target = fakeTarget();
  prim.attached({ chart: fakeChart(), series: fakeSeries(), requestUpdate: () => {} });
  // Act: i=1 を強調
  prim.setHighlight(1);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: pair0（非ハイライト）は低 alpha、pair1（ハイライト）は alpha=1
  assert.ok(target.segments[0].alpha < 1, '非ハイライト線は減光されるべき');
  assert.equal(target.segments[1].alpha, 1);
});

test('with no highlight (null) all lines are drawn at full alpha', () => {
  // Arrange
  const prim = new PairLinesPrimitive(PAIRS);
  const target = fakeTarget();
  // Act
  drawOnce(prim, { chart: fakeChart(), series: fakeSeries(), target });
  // Assert: _highlight=null は全線 alpha=1
  assert.ok(target.segments.every((s) => s.alpha === 1));
});

test('setHighlight requests an update so the chart re-draws', () => {
  // Arrange
  const prim = new PairLinesPrimitive(PAIRS);
  let updates = 0;
  prim.attached({ chart: fakeChart(), series: fakeSeries(), requestUpdate: () => { updates += 1; } });
  // Act
  prim.setHighlight(1);
  // Assert: requestUpdate が呼ばれる（再描画駆動）
  assert.equal(updates, 1);
});

test('setPairs replaces pairs and requests an update', () => {
  // Arrange
  const prim = new PairLinesPrimitive([]);
  let updates = 0;
  prim.attached({ chart: fakeChart(), series: fakeSeries(), requestUpdate: () => { updates += 1; } });
  const target = fakeTarget();
  // Act
  prim.setPairs(PAIRS);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: 差し替え後の pairs が描画され、requestUpdate も呼ばれる
  assert.equal(updates, 1);
  assert.equal(target.segments.length, 2);
});

test('does not throw when attached is never called (draw is a safe no-op before attach)', () => {
  // Arrange: attach 前（chart/series 未受領）でも throw しない（後方互換・防御）
  const prim = new PairLinesPrimitive(PAIRS);
  const target = fakeTarget();
  // Act / Assert
  assert.doesNotThrow(() => prim.paneViews().forEach((v) => v.renderer().draw(target)));
  assert.equal(target.segments.length, 0); // 座標源が無いので描画なし
});
