// market_profile_theme.test.js — Market Profile の描画色をテーマ配線点へ接続する（段階 5-E）。
//
// 範囲（ISSUE-360 の射程を正しく切る）:
//   同 ISSUE が対象外にしたのは `heatColor()` の HSL 色相ランプ **ただ 1 つ**である。色相環を
//   240°→0° 掃引するランプは、両端 2 色を与えても中間の経路が一意に定まらないため、端点 2 色の
//   線形補間へ潰すと中間色が変わる（D-11 恒等テーマを MP について破る）。よって heatColor は
//   現状維持とし、**それ以外の 16 色**を配線点化する。
//
// 新語はゼロ。実測でチャネルを取ると、8 色が既存トークンの低不透明度そのものである:
//   rgba(38,166,154, ...) = #26a69a = bullish   /  rgba(239,83,80, ...) = #ef5350 = bearish
//
// 機構は 'js'（canvas は CSS 変数を解決できない）。色は注入で受け、未注入時の既定だけを
//   台帳から引く（replay_boundary_dim / pair_lines と同じ規律）。

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
  MarketProfileHistogramPrimitive, heatColor,
} from '../js/adapter/front/market_profile_primitive.js';
import { CHROME_CURRENT, chromeSlot } from '../js/usecase/chrome_tokens.js';

const SRC = readFileSync(
  fileURLToPath(new URL('../js/adapter/front/market_profile_primitive.js', import.meta.url)), 'utf8',
);

function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

// 段階 5-E で MP に足す配線点と、その現行リテラル（**接続前のコードからの実測値**）。
const MP_SLOTS = [
  ['mpPocLine', '#ff3b3b', 'alert'],
  ['mpPocStar', '#ffd54a', 'alert'],
  ['mpVaLine', 'rgba(168, 41, 174, 0.5)', 'range'],
  ['mpCursorLine', 'rgba(120, 190, 255, 0.9)', 'highlight'],
  ['mpSessTintUp', 'rgba(38, 166, 154, 0.12)', 'bullish'],
  ['mpSessTintDown', 'rgba(239, 83, 80, 0.12)', 'bearish'],
  ['mpOhlcUp', 'rgba(38, 166, 154, 0.8)', 'bullish'],
  ['mpOhlcDown', 'rgba(239, 83, 80, 0.8)', 'bearish'],
  ['mpSessPoc', 'rgba(255,255,255,0.95)', 'text'],
  ['mpTfpBgUp', 'rgba(38, 166, 154, 0.1)', 'bullish'],
  ['mpTfpBgDown', 'rgba(239, 83, 80, 0.1)', 'bearish'],
  ['mpTfpBgUpDim', 'rgba(38, 166, 154, 0.04)', 'bullish'],
  ['mpTfpBgDownDim', 'rgba(239, 83, 80, 0.04)', 'bearish'],
  ['mpStripeOdd', 'rgba(255,255,255,.05)', 'text'],
  ['mpStripeEven', 'rgba(255,255,255,.015)', 'text'],
  ['mpDateLabel', 'rgba(154,164,178,.6)', 'text'],
];

test('TC-MP-T01 恒等: MP の 16 配線点は接続前の実測リテラルを逐語で保つ', () => {
  // Arrange / Act / Assert
  for (const [id, current] of MP_SLOTS) {
    assert.equal(CHROME_CURRENT[id], current, `${id}: 現行リテラルが変わっている`);
  }
});

test('TC-MP-T02 通過条件 5: 新語ゼロ（16 点はすべて既存トークンへ割れている）', () => {
  // Arrange / Act / Assert
  for (const [id, , token] of MP_SLOTS) {
    const slot = chromeSlot(id);
    assert.ok(slot, `${id}: 台帳に無い`);
    assert.equal(slot.token, token, `${id}: 束ねるトークンが違う`);
    assert.equal(slot.mechanism, 'js', `${id}: canvas 描画なので機構は js`);
  }
});

