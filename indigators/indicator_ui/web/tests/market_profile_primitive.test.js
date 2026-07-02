// market_profile_primitive.js（MarketProfileHistogramPrimitive）の描画仕様検証。
//
// 設計入力: pair_lines_primitive.test.js を手本（fake target/series で座標・矩形・線分を観測）。
//   v5 primitive 事実: attached({chart,series,requestUpdate})・paneViews()→renderer().draw(target)→
//   target.useBitmapCoordinateSpace(scope=>scope.context 描画)・series.priceToCoordinate（範囲外 null）。
//   実 canvas / 実 lwc には依存しない。構造: Arrange-Act-Assert。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { MarketProfileHistogramPrimitive, heatColor } from '../js/adapter/front/market_profile_primitive.js';

// heatColor（ヒート配色ヘルパ）: norm 0..1 を 青(hue240)→赤(hue0)。境界・防御分岐の回帰網。
test('heatColor: norm=0 は青(hue240)・norm=1 は赤(hue0)', () => {
  assert.equal(heatColor(0), 'hsla(240, 95%, 46%, 0.9)');
  assert.equal(heatColor(1), 'hsla(0, 95%, 58%, 0.9)');
});
test('heatColor: 範囲外/NaN/null は [0,1] にクランプ（0=青 / >1=赤）', () => {
  assert.equal(heatColor(-1), heatColor(0)); // 下限クランプ
  assert.equal(heatColor(2), heatColor(1));  // 上限クランプ
  assert.equal(heatColor(NaN), heatColor(0)); // NaN→0=青
  assert.equal(heatColor(null), heatColor(0)); // null→0=青
});
test('heatColor: alpha 指定が反映される', () => {
  assert.ok(heatColor(0.5, 0.3).endsWith(', 0.3)'));
});

// fake series: price→y 恒等写像（priceNulls の価格は null＝範囲外）。
function fakeSeries(priceNulls = new Set()) {
  return { priceToCoordinate(price) { return priceNulls.has(price) ? null : price; } };
}
const fakeChart = () => ({});

// fake target: fillRect（横バー）と stroke（POC/VA 水平線）を記録する。
function fakeTarget(width = 800) {
  const rects = [];
  const lines = [];
  let cur = null;
  const context = {
    fillStyle: null, strokeStyle: null, globalAlpha: 1, lineWidth: 1,
    fillRect(x, y, w, h) { rects.push({ x, y, w, h, fill: this.fillStyle, alpha: this.globalAlpha }); },
    beginPath() { cur = {}; },
    moveTo(x, y) { cur.x1 = x; cur.y1 = y; },
    lineTo(x, y) { cur.x2 = x; cur.y2 = y; },
    stroke() { lines.push({ ...cur, color: this.strokeStyle }); },
    save() {}, restore() {},
  };
  return {
    rects, lines,
    useBitmapCoordinateSpace(fn) {
      fn({ context, bitmapSize: { width, height: 600 }, horizontalPixelRatio: 1, verticalPixelRatio: 1 });
    },
  };
}

const PROFILE = {
  bins: [
    { price: 100, tpo: 2, norm: 0.5 },
    { price: 101, tpo: 4, norm: 1.0 },
    { price: 102, tpo: 1, norm: 0.25 },
  ],
  poc: 101, va_low: 100, va_high: 101, price_min: 100, price_max: 102,
};

// attach → setProfile → setVisible(true) → draw を実行して target を返す共通手順。
function drawProfile(prim, { chart, series, target, visible = true }) {
  prim.attached({ chart, series, requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(visible);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
}

test('draws one horizontal bar per bin when visible', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget();
  // Act
  drawProfile(prim, { chart: fakeChart(), series: fakeSeries(), target });
  // Assert: 3 bins → 3 バー
  assert.equal(target.rects.length, 3);
});

test('bar widths are proportional to norm and right-aligned to the chart edge', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  // Act
  drawProfile(prim, { chart: fakeChart(), series: fakeSeries(), target });
  // Assert: バーは bins の配列順に描かれる（PROFILE 順 = norm 0.5, 1.0, 0.25）。
  //   バーは価格に対し上下中心化（y = price - barH/2）されるため、y キーではなく描画順で識別する。
  const [mid, wide, narrow] = target.rects; // norm 0.5 / 1.0 / 0.25
  assert.ok(wide.w > mid.w && mid.w > narrow.w, 'バー長は norm に比例すべき');
  for (const r of target.rects) {
    assert.ok(Math.abs((r.x + r.w) - 800) < 1e-6, 'バーは右端に整列すべき');
  }
});

