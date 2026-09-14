// replay_boundary_dim.test.js — 減光プリミティブの検証（fake chart/series/target 注入・AAA）。
//   参照実装＝プロト replay_boundary_dim.js。lwc 実体には触れず契約（attached/paneViews/_draw）を検証。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ReplayBoundaryDimPrimitive } from '../js/adapter/front/replay_boundary_dim.js';

function fakeTimeScale({ x = 100, barSpacing = 8 } = {}) {
  return {
    timeToCoordinate: (_t) => x,
    options: () => ({ barSpacing }),
  };
}
function fakeChart(ts) { return { timeScale: () => ts }; }
function fakeTarget() {
  const calls = [];
  return {
    calls,
    useBitmapCoordinateSpace(fn) {
      const rects = [];
      fn({
        bitmapSize: { width: 1000, height: 400 },
        horizontalPixelRatio: 1,
        context: { set fillStyle(v) { rects._color = v; }, fillRect: (...a) => rects.push(a) },
      });
      calls.push(rects);
    },
  };
}

test('setBoundaryTime triggers requestUpdate', () => {
  const p = new ReplayBoundaryDimPrimitive();
  let updated = 0;
  p.attached({ chart: fakeChart(fakeTimeScale()), series: {}, requestUpdate: () => { updated += 1; } });
  p.setBoundaryTime(1000);
  assert.equal(updated, 1);
});

test('_draw paints nothing when boundaryTime is null (全期間)', () => {
  const p = new ReplayBoundaryDimPrimitive();
  p.attached({ chart: fakeChart(fakeTimeScale()), series: {}, requestUpdate: () => {} });
  const target = fakeTarget();
  p.setBoundaryTime(null);
  p._draw(target);
  assert.equal(target.calls.length, 0); // useBitmapCoordinateSpace 未呼び出し
});

test('_draw paints [0, xMedia+barSpacing/2) rectangle including the boundary bar body', () => {
  const p = new ReplayBoundaryDimPrimitive();
  p.attached({ chart: fakeChart(fakeTimeScale({ x: 100, barSpacing: 8 })), series: {}, requestUpdate: () => {} });
  const target = fakeTarget();
  p.setBoundaryTime(1000);
  p._draw(target);
  const rect = target.calls[0][0]; // 最初の fillRect の引数
  // 右端 = (100 + 8/2) * ratio(1) = 104。矩形は [0,0,104,400]。
  assert.deepEqual(rect, [0, 0, 104, 400]);
});

test('_draw paints nothing when the boundary is off to the left (right<=0)', () => {
  const p = new ReplayBoundaryDimPrimitive();
  // timeToCoordinate が負の x（境界が左端より左）→ xEdge<=0 → 塗らない。
  p.attached({ chart: fakeChart(fakeTimeScale({ x: -50, barSpacing: 0 })), series: {}, requestUpdate: () => {} });
  const target = fakeTarget();
  p.setBoundaryTime(1000);
  p._draw(target);
  assert.equal(target.calls[0].length, 0); // fillRect 呼ばれず
});

test('paneViews exposes a bottom zOrder view (背景側に描く)', () => {
  const p = new ReplayBoundaryDimPrimitive();
  const view = p.paneViews()[0];
  assert.equal(view.zOrder(), 'bottom');
});
