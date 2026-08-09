// chrome_tokens.test.js — チャートクロム配線点の台帳テスト
//   （基本設計_指標カラーテーマ.md §4.2 配線点 20 点・§4.6 CHROME_DEFAULT・§7.4 段階 1 通過条件 6）。
//
// 本表は「クロムの現行リテラルの単一情報源」である。これが 3 ファイル（chart_bootstrap /
//   chart_renderer / replay_boundary_dim）と CSS 3 宣言に散在したままだと、「テーマなし」へ
//   戻すときの復元値が二重定義になる（§7.2 S1 が棄却された理由そのもの）。
//   よって件数（20）・トークン束ね・現行値の逐語一致を全数で固定する。

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  CHROME_SLOTS, CHROME_CURRENT, CHROME_DEFAULT, CHROME_TOKENS, chromeSlot,
} from '../js/usecase/chrome_tokens.js';
import { isColorRole } from '../js/domain/color_roles.js';

// --- §4.2 の表（逐語）----------------------------------------------------
// [# , slot id, 現行値, 機構, トークン]
const LEDGER = [
  [1, 'layoutBackground', '#131722', 'js', 'surface'],
  [2, 'backgroundFallback', '#131722', 'js', 'surface'],
  [3, 'gridVertLines', '#1f2530', 'js', 'grid'],
  [4, 'gridHorzLines', '#1f2530', 'js', 'grid'],
  [5, 'layoutTextColor', '#d1d4dc', 'js', 'text'],
  [6, 'paneSeparator', '#2a2e39', 'js', 'border'],
  [7, 'paneSeparatorHover', 'rgba(178,181,189,0.2)', 'js', 'border'],
  [8, 'rightPriceScaleBorder', '#2a2e39', 'js', 'border'],
  [9, 'timeScaleBorder', '#2a2e39', 'js', 'border'],
  [10, 'candleUp', '#26a69a', 'js', 'bullish'],
  [11, 'candleDown', '#ef5350', 'js', 'bearish'],
  [12, 'candleUpRestore', '#26a69a', 'js', 'bullish'],
  [13, 'candleDownRestore', '#ef5350', 'js', 'bearish'],
  [14, 'priceLine', '#ff9800', 'js', 'highlight'],
  [15, 'currentPriceUp', '#26a69a', 'css', 'bullish'],
  [16, 'currentPriceDown', '#ef5350', 'css', 'bearish'],
  [17, 'currentPriceNeutral', '#d1d4dc', 'css', 'text'],
  [18, 'dimCandle', '#16191f', 'js', 'surface'],
  [19, 'analysisTint', '#1b1a24', 'js', 'surface'],
  [20, 'replayBoundaryDim', '#090d18', 'js', 'surface'],
];

// §4.2 / §4.6 の派生差分（E-29 の実測値。設計で決めた値ではない）。
const DERIVED = {
  dimCandle: [3, 2, -3],
  analysisTint: [8, 3, 2],
  replayBoundaryDim: [-10, -10, -10],
};

test('§4.2: 配線点は 20 点（id・現行値・機構・トークンが表と全数一致）', () => {
  assert.equal(CHROME_SLOTS.length, 20);
  assert.deepEqual(
    CHROME_SLOTS.map((s) => [s.id, s.current, s.mechanism, s.token]),
    LEDGER.map(([, id, current, mechanism, token]) => [id, current, mechanism, token]),
  );
});

test('§4.2: 束ねるトークンは 7 種（surface/grid/border/text/bullish/bearish/highlight）', () => {
  const used = [...new Set(CHROME_SLOTS.map((s) => s.token))];
  assert.deepEqual(used.sort(),
    ['bearish', 'border', 'bullish', 'grid', 'highlight', 'surface', 'text']);
  assert.deepEqual([...CHROME_TOKENS].sort(), used.sort());
  // すべて語彙内であること（クロム専用の別語彙を作らない＝FR-C11）。
  for (const t of CHROME_TOKENS) {
    assert.ok(isColorRole(t), t);
  }
});

test('CHROME_CURRENT は slot id → 現行リテラルの逐語写像（単一情報源）', () => {
  assert.deepEqual(CHROME_CURRENT, Object.fromEntries(LEDGER.map(([, id, cur]) => [id, cur])));
  assert.ok(Object.isFrozen(CHROME_CURRENT));
  assert.ok(Object.isFrozen(CHROME_SLOTS));
});

test('chromeSlot(id) は表の行を返し、未知 id は null（全域的）', () => {
  for (const [, id, current, mechanism, token] of LEDGER) {
    const s = chromeSlot(id);
    assert.ok(s, id);
    assert.equal(s.current, current);
    assert.equal(s.mechanism, mechanism);
    assert.equal(s.token, token);
  }
  assert.equal(chromeSlot('nonexistent'), null);
  assert.equal(chromeSlot(null), null);
  assert.equal(chromeSlot(undefined), null);
});

