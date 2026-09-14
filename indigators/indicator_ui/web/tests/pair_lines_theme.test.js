// pair_lines_theme.test.js — 売買ペア線をテーマ配線点へ接続する（段階 5-E）。
//
// canvas 描画（ctx.strokeStyle）は CSS カスタムプロパティを解決できないため、CSS 機構は使えない。
//   よって replay_boundary_dim.js と**同じ規律**を採る: 色は注入で受け、未注入時の既定だけを
//   chrome_tokens.js（単一情報源）から引く。配信は ChartRenderer の chrome 購読口が届ける。
//
// 意味の割当（実測）: `pair.win` は当該トレードが勝ちか負けか＝取引の**成果**である。
//   よって profit / loss であって bullish / bearish ではない。同じファイルの中で side（方向）を
//   使っている trade_markers_renderer と対になる分離である。

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { PairLinesPrimitive } from '../js/adapter/front/pair_lines_primitive.js';
import { CHROME_CURRENT, chromeSlot } from '../js/usecase/chrome_tokens.js';
import { resolveChromeSlotColor } from '../js/usecase/color_resolver.js';

const SRC = readFileSync(
  fileURLToPath(new URL('../js/adapter/front/pair_lines_primitive.js', import.meta.url)), 'utf8',
);

function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

// 座標源と描画面の最小 fake。strokeStyle の観測だけが目的。
function fakeScene(pairs) {
  const strokes = [];
  const chart = {
    timeScale: () => ({ timeToCoordinate: (t) => t }),
  };
  const series = { priceToCoordinate: (p) => p };
  const ctx = {
    _stroke: null,
    save() {}, restore() {}, beginPath() {}, moveTo() {}, lineTo() {},
    set strokeStyle(v) { this._stroke = v; },
    get strokeStyle() { return this._stroke; },
    globalAlpha: 1, lineWidth: 1,
    stroke() { strokes.push(this._stroke); },
  };
  const target = { useBitmapCoordinateSpace: (fn) => fn({ context: ctx }) };
  return { chart, series, target, strokes, pairs };
}

function drawWith(primitive, pairs) {
  const s = fakeScene(pairs);
  primitive.attached({ chart: s.chart, series: s.series, requestUpdate: () => {} });
  primitive.setPairs(pairs);
  primitive.paneViews()[0].renderer().draw(s.target);
  return s.strokes;
}

const PAIRS = [
  { i: 0, win: true, entry: { time: 1, price: 2 }, exit: { time: 3, price: 4 } },
  { i: 1, win: false, entry: { time: 5, price: 6 }, exit: { time: 7, price: 8 } },
];

test('TC-PL-T01 恒等: 未注入のペア線は現行リテラル（勝ち #26a69a / 負け #ef5350）で描く', () => {
  // Arrange
  const p = new PairLinesPrimitive([]);
  // Act
  const strokes = drawWith(p, PAIRS);
  // Assert
  assert.deepEqual(strokes, ['#26a69a', '#ef5350']);
});

test('TC-PL-T02 恒等: 既定は台帳の現行値そのもの（値を写経していない）', () => {
  // Arrange / Act
  const strokes = drawWith(new PairLinesPrimitive([]), PAIRS);
  // Assert
  assert.deepEqual(strokes, [CHROME_CURRENT.pairLineWin, CHROME_CURRENT.pairLineLoss]);
});

test('TC-PL-T03 通過条件 2: pair_lines_primitive.js に素の色リテラルが残っていない', () => {
  // Arrange
  const code = stripComments(SRC);
  // Act
  const found = [...code.matchAll(/#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|hsla?\([^)]*\)/g)]
    .map((m) => m[0]).filter((v) => !v.includes('${'));
  // Assert
  assert.deepEqual(found, [], `リテラルが残っている: ${found.join(' / ')}`);
});

test('TC-PL-T04 配信された色で描く（テーマ適用がペア線へ届く）', () => {
  // Arrange
  const p = new PairLinesPrimitive([]);
  // Act
  p.setChromeColors({ pairLineWin: '#111111', pairLineLoss: '#222222' });
  const strokes = drawWith(p, PAIRS);
  // Assert
  assert.deepEqual(strokes, ['#111111', '#222222']);
});

test('TC-PL-T05 配信は全域的（未知・非文字列・部分指定でも例外を投げず現行を保つ）', () => {
  // Arrange
  const p = new PairLinesPrimitive([]);
  // Act / Assert: 例外を投げない。
  assert.doesNotThrow(() => p.setChromeColors(null));
  assert.doesNotThrow(() => p.setChromeColors({}));
  assert.doesNotThrow(() => p.setChromeColors({ pairLineWin: 42 }));
  // 不正値では現行値を保つ（描画を壊さない）。
  assert.deepEqual(drawWith(p, PAIRS), [CHROME_CURRENT.pairLineWin, CHROME_CURRENT.pairLineLoss]);
  // 片側だけの指定は片側だけ効く（部分マージ）。
  p.setChromeColors({ pairLineLoss: '#333333' });
  assert.deepEqual(drawWith(p, PAIRS), [CHROME_CURRENT.pairLineWin, '#333333']);
});

test('TC-PL-T06 通過条件 5: ペア線は成果（profit / loss）を読み、方向（bullish / bearish）ではない', () => {
  // `pair.win` は勝ち負け＝成果である。方向へ束ねると、ローソクの陽線色を変えただけで
  //   勝ちトレードの線が動く（意味の異なるものが連動する）。
  // Arrange / Act / Assert
  assert.equal(chromeSlot('pairLineWin').token, 'profit');
  assert.equal(chromeSlot('pairLineLoss').token, 'loss');
  assert.equal(chromeSlot('pairLineWin').mechanism, 'js');
  assert.equal(chromeSlot('pairLineLoss').mechanism, 'js');
});

test('TC-PL-T07 テーマが profit を宣言するとペア線の解決値が変わる（bullish 宣言では変わらない）', () => {
  // 分離が「名前だけ」でないことの実証。解決まで通して確かめる。
  // Arrange
  const themeProfit = { roleColors: { profit: '#0a0a0a', loss: '#0b0b0b' } };
  const themeBullish = { roleColors: { bullish: '#0a0a0a', bearish: '#0b0b0b' } };
  // Act / Assert
  assert.equal(resolveChromeSlotColor({ slotId: 'pairLineWin', theme: themeProfit }), '#0a0a0a');
  assert.equal(resolveChromeSlotColor({ slotId: 'pairLineLoss', theme: themeProfit }), '#0b0b0b');
  assert.equal(resolveChromeSlotColor({ slotId: 'pairLineWin', theme: themeBullish }),
    CHROME_CURRENT.pairLineWin, '方向の宣言が成果の線を動かしている');
});
