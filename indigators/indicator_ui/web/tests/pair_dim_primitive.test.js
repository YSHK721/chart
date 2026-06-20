// pair_dim_primitive.js（PairDimPrimitive・ISeriesPrimitive）の仕様検証（v5・§11）。
//
// 設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §11（案A・dimming オーバーレイ primitive）。
//   フェーズ2 確定: 専用 primitive を併設（PairLinesPrimitive に相乗りしない・SRP 分離）。
//   highlight 時のみ、ペア i の [左端, entryX] と [exitX, 右端] の x 帯に半透明暗色矩形を
//   pane 全高（scope.bitmapSize.height）で描画。entryX/exitX は timeScale().timeToCoordinate(time)。
//   座標 null（範囲外）はスキップ。highlight=null は何も描かない。
// 構造: Arrange-Act-Assert。座標変換・useBitmapCoordinateSpace は fake を注入し、塗られた矩形の
//   x/幅/高さ/塗り有無を観測する（実 canvas/実 lwc・実 z 合成はブラウザ委譲＝§11 受入・node:test 範囲外）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { PairDimPrimitive } from '../js/adapter/front/pair_dim_primitive.js';

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

// fake target: useBitmapCoordinateSpace に scope を渡し、fillRect 呼び出しを記録する。
//   bitmapSize は pane 全幅 800 / 全高 600 を表す。
function fakeTarget(width = 800, height = 600) {
  const rects = []; // { x, y, w, h, fillStyle, alpha }
  let fillStyle;
  let alpha = 1;
  const context = {
    set fillStyle(v) { fillStyle = v; },
    get fillStyle() { return fillStyle; },
    set globalAlpha(v) { alpha = v; },
    get globalAlpha() { return alpha; },
    fillRect(x, y, w, h) { rects.push({ x, y, w, h, fillStyle, alpha }); },
    save() {}, restore() {},
  };
  return {
    rects,
    useBitmapCoordinateSpace(fn) {
      fn({
        context,
        bitmapSize: { width, height },
        horizontalPixelRatio: 1,
        verticalPixelRatio: 1,
      });
    },
  };
}

// PairDimPrimitive を attach 済みにして renderer.draw(target) を 1 回実行する。
function drawOnce(prim, { chart, series, target }) {
  let updates = 0;
  prim.attached({ chart, series: series || {}, requestUpdate: () => { updates += 1; } });
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  return updates;
}

const PAIRS = [
  { i: 0, side: 'buy', win: true, entry: { time: 100, price: 1 }, exit: { time: 200, price: 2 } },
  { i: 1, side: 'sell', win: false, entry: { time: 300, price: 3 }, exit: { time: 400, price: 4 } },
];

test('highlight=null draws nothing (no dimming when not hovering)', () => {
  // Arrange
  const prim = new PairDimPrimitive(PAIRS);
  const target = fakeTarget();
  // Act: setHighlight を呼ばない（初期 null）
  drawOnce(prim, { chart: fakeChart(), target });
  // Assert: 矩形 0（何も減光しない）
  assert.equal(target.rects.length, 0);
});

test('setHighlight(i) dims the two x-bands outside the pair: [left,entryX] and [exitX,right]', () => {
  // Arrange: ペア i=0 は entryX=100, exitX=200。pane 幅 800 → 左帯[0,100]・右帯[200,800]。
  const prim = new PairDimPrimitive(PAIRS);
  const target = fakeTarget(800, 600);
  prim.attached({ chart: fakeChart(), series: {}, requestUpdate: () => {} });
  // Act
  prim.setHighlight(0);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: 2 矩形（左帯・右帯）。x/幅で帯を特定する。
  assert.equal(target.rects.length, 2);
  const byX = [...target.rects].sort((a, b) => a.x - b.x);
  // 左帯: x=0, w=100（左端→entryX）
  assert.equal(byX[0].x, 0);
  assert.equal(byX[0].w, 100);
  // 右帯: x=200（exitX）, w=600（→右端 800）
  assert.equal(byX[1].x, 200);
  assert.equal(byX[1].w, 600);
});

test('dim bands span the full pane height (scope.bitmapSize.height)', () => {
  // Arrange
  const prim = new PairDimPrimitive(PAIRS);
  const target = fakeTarget(800, 600);
  prim.attached({ chart: fakeChart(), series: {}, requestUpdate: () => {} });
  // Act
  prim.setHighlight(0);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: 全帯が y=0 から高さ 600（pane 全高）
  assert.ok(target.rects.length > 0);
  for (const r of target.rects) {
    assert.equal(r.y, 0);
    assert.equal(r.h, 600);
  }
});

