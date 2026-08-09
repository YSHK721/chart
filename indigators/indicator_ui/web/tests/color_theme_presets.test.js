// color_theme_presets.test.js — 同梱プリセット「基本」（§9 T-1 の確定）。
//
// 目的（§1.0）に対するプリセットの役割: 目的 2「カラーに意味をもたせる」は、E-26・E-27 が記録した
//   「緑／赤が方向・強度・勝敗の 3 意味を担い、#d2433a が抵抗帯（弱気）と外れ値の 2 意味を持つ」状態を
//   解いて初めて達成される。同梱プリセットはその解き方を 1 つ具体化したもので、**警戒（alert）を赤から
//   外す**ことが眼目である。ユーザーが 14 行を手入力しなくても目的の状態に到達できるようにする。
//
// 設計上の要（本テストが固定する不変条件）:
//   プリセットは「テーマ集合の初期値」ではなく「**読み出し時に合成する既定**」である。
//   集合へ書き込む方式にすると、ユーザーが削除しても次回起動で復活し、改名しても上書きされる
//   （ユーザーの操作が無効化される＝§5.3 の削除・改名が意味を失う）。合成方式なら、同じ themeId を
//   持つ永続値が在ればそちらが勝ち、削除の記録も尊重される。

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  PRESET_THEMES, PRESET_THEME_IDS, withPresets, isPresetThemeId,
} from '../js/usecase/color_themes.js';
import { COLOR_ROLES, isColorRole } from '../js/domain/color_roles.js';
import { isNormalizedHex } from '../js/domain/color_value.js';

const BASIC = () => PRESET_THEMES.find((t) => t.name === '基本');

// --- プリセットの形（§4.4 のエンティティを満たすこと）-----------------------

test('プリセットは 1 件（「基本」）で、§4.4 のエンティティを満たす', () => {
  assert.equal(PRESET_THEMES.length, 1);
  const t = BASIC();
  assert.ok(t, '「基本」が見つからない');
  assert.match(t.themeId, /^thm#/);
  assert.ok(t.name.length >= 1 && t.name.length <= 40);
  assert.equal(t.tfModifier, null, '既定は時間足で地が動かない（§4.7）');
  assert.equal(typeof t.createdAt, 'number');
  assert.equal(typeof t.updatedAt, 'number');
});

test('プリセットは 14 トークンすべてを宣言する（部分宣言で意味が虫食いにならない）', () => {
  const rc = BASIC().roleColors;
  assert.deepEqual(Object.keys(rc).sort(), [...COLOR_ROLES].sort());
  for (const [token, value] of Object.entries(rc)) {
    assert.ok(isColorRole(token), `語彙外トークン: ${token}`);
    assert.ok(isNormalizedHex(value), `${token}: 保存形（小文字 hex6）でない（${value}）`);
  }
});

// --- 目的（意味の分離）を色で固定する ---------------------------------------

test('目的: 警戒（alert）が弱気（bearish）と別の色である（E-26・E-27 の衝突を解く）', () => {
  const rc = BASIC().roleColors;
  assert.notEqual(rc.alert, rc.bearish, '警戒が弱気と同色では「赤＝方向かつ警戒」が残る');
  // 色相まで離れていること（明度違いの赤では衝突が残る）。R チャネル優位の赤系でないことを見る。
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(rc.alert.slice(i, i + 2), 16));
  assert.ok(g > b, `警戒が赤系のまま（${rc.alert}）: 緑成分が青成分を上回る暖色にする`);
});

test('目的: 方向の 2 色が互いに異なり、他のどの意味とも重ならない', () => {
  const rc = BASIC().roleColors;
  assert.notEqual(rc.bullish, rc.bearish);
  for (const token of COLOR_ROLES) {
    if (token === 'bullish' || token === 'bearish') continue;
    assert.notEqual(rc[token], rc.bullish, `${token} が強気と同色`);
    assert.notEqual(rc[token], rc.bearish, `${token} が弱気と同色`);
  }
});

