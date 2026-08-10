// tf_period_tooltip_theme.test.js — 時間足プロファイルのツールチップをテーマへ接続する（段階 5-E）。
//
// 機構は CSS（`var(--ct-<slotId>, <現行値>)`）。ツールチップは DOM 要素なので `:root` の
//   カスタムプロパティが継承され、5-D が既に配っている変数がそのまま効く（canvas と違い注入不要）。
//
// 割当（実測）:
//   rgba(19,23,34,0.92)   → (19,23,34) = #131722 = surface の 0.92（新語ゼロ）
//   rgba(154,164,178,0.35)→ text からの実測差分 [-55,-48,-42] の 0.35（枠線）
//   #d1d4dc               → text ＝ 既存配線点 uiText を再利用（専用 slot を作らない）

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { CHROME_CURRENT, chromeSlot } from '../js/usecase/chrome_tokens.js';
import { TfPeriodTooltip } from '../js/adapter/front/tf_period_tooltip.js';

const SRC = readFileSync(
  fileURLToPath(new URL('../js/adapter/front/tf_period_tooltip.js', import.meta.url)), 'utf8',
);

function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

const VAR_RE = /var\(--ct-([A-Za-z]+),\s*((?:[^()]|\([^()]*\))*)\)/g;

test('TC-TT-T01 恒等: ツールチップの配線点は接続前の実測リテラルを逐語で保つ', () => {
  // Arrange / Act / Assert
  assert.equal(CHROME_CURRENT.tfpTooltipSurface, 'rgba(19,23,34,0.92)');
  assert.equal(CHROME_CURRENT.tfpTooltipBorder, 'rgba(154,164,178,0.35)');
});

test('TC-TT-T02 通過条件 5: 新語ゼロ（地は surface・枠は text へ割れている）', () => {
  // Arrange / Act / Assert
  assert.equal(chromeSlot('tfpTooltipSurface').token, 'surface');
  assert.equal(chromeSlot('tfpTooltipBorder').token, 'text');
  assert.equal(chromeSlot('tfpTooltipSurface').mechanism, 'css');
  assert.equal(chromeSlot('tfpTooltipBorder').mechanism, 'css');
});

test('TC-TT-T03 通過条件 2: tf_period_tooltip.js に素の色リテラルが残っていない', () => {
  // Arrange
  const code = stripComments(SRC).replace(new RegExp(VAR_RE.source, 'g'), 'VAR');
  // Act
  const found = [...code.matchAll(/#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|hsla?\([^)]*\)/g)]
    .map((m) => m[0]).filter((v) => !v.includes('${'));
  // Assert
  assert.deepEqual(found, [], `リテラルが残っている: ${found.join(' / ')}`);
});

test('TC-TT-T04 恒等: 実際に書かれる var() の fallback は台帳の現行値と文字列一致する', () => {
  // ソースには `chromeVar('tfpTooltipSurface')` としか書かれておらず、`var(--ct-...)` の綴りは
  //   実行時に CHROME_CURRENT から組まれる。よってソース走査ではなく**生成結果**を観測する。
  // Arrange: 最小 DOM スタブ。
  const el = {
    className: '',
    style: { _css: '', get cssText() { return this._css; }, set cssText(v) { this._css = v; } },
  };
  const container = { children: [], appendChild(n) { this.children.push(n); return n; } };
  const doc = { createElement: () => el };
  const tooltip = new TfPeriodTooltip({ document: doc, container });
  // Act
  tooltip._ensureEl();
  // Assert
  const seen = [...el.style.cssText.matchAll(new RegExp(VAR_RE.source, 'g'))];
  assert.equal(seen.length, 3, `--ct-* を 3 つ読んでいない（${seen.length}）: ${el.style.cssText}`);
  for (const [, slotId, fallback] of seen) {
    assert.equal(fallback.trim(), CHROME_CURRENT[slotId],
      `--ct-${slotId}: fallback が現行リテラルと違う`);
  }
});

test('TC-TT-T05 文字色は既存配線点（uiText）を再利用する（同じ意味に席を増やさない）', () => {
  // 専用 slot を作ると「ツールチップの文字色」と「アプリ UI の文字色」が別々に動き、
  //   同じ意味に 2 つの席ができる（認知負荷が増える）。
  assert.ok(SRC.includes("chromeVar('uiText')"), 'uiText を再利用していない');
});