test('dim bands use a semi-transparent dark fill (alpha < 1)', () => {
  // Arrange
  const prim = new PairDimPrimitive(PAIRS);
  const target = fakeTarget();
  prim.attached({ chart: fakeChart(), series: {}, requestUpdate: () => {} });
  // Act
  prim.setHighlight(0);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: 半透明（alpha<1）で塗られている
  assert.ok(target.rects.length > 0);
  for (const r of target.rects) {
    assert.ok(r.alpha < 1, '減光帯は半透明であるべき');
  }
});

test('C3: skips a band when timeToCoordinate returns null (entryX out of range)', () => {
  // Arrange: ペア0 の entry time=100 を null → 左帯[left,entryX] は算出不能でスキップ。
  //   exitX=200 は有効 → 右帯[200,800] のみ描画。
  const prim = new PairDimPrimitive(PAIRS);
  const target = fakeTarget(800, 600);
  prim.attached({ chart: fakeChart(new Set([100])), series: {}, requestUpdate: () => {} });
  // Act
  prim.setHighlight(0);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: 右帯のみ（1 矩形）
  assert.equal(target.rects.length, 1);
  assert.equal(target.rects[0].x, 200);
  assert.equal(target.rects[0].w, 600);
});

test('C3: skips a band when timeToCoordinate returns null (exitX out of range)', () => {
  // Arrange: ペア0 の exit time=200 を null → 右帯[exitX,right] スキップ。左帯[0,100] のみ。
  const prim = new PairDimPrimitive(PAIRS);
  const target = fakeTarget(800, 600);
  prim.attached({ chart: fakeChart(new Set([200])), series: {}, requestUpdate: () => {} });
  // Act
  prim.setHighlight(0);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: 左帯のみ（1 矩形）
  assert.equal(target.rects.length, 1);
  assert.equal(target.rects[0].x, 0);
  assert.equal(target.rects[0].w, 100);
});

test('clearing highlight (null) restores: no bands are drawn after un-hover', () => {
  // Arrange: 一度 highlight して、その後 null へ戻す（ホバー解除）。
  const prim = new PairDimPrimitive(PAIRS);
  prim.attached({ chart: fakeChart(), series: {}, requestUpdate: () => {} });
  prim.setHighlight(0);
  // Act: 解除
  prim.setHighlight(null);
  const target = fakeTarget();
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: 解除後は何も描かない（復帰）
  assert.equal(target.rects.length, 0);
});

test('setHighlight requests an update so the chart re-draws', () => {
  // Arrange
  const prim = new PairDimPrimitive(PAIRS);
  let updates = 0;
  prim.attached({ chart: fakeChart(), series: {}, requestUpdate: () => { updates += 1; } });
  // Act
  prim.setHighlight(0);
  // Assert
  assert.equal(updates, 1);
});

test('setPairs replaces pairs and requests an update', () => {
  // Arrange
  const prim = new PairDimPrimitive([]);
  let updates = 0;
  prim.attached({ chart: fakeChart(), series: {}, requestUpdate: () => { updates += 1; } });
  // Act
  prim.setPairs(PAIRS);
  // Assert: requestUpdate 駆動。差し替え後 highlight すれば帯が出る。
  assert.equal(updates, 1);
  prim.setHighlight(1);
  const target = fakeTarget(800, 600);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // ペア1: entryX=300, exitX=400 → 左帯[0,300]・右帯[400,800]
  assert.equal(target.rects.length, 2);
});

test('highlight for an unknown index draws nothing (no matching pair)', () => {
  // Arrange: PAIRS に存在しない i=99 を highlight
  const prim = new PairDimPrimitive(PAIRS);
  const target = fakeTarget();
  prim.attached({ chart: fakeChart(), series: {}, requestUpdate: () => {} });
  // Act
  prim.setHighlight(99);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: 一致ペアなし → 何も描かない
  assert.equal(target.rects.length, 0);
});

test('zOrder is "bottom" so dimming sits under markers/pair-lines (above candles is browser-confirmed)', () => {
  // Arrange: §11/方針2 の z 配置（ローソクの上・マーカー/ペア線の下）。実 z 合成はブラウザ委譲だが、
  //   zOrder 契約（"bottom" を返す）は node:test で固定する。
  const prim = new PairDimPrimitive(PAIRS);
  // Act / Assert
  assert.equal(typeof prim.zOrder, 'function');
  assert.equal(prim.zOrder(), 'bottom');
});

test('does not throw when attached is never called (draw is a safe no-op before attach)', () => {
  // Arrange: attach 前（chart 未受領）でも throw しない（後方互換・防御）。highlight 済みでも安全。
  const prim = new PairDimPrimitive(PAIRS);
  prim.setHighlight(0);
  const target = fakeTarget();
  // Act / Assert
  assert.doesNotThrow(() => prim.paneViews().forEach((v) => v.renderer().draw(target)));
  assert.equal(target.rects.length, 0); // 座標源が無いので描画なし
});