test('draws POC / VAH / VAL horizontal reference lines', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget();
  // Act
  drawProfile(prim, { chart: fakeChart(), series: fakeSeries(), target });
  // Assert: POC(101) / VAH(101) / VAL(100) の 3 本
  assert.equal(target.lines.length, 3);
  const ys = target.lines.map((l) => l.y1).sort((a, b) => a - b);
  assert.deepEqual(ys, [100, 101, 101]);
});

test('draws nothing when not visible (toggle OFF)', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget();
  // Act
  drawProfile(prim, { chart: fakeChart(), series: fakeSeries(), target, visible: false });
  // Assert
  assert.equal(target.rects.length, 0);
  assert.equal(target.lines.length, 0);
});

// ---------------------------------------------------------------------------
// スナップショット（増分2 C）: setSnapshot(true) で累積バーを減光（DIM_ALPHA=0.30）し、
//   today[] を today_max スケールで通常ヒート色で重畳。OFF（既定）は従来の明るい累積バー（不変）。
//   移植元 prototype_260630-01 drawComposite（showToday 時の DIM＋当日強調）。
// ---------------------------------------------------------------------------
const PROFILE_WITH_TODAY = {
  bins: [
    { price: 100, tpo: 2, norm: 0.5 },
    { price: 101, tpo: 4, norm: 1.0 },
    { price: 102, tpo: 1, norm: 0.25 },
  ],
  poc: 101, va_low: 100, va_high: 101, price_min: 100, price_max: 102,
  // 当日ぶん: price 101 のみ滞在（today_max=3）。
  today: [0, 3, 0], today_max: 3,
};

function drawProfileP(prim, profile, { series, target, snapshot = false }) {
  prim.attached({ chart: fakeChart(), series, requestUpdate: () => {} });
  prim.setProfile(profile);
  prim.setVisible(true);
  if (snapshot) prim.setSnapshot(true);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
}

test('snapshot OFF (default): cumulative bars use the normal alpha (0.9) — unchanged behavior', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget();
  // Act: setSnapshot は呼ばない（既定 OFF）
  drawProfileP(prim, PROFILE_WITH_TODAY, { series: fakeSeries(), target });
  // Assert: 全バーが通常アルファ（減光しない）
  assert.equal(target.rects.length, 3);
  for (const r of target.rects) {
    assert.ok(String(r.fill).endsWith(', 0.9)'), '通常時は 0.9 アルファ');
  }
});

test('snapshot ON: cumulative bars are dimmed (DIM_ALPHA=0.30) and today[] bars overlaid at bright alpha', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget();
  // Act
  drawProfileP(prim, PROFILE_WITH_TODAY, { series: fakeSeries(), target, snapshot: true });
  // Assert: 3 累積バー（減光 0.3）＋ 1 当日バー（price101・明るい）
  const dim = target.rects.filter((r) => String(r.fill).includes(', 0.3)'));
  const bright = target.rects.filter((r) => !String(r.fill).includes(', 0.3)'));
  assert.equal(dim.length, 3, '累積バーは全て減光(0.3)');
  assert.equal(bright.length, 1, '当日ぶん(today>0)の 1 本のみ明るく重畳');
});

test('snapshot ON without today[] data: only dims, no overlay bars (no throw)', () => {
  // Arrange: today なしの profile（snapshot ON でも今日バーは描かない）
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget();
  const noToday = { ...PROFILE_WITH_TODAY };
  delete noToday.today; delete noToday.today_max;
  // Act
  assert.doesNotThrow(() => drawProfileP(prim, noToday, { series: fakeSeries(), target, snapshot: true }));
  // Assert: 3 累積バーは減光、当日オーバーレイは無し
  const bright = target.rects.filter((r) => !String(r.fill).includes(', 0.3)'));
  assert.equal(bright.length, 0);
});