test('依頼者指示: ローソクの方向色も現行値から変える（据え置きにしない）', () => {
  // 方向色は bullish / bearish トークンがローソクの上げ下げ 6 経路を駆動する（§4.2 #10/#11）。
  //   プリセットがここを現行値のままにすると、テーマを適用してもローソクだけ何も起きない。
  const rc = BASIC().roleColors;
  assert.notEqual(rc.bullish, '#26a69a', '強気が現行値のまま（ローソクが変わらない）');
  assert.notEqual(rc.bearish, '#ef5350', '弱気が現行値のまま（ローソクが変わらない）');
  // 色相は現行を保つ（teal 対 red）＝「方向」という意味そのものは変えない。
  const ch = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const [br, bg, bb] = ch(rc.bullish);
  assert.ok(bg > br && bb > br, `強気が teal 系でない（${rc.bullish}）`);
  const [rr, rg, rb] = ch(rc.bearish);
  assert.ok(rr > rg && rr > rb, `弱気が red 系でない（${rc.bearish}）`);
});

test('目的: 現在地（highlight）が警戒（alert）と紛れない', () => {
  const rc = BASIC().roleColors;
  assert.notEqual(rc.highlight, rc.alert);
});

test('目的: 読ませる線（level）と読ませない線（muted）が明度差で区別できる', () => {
  const rc = BASIC().roleColors;
  const lum = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)).reduce((a, b) => a + b, 0);
  assert.ok(lum(rc.level) > lum(rc.muted), 'level は muted より明るいこと（読ませる／読ませない）');
});

test('地（surface/grid/border/text）は現行のまま（見慣れた地の上で図だけが意味を持つ）', () => {
  const rc = BASIC().roleColors;
  assert.equal(rc.surface, '#131722');
  assert.equal(rc.grid, '#1f2530');
  assert.equal(rc.border, '#2a2e39');
  assert.equal(rc.text, '#d1d4dc');
});

// --- 合成の規律（ユーザーの操作を無効化しない）-------------------------------

test('withPresets: 永続値が空ならプリセットだけを返す（初回起動）', () => {
  const out = withPresets([]);
  assert.equal(out.length, 1);
  assert.equal(out[0].name, '基本');
});

test('withPresets: ユーザーのテーマはプリセットより後ろに並ぶ（既定が先頭）', () => {
  const mine = { themeId: 'thm#9', name: '自作', roleColors: {}, tfModifier: null, createdAt: 1, updatedAt: 1 };
  const out = withPresets([mine]);
  assert.deepEqual(out.map((t) => t.themeId), [BASIC().themeId, 'thm#9']);
});

test('withPresets: 同じ themeId の永続値があればユーザー側が勝つ（改名・色の変更を尊重）', () => {
  const edited = {
    themeId: BASIC().themeId, name: '基本（改）', roleColors: { bullish: '#000000' },
    tfModifier: null, createdAt: 1, updatedAt: 2,
  };
  const out = withPresets([edited]);
  assert.equal(out.length, 1, 'プリセットが二重に現れてはならない');
  assert.equal(out[0].name, '基本（改）');
  assert.equal(out[0].roleColors.bullish, '#000000');
});

test('withPresets: 削除の記録があればプリセットを復活させない（§5.3 の削除を尊重）', () => {
  const out = withPresets([], { removedPresetIds: [BASIC().themeId] });
  assert.deepEqual(out, [], '削除したプリセットが次回起動で復活してはならない');
});

test('withPresets: 不正な入力でも例外を投げない（全域的）', () => {
  for (const bad of [null, undefined, 'x', 42, {}]) {
    assert.doesNotThrow(() => withPresets(bad));
  }
  assert.ok(Array.isArray(withPresets(null)));
});

// --- 採番との整合（プリセット id をユーザーのテーマが再利用しない）------------

test('プリセットの themeId は採番規則と同形で、述語で判別できる', () => {
  for (const id of PRESET_THEME_IDS) {
    assert.match(id, /^thm#\d+$/);
    assert.equal(isPresetThemeId(id), true);
  }
  assert.equal(isPresetThemeId('thm#999'), false);
  assert.equal(isPresetThemeId(null), false);
});
