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

import { CHROME_SLOTS, CHROME_CURRENT, THEME_EXEMPT_LITERALS } from '../js/usecase/chrome_tokens.js';
import { COLOR_ROLES } from '../js/domain/color_roles.js';

const CSS = readFileSync(fileURLToPath(new URL('../css/app.css', import.meta.url)), 'utf8');
const REPLAY_CSS = readFileSync(fileURLToPath(new URL('../../../../simulator/replay_ui/web/css/replay_bar.css', import.meta.url)), 'utf8');

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
  // 対象は現在値表示の 3 点（A-10 の当初対象）。段階 5-D で足したアプリ UI クロムの配線点は
  //   セレクタが 1 対 1 でないため、別の検定（下の「全配線点が var(--ct-<id>) で読まれる」）が持つ。
  for (const slot of CHROME_SLOTS.filter((s) => s.mechanism === 'css' && !s.id.startsWith('ui'))) {
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

// 段階 5-D（旧 N-9 の後継）: 本段階が、旧テストが自ら「v2 で行う」と書いていた全体接続である。
//   旧テストは「部分接続を作らない」ために接続数を 3 に固定していた。全数接続した今、同じ意図
//   （＝地だけリテラル・文字だけテーマ色という判読不能な組み合わせを作らない）を守る条件は
//   「接続が 3 件であること」ではなく「**リテラルが 1 件も残っていないこと**」へ反転する。
test('段階 5-D: app.css に素の色リテラルが残っていない（台帳の対象外を除き 0 件）', () => {
  // var() の fallback は許す（JS 不動作時に現行と一致するための値）。fallback を潰してから探す。
  const stripped = CSS.replace(/var\([^)]*\)/g, 'VAR');
  const exempt = new Set(THEME_EXEMPT_LITERALS.map((e) => e.literal));
  const found = [...stripped.matchAll(/#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)/g)].map((m) => m[0]);
  const leaked = found.filter((v) => !exempt.has(v));
  assert.deepEqual(leaked, [], `リテラルが残っている: ${leaked.join(' / ')}`);
});

test('段階 5-D: 台帳の対象外リテラルは影 4 種のみ（例外が暗黙に増えていない）', () => {
  // 例外を増やすには台帳へ足すしかない構造であることの固定。ここが自動で追随してしまうと
  //   「見逃した色を例外にする」抜け道になるため、件数と理由を逐語で押さえる。
  assert.deepEqual(THEME_EXEMPT_LITERALS.map((e) => e.literal), [
    'rgba(0, 0, 0, .55)', 'rgba(0, 0, 0, .5)', 'rgba(0,0,0,0.5)', 'rgba(0, 0, 0, .45)',
  ]);
  for (const e of THEME_EXEMPT_LITERALS) {
    assert.equal(e.reason, 'shadow', '影以外の例外は認めない');
  }
});

test('段階 5-D: CSS 機構の全配線点が app.css / replay_bar.css から var(--ct-<id>, <現行値>) で読まれる', () => {
  // 通過条件 5（単一情報源）の一方向。台帳に在る配線点が CSS のどこからも読まれていなければ、
  //   それは「配ったが誰も受け取らない変数」＝死んだ配線点である。
  const all = `${CSS}\n${REPLAY_CSS}`;
  for (const slot of CHROME_SLOTS.filter((s) => s.mechanism === 'css' && s.id.startsWith('ui'))) {
    assert.ok(all.includes(`var(--ct-${slot.id}, ${slot.current})`),
      `${slot.id}: var(--ct-${slot.id}, ${slot.current}) が CSS に無い`);
  }
});

test('段階 5-D: CSS が読む --ct-* はすべて台帳に実在する（手書きの対応表を作らない）', () => {
  // 通過条件 5 のもう一方向。CSS 側だけで変数名を増やせると、applier が書かない変数を CSS が
  //   読む状態（＝常に fallback で、テーマが効かない）が黙って生まれる。
  const known = new Set([
    ...CHROME_SLOTS.filter((s) => s.mechanism === 'css').map((s) => `--ct-${s.id}`),
    ...COLOR_ROLES.map((t) => `--ct-${t}`),
  ]);
  const used = new Set([...`${CSS}\n${REPLAY_CSS}`.matchAll(/--ct-[A-Za-z]+/g)].map((m) => m[0]));
  for (const name of used) {
    assert.ok(known.has(name), `${name} は台帳に無い変数名`);
  }
});

test('段階 5-D: 接続後も現在値表示の 3 宣言はトークン変数を読み続ける（既存経路の不変）', () => {
  for (const slot of CHROME_SLOTS.filter((s) => s.mechanism === 'css' && !s.id.startsWith('ui'))) {
    assert.ok(CSS.includes(`var(--ct-${slot.token}, ${slot.current})`), slot.id);
  }
});

test('§1.3: CSS カスタムプロパティの接頭辞は --ct- で、既存の --live-follow-* と分離している', () => {
  // 既存の名前空間（live-follow ボタン専用 5 件）が本機能の接頭辞と衝突しない。
  assert.ok(CSS.includes('--live-follow-'), '既存の --live-follow-* が失われている');
  for (const slot of CHROME_SLOTS.filter((s) => s.mechanism === 'css')) {
    assert.equal(`--ct-${slot.token}`.startsWith('--live-follow-'), false);
  }
});