test('setSnapshot(false) after ON restores bright cumulative bars', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget();
  prim.attached({ chart: fakeChart(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE_WITH_TODAY);
  prim.setVisible(true);
  prim.setSnapshot(true);
  prim.setSnapshot(false);
  // Act
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: 減光バーが無い（全て通常アルファ）
  const dim = target.rects.filter((r) => String(r.fill).includes(', 0.3)'));
  assert.equal(dim.length, 0);
});

// ---------------------------------------------------------------------------
// リプレイ（増分1）: T 縦線（setCursorTime）。移植元 prototype_260630-01 の遡り時点 T 縦線。
//   chart.timeScale().timeToCoordinate(T) → x を得て、y=0..height の垂直線を描く。null で消える。
// ---------------------------------------------------------------------------
// timeToCoordinate を提供する fake chart（T→x 恒等写像・範囲外は null）。
function fakeChartWithTime(nullTimes = new Set()) {
  return {
    timeScale: () => ({ timeToCoordinate: (t) => (nullTimes.has(t) ? null : t) }),
  };
}

test('setCursorTime(T) draws a vertical line at timeToCoordinate(T) (リプレイ T 縦線)', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChartWithTime(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  // Act: T=250 を設定（POC/VA 3 本 + T 縦線 1 本 = 4 本）。
  prim.setCursorTime(250);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: 垂直線（x1===x2===250・y は上下に伸びる）が 1 本増える。
  const vlines = target.lines.filter((l) => l.x1 === l.x2 && l.x1 === 250);
  assert.equal(vlines.length, 1, 'T=250 の縦線が 1 本描かれる');
  assert.ok(vlines[0].y1 !== vlines[0].y2, '縦線は垂直（y が上下に伸びる）');
});

test('setCursorTime(null) removes the T vertical line (replay OFF で消える)', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChartWithTime(), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  prim.setCursorTime(250);
  // Act: null で消す。
  prim.setCursorTime(null);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: 縦線は無い（POC/VA の 3 本のみ）。
  const vlines = target.lines.filter((l) => l.x1 === l.x2);
  assert.equal(vlines.length, 0, 'T 縦線は消える');
  assert.equal(target.lines.length, 3, 'POC/VA の 3 本のみ');
});

test('setCursorTime skips the line when timeToCoordinate returns null (範囲外 T)', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget(800);
  prim.attached({ chart: fakeChartWithTime(new Set([999])), series: fakeSeries(), requestUpdate: () => {} });
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  // Act: 範囲外 T（timeToCoordinate→null）。
  prim.setCursorTime(999);
  prim.paneViews().forEach((v) => v.renderer().draw(target));
  // Assert: 縦線は描かれない（POC/VA の 3 本のみ）。
  assert.equal(target.lines.length, 3);
});

test('skips a bin whose price is out of range (priceToCoordinate null)', () => {
  // Arrange: price 101 を範囲外にする → その bin と POC/VAH 線は描かれない
  const prim = new MarketProfileHistogramPrimitive();
  const target = fakeTarget();
  // Act
  drawProfile(prim, { chart: fakeChart(), series: fakeSeries(new Set([101])), target });
  // Assert: 描画される bin は 100 と 102 の 2 本
  assert.equal(target.rects.length, 2);
});

test('draw is a safe no-op before attach', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  const target = fakeTarget();
  // Act / Assert
  assert.doesNotThrow(() => prim.paneViews().forEach((v) => v.renderer().draw(target)));
  assert.equal(target.rects.length, 0);
});

test('setProfile and setVisible request an update so the chart re-draws', () => {
  // Arrange
  const prim = new MarketProfileHistogramPrimitive();
  let updates = 0;
  prim.attached({ chart: fakeChart(), series: fakeSeries(), requestUpdate: () => { updates += 1; } });
  // Act
  prim.setProfile(PROFILE);
  prim.setVisible(true);
  // Assert
  assert.equal(updates, 2);
});
