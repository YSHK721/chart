// chrome_css_slot_vars.test.js — CSS 機構へ「配線点単位」の値を配る経路（段階 5-D）。
//
// 存在理由: v1 の CSS 機構はトークン単位の `--ct-<token>` しか配れなかった。app.css の全リテラルを
//   トークン経由へ移すと、トークンからの**派生**（surface + delta のパネル面など）と**不透明度**
//   （rgba の地）が CSS 側に現れる。CSS には delta を計算する手段が無いため、トークン変数だけでは
//   これらへ到達できない。よって配線点単位の変数 `--ct-<slotId>` を JS 側で解決して配る。
//
// 規約は 1 本だけにする（分岐を作らない）: CSS 機構の配線点は**例外なく** `--ct-<slotId>` を読む。
//   「単色なら token 変数・派生なら slot 変数」と使い分けると、CSS の書き手が slot の属性を知らな
//   ければ変数名を決められない＝台帳と CSS の間に手書きの対応表が生まれる（通過条件 5 違反）。

import test from 'node:test';
import assert from 'node:assert/strict';

import { resolveAllChrome, resolveChromeSlotColor } from '../js/usecase/color_resolver.js';
import { CHROME_SLOTS, CHROME_CURRENT } from '../js/usecase/chrome_tokens.js';
import { ChromeThemeApplier } from '../js/adapter/front/chrome_theme_applier.js';

const CSS_SLOTS = CHROME_SLOTS.filter((s) => s.mechanism === 'css');

// --- resolveAllChrome が CSS 機構ぶんを別の袋で返す -----------------------

test('TC-CV01 resolveAllChrome は CSS 機構の配線点を cssSlots として返す（全数・過不足なし）', () => {
  // Arrange / Act
  const { cssSlots } = resolveAllChrome(null);
  // Assert
  assert.deepEqual(Object.keys(cssSlots).sort(), CSS_SLOTS.map((s) => s.id).sort());
});

test('TC-CV02 恒等: テーマ未設定なら cssSlots の値は現行リテラルと文字列一致する', () => {
  // Arrange / Act
  const { cssSlots } = resolveAllChrome(null);
  // Assert
  for (const slot of CSS_SLOTS) {
    assert.equal(cssSlots[slot.id], CHROME_CURRENT[slot.id], `${slot.id}: 現行リテラルから動いた`);
  }
});

test('TC-CV03 cssSlots は slot 単位の解決結果と一致する（解決規則を二重に持たない）', () => {
  // Arrange
  const theme = { roleColors: { accent: '#112233', danger: '#445566', surface: '#000000' } };
  // Act
  const { cssSlots } = resolveAllChrome(theme);
  // Assert
  for (const slot of CSS_SLOTS) {
    assert.equal(cssSlots[slot.id], resolveChromeSlotColor({ slotId: slot.id, theme }), slot.id);
  }
});

// --- 派生 × 不透明度の合成（新しい振る舞い）------------------------------

test('TC-CV04 delta と alpha を併せ持つ配線点は、delta を当ててから alpha を巻く', () => {
  // Arrange: 実在の配線点ではなく規則そのものを固定する。読取欄の地 rgba(30,34,45,.82) は
  //   grid から delta[-1,-3,-3] であり、delta だけでは不透明度が落ちて地が透けなくなる。
  const theme = { roleColors: { grid: '#1f2530' } }; // (31,37,48)
  const slot = {
    id: 'probe', token: 'grid', current: 'rgba(30, 34, 45, 0.82)', mechanism: 'css',
    derivedFrom: 'grid', delta: [-1, -3, -3], alpha: 0.82,
  };
  // Act
  const out = resolveChromeSlotColor({ slotId: slot.id, theme, slot });
  // Assert
  assert.equal(out, 'rgba(30, 34, 45, 0.82)');
});

test('TC-CV05 delta のみの配線点は不透明色のまま（alpha を勝手に足さない）', () => {
  // Arrange: 加法 delta を保つ有彩色系で見る（減光系は対地 CR 目標へ移行したため対象外）。
  //   danger #ef5350 → uiDangerSolid は delta [-63,-25,-32]。
  const theme = { roleColors: { danger: '#ef5350' } };
  // Act
  const out = resolveChromeSlotColor({ slotId: 'uiDangerSolid', theme });
  // Assert
  assert.equal(out, '#b03a30');
});

// --- applier が :root へ配線点変数を書く ---------------------------------

function makeRootStyle() {
  const set = new Map();
  const removed = [];
  return {
    style: {
      setProperty(name, value) { set.set(name, value); },
      removeProperty(name) { removed.push(name); set.delete(name); },
    },
    set,
    removed,
  };
}

test('TC-CV06 applier は CSS 機構の配線点を --ct-<slotId> として :root へ書く', () => {
  // Arrange
  const { style, set } = makeRootStyle();
  const applier = new ChromeThemeApplier({ rootStyle: style });
  // Act
  applier.apply(resolveAllChrome(null));
  // Assert
  for (const slot of CSS_SLOTS) {
    assert.equal(set.get(`--ct-${slot.id}`), CHROME_CURRENT[slot.id], slot.id);
  }
});

test('TC-CV07 applier はトークン変数も引き続き書く（既存の 3 宣言を壊さない）', () => {
  // Arrange
  const { style, set } = makeRootStyle();
  const applier = new ChromeThemeApplier({ rootStyle: style });
  // Act
  applier.apply(resolveAllChrome(null));
  // Assert
  assert.equal(set.get('--ct-surface'), '#131722');
  assert.equal(set.get('--ct-accent'), '#2962ff');
});

test('TC-CV08 値が null の配線点変数は removeProperty で消す（適用履歴を残さない）', () => {
  // Arrange
  const { style, removed } = makeRootStyle();
  const applier = new ChromeThemeApplier({ rootStyle: style });
  // Act: 未知 slot を含む袋を直接渡す（resolver を経由しない縮退経路）。
  applier.apply({ slots: {}, tokens: {}, cssSlots: { uiAccent: null } });
  // Assert
  assert.ok(removed.includes('--ct-uiAccent'));
});
