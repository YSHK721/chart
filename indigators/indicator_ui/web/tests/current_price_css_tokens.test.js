// current_price_css_tokens.test.js — 現在値表示の CSS 3 宣言のトークン化（A-10）
//   （基本設計_指標カラーテーマ.md §4.3・§7.4 段階 1 通過条件 7）。
//
// 現在値は 2 つの配信機構にまたがる（E-28）。価格線は JS（lwc オプション）、左上の大型数値は
//   CSS。CSS 側に色リテラルを残すと、同一の意味（bullish / bearish / text）が 2 箇所で別々に
//   定義され、テーマを適用しても数値表示だけ旧色に残る。よって CSS は `var(--ct-*)` を読むだけの
//   側にし、依存の向きを CSS → トークンの一方向にする（§7.3 DIP）。
//
// fallback には現行値を書く。JS が `:root` へ書き込めない状況（F-C11・SSR・スクリプト無効）でも
//   解決結果が現行と一致する＝通過条件 7 そのもの。

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { CHROME_SLOTS, CHROME_CURRENT } from '../js/usecase/chrome_tokens.js';

const CSS = readFileSync(fileURLToPath(new URL('../css/app.css', import.meta.url)), 'utf8');

// CSS 機構の配線点（chrome_tokens.js が単一情報源）。セレクタは本テストが持つ。
const SELECTORS = {
  currentPriceNeutral: '#current-price',
  currentPriceUp: '#current-price.is-up',
  currentPriceDown: '#current-price.is-down',
};

function declarationFor(selector) {
  // `<selector> { ... }` のブロックを取り出す（宣言の並びは問わない）。
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const m = CSS.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  return m ? m[1] : null;
}

test('§4.3: CSS 機構の配線点 3 点がすべて var(--ct-<token>, <現行値>) を読む', () => {
  for (const slot of CHROME_SLOTS.filter((s) => s.mechanism === 'css')) {
    const selector = SELECTORS[slot.id];
    assert.ok(selector, `セレクタ未定義: ${slot.id}`);
    const block = declarationFor(selector);
    assert.ok(block, `${selector} のブロックが見つからない`);
    const expected = `var(--ct-${slot.token}, ${slot.current})`;
    assert.ok(block.includes(`color: ${expected}`),
      `${selector}: 期待 "color: ${expected}" / 実際 "${block.trim()}"`);
  }
});

test('通過条件 7: --ct-* 未設定時に解決される色は現行値と一致する（fallback）', () => {
  // fallback に書かれた値が現行リテラルであること＝JS 不動作時も現行と一致する。
  const expectedFallback = {
    currentPriceNeutral: CHROME_CURRENT.currentPriceNeutral, // #d1d4dc
    currentPriceUp: CHROME_CURRENT.currentPriceUp, // #26a69a
    currentPriceDown: CHROME_CURRENT.currentPriceDown, // #ef5350
  };
  for (const [id, selector] of Object.entries(SELECTORS)) {
    const block = declarationFor(selector);
    const m = block.match(/color:\s*var\(--ct-[a-z]+,\s*([^)]+)\)/);
    assert.ok(m, `${selector}: var() の fallback を取り出せない`);
    assert.equal(m[1].trim(), expectedFallback[id], selector);
  }
});

test('A-10: 現在値表示のブロックに素の色リテラルが残っていない（二重定義の除去）', () => {
  for (const selector of Object.values(SELECTORS)) {
    const block = declarationFor(selector);
    // var() の fallback 以外の位置に hex が無いこと。fallback を除去してから探す。
    const withoutFallbacks = block.replace(/var\([^)]*\)/g, 'var()');
    assert.equal(/#[0-9a-fA-F]{3,6}\b/.test(withoutFallbacks), false,
      `${selector}: 素の色リテラルが残っている → "${withoutFallbacks.trim()}"`);
  }
});

test('N-9: v1 で var(--ct-*) を読む CSS 宣言は現在値表示の 3 つだけ（部分接続を作らない）', () => {
  // §3.2 N-9 はアプリ UI クロム（ツールバー・ダイアログ・メニュー等）を v1 対象外と定め、
  //   §4.3 は「v1 で var(--ct-*) へ置換する CSS は現在値表示の 3 宣言のみ」と定める。
  //   一部だけ接続すると、例えば surface:#ffffff / text:#111111 のテーマでパネルの地はリテラルの
  //   ままで文字色だけが変わり、判読不能な状態が作れてしまう（同一概念に 2 通りの効き方）。
  //   接続は v2 で app.css 全体を一括置換して行う（N-9 の手順）。
  const uses = [...CSS.matchAll(/var\(--ct-[a-z]+[^)]*\)/g)].map((m) => m[0]);
  assert.equal(uses.length, 3, `v1 の var(--ct-*) は 3 宣言のみ（実際: ${uses.join(' / ')}）`);
  const expected = CHROME_SLOTS.filter((s) => s.mechanism === 'css')
    .map((s) => `var(--ct-${s.token}, ${s.current})`);
  assert.deepEqual([...uses].sort(), [...expected].sort());
});

test('§1.3: CSS カスタムプロパティの接頭辞は --ct- で、既存の --live-follow-* と分離している', () => {
  // 既存の名前空間（live-follow ボタン専用 5 件）が本機能の接頭辞と衝突しない。
  assert.ok(CSS.includes('--live-follow-'), '既存の --live-follow-* が失われている');
  for (const slot of CHROME_SLOTS.filter((s) => s.mechanism === 'css')) {
    assert.equal(`--ct-${slot.token}`.startsWith('--live-follow-'), false);
  }
});