test('TC-MP-T03 実測: 方向色 8 点は bullish / bearish の低不透明度そのもの（新語が要らない根拠）', () => {
  // 「新語ゼロで足りる」と主張する以上、チャネルが既存トークンと一致することを実測で示す。
  const rgbOf = (v) => /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/.exec(v).slice(1, 4).map(Number);
  const hexOf = (v) => `#${rgbOf(v).map((c) => c.toString(16).padStart(2, '0')).join('')}`;
  for (const [id, current, token] of MP_SLOTS) {
    if (token !== 'bullish' && token !== 'bearish') continue;
    const expected = token === 'bullish' ? '#26a69a' : '#ef5350';
    assert.equal(hexOf(current), expected, `${id}: ${current} が ${token} の色でない`);
  }
});

test('TC-MP-T04 通過条件 2: market_profile_primitive.js に素の色リテラルが残っていない（heatColor を除く）', () => {
  // Arrange: heatColor の hsla テンプレートは ISSUE-360 により対象外（色相ランプ）。
  //   テンプレート展開（`${`）を含むものは値ではなく組み立て式なので除外する。
  const code = stripComments(SRC);
  // Act
  const strings = [...code.matchAll(/#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|hsla?\([^)]*\)/g)]
    .map((m) => m[0]).filter((v) => !v.includes('${'));
  const arrays = [...code.matchAll(/\[\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\]/g)]
    .filter((m) => [1, 2, 3].every((i) => Number(m[i]) <= 255)).map((m) => m[0]);
  // Assert
  assert.deepEqual(strings, [], `文字列の色リテラルが残っている: ${strings.join(' / ')}`);
  assert.deepEqual(arrays, [], `チャネル配列の色リテラルが残っている: ${arrays.join(' / ')}`);
});

test('TC-MP-T05 恒等: heatColor は現状維持（ISSUE-360 の射程・色相ランプは潰さない）', () => {
  // 接続前の実装（hue 240*(1-t) / light 46+12t / alpha 0.9）の出力そのもの。
  // Arrange / Act / Assert: 境界値（0・1）と中間。
  assert.equal(heatColor(0), 'hsla(240, 95%, 46%, 0.9)');
  assert.equal(heatColor(1), 'hsla(0, 95%, 58%, 0.9)');
  assert.equal(heatColor(0.5), 'hsla(120, 95%, 52%, 0.9)');
  // 範囲外入力のクランプ（異常系）も接続前と同一。
  assert.equal(heatColor(-1), 'hsla(240, 95%, 46%, 0.9)');
  assert.equal(heatColor(2), 'hsla(0, 95%, 58%, 0.9)');
});

test('TC-MP-T06 配信: setChromeColors で色を受け取り、未指定は現行を保つ（全域的）', () => {
  // Arrange
  const p = new MarketProfileHistogramPrimitive();
  // Act / Assert: 例外を投げない。
  assert.doesNotThrow(() => p.setChromeColors(null));
  assert.doesNotThrow(() => p.setChromeColors({}));
  assert.doesNotThrow(() => p.setChromeColors({ mpPocLine: 42 }));
  assert.equal(p._colors.mpPocLine, CHROME_CURRENT.mpPocLine, '不正値で現行値が壊れた');
  // 部分指定は指定分だけ効く。
  p.setChromeColors({ mpPocLine: '#123456' });
  assert.equal(p._colors.mpPocLine, '#123456');
  assert.equal(p._colors.mpVaLine, CHROME_CURRENT.mpVaLine, '指定していない点まで動いた');
});

test('TC-MP-T07 恒等: 未注入の primitive は 16 点すべて現行リテラルを持つ', () => {
  // Arrange / Act
  const p = new MarketProfileHistogramPrimitive();
  // Assert
  for (const [id, current] of MP_SLOTS) {
    assert.equal(p._colors[id], current, `${id}: 既定が現行リテラルでない`);
  }
});