// --- §4.6 CHROME_DEFAULT（トークン単位の現行既定）------------------------
test('§4.6: CHROME_DEFAULT は 7 トークン全てに定義を持つ（未定義返却が起きない＝LSP）', () => {
  assert.deepEqual(CHROME_DEFAULT, Object.freeze({
    surface: '#131722',
    grid: '#1f2530',
    border: '#2a2e39',
    text: '#d1d4dc',
    bullish: '#26a69a',
    bearish: '#ef5350',
    highlight: '#ff9800',
  }));
  for (const t of CHROME_TOKENS) {
    assert.equal(typeof CHROME_DEFAULT[t], 'string', t);
    assert.match(CHROME_DEFAULT[t], /^#[0-9a-f]{6}$/, t);
  }
});

// 恒等性の要（§7.4 段階 1 通過条件 6・D-11）。
//   トークン単位の既定（CHROME_DEFAULT）と配線点単位の現行値（slot.current）が食い違う配線点は
//   #7 だけであり、それは「不透明度を持つ」ことに由来する（現行 rgba(178,181,189,0.2) は border
//   の #2a2e39 とは別の色）。よって既定値の単一情報源は**配線点単位**でなければならず、
//   トークン単位の既定で置き換えるとテーマ未設定時の見た目が変わる。
test('恒等性: 単色かつ非派生の配線点は CHROME_DEFAULT[token] と一致する', () => {
  for (const s of CHROME_SLOTS) {
    if (s.alpha != null || s.derivedFrom != null) continue;
    assert.equal(s.current, CHROME_DEFAULT[s.token], `${s.id}`);
  }
});

test('恒等性: 不透明度を持つ配線点は #7 のみで、トークン既定とは別の色である', () => {
  const withAlpha = CHROME_SLOTS.filter((s) => s.alpha != null);
  assert.equal(withAlpha.length, 1);
  assert.equal(withAlpha[0].id, 'paneSeparatorHover');
  assert.equal(withAlpha[0].alpha, 0.2);
  assert.notEqual(withAlpha[0].current, CHROME_DEFAULT[withAlpha[0].token]);
});

// --- §4.2 派生（E-29 の実測差分）----------------------------------------
test('§4.2: 派生配線点は 3 点で、いずれも surface に従属し実測差分を持つ', () => {
  const derived = CHROME_SLOTS.filter((s) => s.derivedFrom != null);
  assert.equal(derived.length, 3);
  for (const s of derived) {
    assert.equal(s.derivedFrom, 'surface', s.id);
    assert.deepEqual(s.delta, DERIVED[s.id], s.id);
    assert.equal(s.token, 'surface', s.id);
  }
});

test('E-29: 派生差分は背景 #131722 から現行値を厳密に再現する（設計値ではなく実測差分）', () => {
  const base = CHROME_DEFAULT.surface;
  const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const [br, bg, bb] = rgb(base);
  for (const s of CHROME_SLOTS.filter((x) => x.derivedFrom != null)) {
    const [r, g, b] = rgb(s.current);
    assert.deepEqual([r - br, g - bg, b - bb], s.delta, s.id);
  }
});

// --- 機構の分割（§4.3・FR-C12）-------------------------------------------
test('§4.3: CSS 機構の配線点は現在値表示の 3 点のみ（v1 の置換対象）', () => {
  const css = CHROME_SLOTS.filter((s) => s.mechanism === 'css');
  assert.deepEqual(css.map((s) => s.id),
    ['currentPriceUp', 'currentPriceDown', 'currentPriceNeutral']);
  assert.deepEqual(css.map((s) => s.token), ['bullish', 'bearish', 'text']);
});

test('§4.2: 「現在値」は 2 機構にまたがり、ライン(JS)と表示(CSS)で別トークンを持つ', () => {
  // 現在値ライン＝値の上下と無関係に常時同色（highlight）。
  assert.equal(chromeSlot('priceLine').token, 'highlight');
  assert.equal(chromeSlot('priceLine').mechanism, 'js');
  // 現在値表示＝前回表示値との比較で上下を色で示す（bullish/bearish、未確定は text）。
  assert.equal(chromeSlot('currentPriceUp').token, 'bullish');
  assert.equal(chromeSlot('currentPriceDown').token, 'bearish');
  assert.equal(chromeSlot('currentPriceNeutral').token, 'text');
});

test('slot id は一意（表の行が重複しない）', () => {
  const ids = CHROME_SLOTS.map((s) => s.id);
  assert.equal(new Set(ids).size, ids.length);
});
