// tickvol_bands_primitive.js（TickvolBandsPrimitive・ISeriesPrimitive）の仕様検証。
//
// 取引密度が濃い時刻帯のチャートパネル背景を塗るプリミティブ。zOrder='bottom'（系列の下＝背景側）で
// 塗るため、ローソク・指標線は原色のまま上に残る。座標変換と useBitmapCoordinateSpace は fake を
// 注入し、塗られた矩形の左右端・色を観測する（実 canvas / 実 lwc に依存しない）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { TickvolBandsPrimitive } from '../js/adapter/front/tickvol_bands_primitive.js';

// fake timeScale: index 経路（timeToIndex→logicalToCoordinate）と座標経路（timeToCoordinate）を
//   個別に無効化できるようにし、退避動作を検証できるようにする。
function fakeChart({ indexPath = true, coordPath = true, barSpacing = 10, nulls = new Set() } = {}) {
  const ts = { options: () => ({ barSpacing }) };
  if (indexPath) {
    ts.timeToIndex = (time) => (nulls.has(time) ? null : time);
    ts.logicalToCoordinate = (i) => (nulls.has(i) ? null : i);
  }
  if (coordPath) {
    ts.timeToCoordinate = (time) => (nulls.has(time) ? null : time + 1000); // 経路の区別用に +1000
  }
  return { timeScale: () => ts };
}

function fakeTarget(width = 800, height = 600, ratio = 1) {
  const rects = [];
  let fill = null;
  const context = {
    set fillStyle(v) { fill = v; },
    get fillStyle() { return fill; },
    fillRect(x, y, w, h) { rects.push({ x, y, w, h, color: fill }); },
  };
  return {
    rects,
    useBitmapCoordinateSpace(fn) {
      fn({ context, bitmapSize: { width, height }, horizontalPixelRatio: ratio, verticalPixelRatio: ratio });
    },
  };
}

function drawOnce(prim, chart, target) {
  let updates = 0;
  prim.attached({ chart, series: {}, requestUpdate: () => { updates += 1; } });
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  return updates;
}

// --- zOrder（背景側） ---------------------------------------------------------- //
test('paints at zOrder bottom so candles stay on top', () => {
  const prim = new TickvolBandsPrimitive();
  assert.equal(prim.paneViews()[0].zOrder(), 'bottom');
});

// --- 塗り範囲 ------------------------------------------------------------------ //
test('fills a full-height rect widened by half a bar on each side', () => {
  const prim = new TickvolBandsPrimitive();
  const target = fakeTarget();
  prim.setRanges([{ from: 100, to: 200 }]);
  drawOnce(prim, fakeChart({ barSpacing: 10 }), target);
  // x は timeToIndex→logicalToCoordinate（恒等）＝100..200。端バーのスロット全体を含めるため ±5。
  assert.deepEqual(target.rects, [{ x: 95, y: 0, w: 110, h: 600, color: 'rgba(41, 98, 255, 0.07)' }]);
});

test('a single-bar band still paints one bar wide', () => {
  const prim = new TickvolBandsPrimitive();
  const target = fakeTarget();
  prim.setRanges([{ from: 100, to: 100 }]);
  drawOnce(prim, fakeChart({ barSpacing: 10 }), target);
  assert.deepEqual(target.rects.map((r) => [r.x, r.w]), [[95, 10]]);
});

test('multiple bands are painted independently', () => {
  const prim = new TickvolBandsPrimitive();
  const target = fakeTarget();
  prim.setRanges([{ from: 100, to: 150 }, { from: 300, to: 320 }]);
  drawOnce(prim, fakeChart({ barSpacing: 0 }), target);
  assert.deepEqual(target.rects.map((r) => [r.x, r.w]), [[100, 50], [300, 20]]);
});

test('coordinates are scaled by the horizontal pixel ratio and clamped to the canvas', () => {
  const prim = new TickvolBandsPrimitive();
  const target = fakeTarget(500, 400, 2);
  prim.setRanges([{ from: 100, to: 400 }]); // ×2 で 200..800 → 幅 500 でクランプ
  drawOnce(prim, fakeChart({ barSpacing: 0 }), target);
  assert.deepEqual(target.rects, [{ x: 200, y: 0, w: 300, h: 400, color: 'rgba(41, 98, 255, 0.07)' }]);
});

// --- 座標経路の退避 ------------------------------------------------------------ //
test('falls back to timeToCoordinate when the index path is unavailable', () => {
  const prim = new TickvolBandsPrimitive();
  const target = fakeTarget(2000);
  prim.setRanges([{ from: 100, to: 200 }]);
  drawOnce(prim, fakeChart({ indexPath: false, barSpacing: 0 }), target);
  assert.deepEqual(target.rects.map((r) => [r.x, r.w]), [[1100, 100]]); // +1000＝座標経路
});

test('a band whose bars are not in the current data is skipped, not painted at 0', () => {
  const prim = new TickvolBandsPrimitive();
  const target = fakeTarget();
  prim.setRanges([{ from: 100, to: 200 }, { from: 300, to: 320 }]);
  drawOnce(prim, fakeChart({ coordPath: false, barSpacing: 0, nulls: new Set([100]) }), target);
  assert.deepEqual(target.rects.map((r) => [r.x, r.w]), [[300, 20]]);
});

// --- 空・未 attach ------------------------------------------------------------- //
test('draws nothing before attach or with no ranges', () => {
  const prim = new TickvolBandsPrimitive();
  const target = fakeTarget();
  prim.paneViews().forEach((v) => v.renderer().draw(target)); // attach 前
  assert.deepEqual(target.rects, []);
  drawOnce(prim, fakeChart(), target);                        // ranges 空
  assert.deepEqual(target.rects, []);
});

test('setRanges requests a redraw and clearing it turns the bands off', () => {
  const prim = new TickvolBandsPrimitive();
  const target = fakeTarget();
  const updates = drawOnce(prim, fakeChart({ barSpacing: 0 }), target);
  assert.equal(updates, 0);
  prim.setRanges([{ from: 10, to: 20 }]);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  assert.equal(target.rects.length, 1);
  prim.setRanges([]);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  assert.equal(target.rects.length, 1, '消灯後は追加で塗らない');
});

test('detached stops drawing (no stale chart reference)', () => {
  const prim = new TickvolBandsPrimitive();
  const target = fakeTarget();
  prim.setRanges([{ from: 10, to: 20 }]);
  drawOnce(prim, fakeChart({ barSpacing: 0 }), target);
  assert.equal(target.rects.length, 1);
  prim.detached();
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  assert.equal(target.rects.length, 1);
});
