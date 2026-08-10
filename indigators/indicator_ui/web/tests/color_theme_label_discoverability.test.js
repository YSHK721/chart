// color_theme_label_discoverability.test.js — 「ローソク足の色が変更できない」への是正（段階 5-D）。
//
// 実使用フィードバック: 依頼者が「ローソク足の色が変更できない」と報告した。実測では変更**できる**
//   （bullish を変えるとローソクの色は変わる）。壊れていたのは機能ではなく**発見可能性**である。
//
// 病因（実測・app.css）: 行ラベルは `.theme-dialog-role-label { flex: 0 0 96px; color: #d1d4dc;
//   font-size: 12px }` で左端固定幅・本文色。ヒントは `.theme-dialog-role-hint { flex: 1 1 auto;
//   color: #787b86; font-size: 11px }` で右端可変・**最も弱い文字色**。16 行を上から目で走らせる
//   とき読まれるのは左端のラベル列だけで、「ローソク」の語はヒントにしか無かった。
//
// 是正の方針: 語彙もトークンも行数も増やさず、ラベルの**語順**だけを変える。ユーザーが名指しする
//   具体物（ローソク陽線／陰線）を先頭に置き、抽象的な意味（強気／弱気）を後置する。
//   却下した代替案: 検索欄の追加（UI 要素が増える）／`candle` トークンの新設（bullish と意味が
//   重複し、同一概念に 2 つの呼び名を作る）。いずれも「認知負荷を重くするな」に反する。

import test from 'node:test';
import assert from 'node:assert/strict';

import { labelForRole, hintForRole } from '../js/adapter/front/color_theme_dialogs.js';
import { COLOR_ROLES } from '../js/domain/color_roles.js';

test('TC-LD01 ローソク足を司るトークンは、ラベルだけを見て見つけられる', () => {
  // Arrange: ユーザーが探すときに使う語。
  const SEARCH_WORD = 'ローソク';
  // Act
  const labels = COLOR_ROLES.map((t) => labelForRole(t));
  // Assert: ヒントではなく**ラベル**に語が出ていること。
  const hits = COLOR_ROLES.filter((t) => labelForRole(t).includes(SEARCH_WORD));
  assert.deepEqual(hits, ['bullish', 'bearish'],
    `ラベル一覧に「${SEARCH_WORD}」が出ていない: ${labels.join(' / ')}`);
});

test('TC-LD02 ラベルは具体物を先頭に、意味を後置する（走査されるのは行頭）', () => {
  // Assert
  assert.ok(labelForRole('bullish').startsWith('ローソク'), labelForRole('bullish'));
  assert.ok(labelForRole('bearish').startsWith('ローソク'), labelForRole('bearish'));
});

test('TC-LD03 上下の区別はラベル内に保たれている（陽線・陰線が読み分けられる）', () => {
  // 具体物を足した結果「どちらが上げか」が読めなくなっては、直した意味がない。
  assert.notEqual(labelForRole('bullish'), labelForRole('bearish'));
  assert.ok(labelForRole('bullish').includes('強気'));
  assert.ok(labelForRole('bearish').includes('弱気'));
});

test('TC-LD04 語彙は 16 語のまま（発見可能性の是正でトークンを増やさない）', () => {
  assert.equal(COLOR_ROLES.length, 16);
  assert.equal(COLOR_ROLES.includes('candle'), false, 'candle トークンを作らない');
});

test('TC-LD05 全 16 トークンがラベルとヒントを持つ（トークン名の素出しが無い）', () => {
  for (const token of COLOR_ROLES) {
    assert.notEqual(labelForRole(token), token, `${token}: ラベル未定義でトークン名が出ている`);
    assert.ok(hintForRole(token).length > 0, `${token}: ヒントが空`);
  }
});

test('TC-LD06 ヒントは併記のまま残す（ラベルに移した語をヒントから消さない）', () => {
  // ラベルとヒントは競合しない。ヒントは「他にどこへ効くか」を示す席であり、ローソク以外の
  //   適用先（現在値・バンド）はラベルに入りきらない。
  assert.ok(hintForRole('bullish').includes('現在値'));
  assert.ok(hintForRole('bearish').includes('現在値'));
});
