// 計算機・ピッカーが**実際に見える**ことの構造ガード（ISSUE-368 スライス 7 追補）。
//
// なぜ要るか（Pre-mortem で成立した失敗原因・2026-08-20）: JS が生成する DOM は検定では
//   「要素が在る」まで確かめられるが、対応する CSS が無くても全検定が緑のまま通る。
//   実 UI では
//     - ゴースト線（`.price-pick-line`）は幅も色も無い div ＝**完全に見えない**（R-P1 が成立しない）
//     - モーダル（`.ps-dialog-backdrop`）は背景も配置も無く本文に平積みされる
//   という形で壊れる。要素の在席だけを見る検定では検出できない（ISSUE-277 と同型の穴）。
//
// 本ガードは「JS が生成するクラスに対応する CSS 規則が配信 CSS に在る」ことだけを固定する
//   （見た目の良し悪しは判定しない＝実 UI 検証の代替ではない）。
// 構造: Arrange-Act-Assert。配信される css/app.css を実ファイルとして読む。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const APP_CSS = readFileSync(fileURLToPath(new URL('../css/app.css', import.meta.url)), 'utf8');
const CSS = readFileSync(fileURLToPath(new URL('../css/position_sizing.css', import.meta.url)), 'utf8');

// JS が生成し、**見えなければ機能が成立しない**クラス。
const REQUIRED_RULES = [
  // ツールバー入口（position_sizing_menu.js）
  '.position-sizing-menu',
  '.position-sizing-menu-trigger',
  // モーダル（position_sizing_dialog.js）
  '.ps-dialog-backdrop',
  '.ps-dialog',
  '.ps-dialog-head',
  '.ps-dialog-body',
  '.ps-row',
  '.ps-out',
  '.ps-choice',
  '.ps-pick',
  '.ps-exit-group',
  // アーム式ピッカー（price_pick_controller.js）— ここが無いとゴースト線が見えない
  '.price-pick-ghost',
  '.price-pick-line',
  '.price-pick-label',
];

test('TC-CS01 計算機・ピッカーが生成するクラスに CSS 規則が在る（見えない UI を作らない）', () => {
  // Arrange / Act
  const missing = REQUIRED_RULES.filter((sel) => !CSS.includes(`${sel} `) && !CSS.includes(`${sel},`)
    && !CSS.includes(`${sel}{`) && !CSS.includes(`${sel}.`) && !CSS.includes(`${sel}:`));
  // Assert
  assert.deepEqual(missing, [], `CSS 規則が無いクラス（実 UI で見えない）: ${missing.join(', ')}`);
});

test('TC-CS03 新規スタイルは配信 CSS から読み込まれる（ファイルが在っても届かなければ意味がない）', () => {
  // Arrange / Act / Assert: index.html は無改変（ゼロ器規約）なので、配信されている app.css から
  //   @import で連結する。3 ページとも app.css を読む（replay は symlink・統合は /live/css/app.css）。
  assert.match(APP_CSS, /@import\s+url\(['"]\.\/position_sizing\.css['"]\)/, '配信 CSS から読み込まれていない');
  const importIndex = APP_CSS.indexOf('@import');
  const firstRule = APP_CSS.search(/^[.#:@a-zA-Z*][^\n]*\{/m);
  assert.ok(importIndex >= 0 && (firstRule === -1 || importIndex < firstRule),
    '@import は他の規則より前に置く（CSS 仕様。後ろだと無視される）');
});

test('TC-CS04 新規スタイルは生の色を持たない（テーマ恒等: 色は --ct-* トークン経由）', () => {
  // Arrange: var(--ct-X, fallback) の fallback を除いた色リテラルを数える。
  const withoutTokens = CSS.replace(/var\(--ct-[a-zA-Z]+,\s*[^)]*\)/g, '');
  // Act
  const bare = [...withoutTokens.matchAll(/#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)/g)].map((m) => m[0]);
  // Assert: 影（rgba の黒）は既存ダイアログと同値のため許す。それ以外の生色は禁止。
  const offenders = bare.filter((c) => !/^rgba\(0,\s*0,\s*0,/.test(c));
  assert.deepEqual(offenders, [], `トークンを通さない色があります: ${offenders.join(', ')}`);
});

test('TC-CS02 ゴースト線とアーム中の隠蔽が定義されている（ピッカーの可視状態が切り替わる）', () => {
  // Arrange / Act / Assert
  assert.match(CSS, /\.price-pick-ghost\.is-hidden\s*\{[^}]*display\s*:\s*none/, 'アーム解除でゴーストが消えない');
  assert.match(CSS, /\.price-pick-line\s*\{[^}]*(height|border-top)/, 'ゴースト線に太さが無い（見えない）');
});
