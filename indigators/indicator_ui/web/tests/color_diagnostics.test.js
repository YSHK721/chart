// color_diagnostics.test.js — テーマの間違いを検出する（段階 5-B・診断）。
//
// 目的（依頼者指示 2026-08-09）: 全色を変更可能にするとトークンが増えて認知負荷が上がる。それを
//   機能で相殺する 2 本のうちの 1 本＝「間違いを検出する」。
//
// 最重要の規律: **警告は保存を妨げない**。診断は助言であって縮退ではないため、`ok` / `code` の
//   ような合否を返さない。参照実装は F-C3（`res.ignoredTokens` が `res.ok` の判定に一切関与
//   しない）で、同じ規律に従う。この分離が構造で保たれていることは
//   tests/color_theme_derivation_wiring.test.js の走査テストが固定する。
//
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  DIRECTION_CONTRAST_MIN, FIGURE_TOKENS, SURFACE_CONTRAST_MIN, diagnoseTheme,
} from '../js/usecase/color_diagnostics.js';

// 現行 14 色（PRESET_THEMES「基本」）。診断の基準になる参照実装。
const CURRENT = Object.freeze({
  bullish: '#00bfa5', bearish: '#ff5252', neutral: '#90a4ae', alert: '#ffa726',
  primary: '#42a5f5', secondary: '#7e57c2', range: '#26c6da', level: '#78909c',
  muted: '#546e7a', surface: '#131722', grid: '#1f2530', border: '#2a2e39',
  text: '#d1d4dc', highlight: '#f5f5f5',
});

const codesOf = (ds) => ds.map((d) => d.code);
const pick = (ds, code) => ds.filter((d) => d.code === code);

// =========================================================================
// W-C1 意味の衝突（2 つ以上のトークンが同じ色）
// =========================================================================

test('TC-DG01 W-C1: 同じ色を持つ 2 トークンを 1 件の診断として報告する', () => {
  // Arrange: 「主出力」と「副出力」が同色＝画面上で 2 つの意味が区別できない。
  const roleColors = { primary: '#42a5f5', secondary: '#42a5f5' };
  // Act
  const out = diagnoseTheme({ roleColors });
  // Assert
  assert.deepEqual(codesOf(out), ['W-C1']);
  assert.deepEqual([...out[0].tokens].sort(), ['primary', 'secondary']);
  assert.equal(out[0].measured, 2, 'measured は衝突しているトークン数');
});

test('TC-DG02 W-C1: 3 トークンの衝突は 1 件にまとめ、独立した衝突は別件として報告する', () => {
  // Arrange: 3 件の組が 1 つと、2 件の組が 1 つ。件数がトークン数ぶんに増えないことの固定。
  const roleColors = {
    primary: '#42a5f5', secondary: '#42a5f5', range: '#42a5f5',
    level: '#78909c', muted: '#78909c',
    alert: '#ffa726',
  };
  // Act
  const out = pick(diagnoseTheme({ roleColors }), 'W-C1');
  // Assert
  assert.equal(out.length, 2, '衝突の「組」の数だけ報告する');
  assert.deepEqual(out.map((d) => d.measured).sort(), [2, 3]);
  assert.deepEqual(out.find((d) => d.measured === 3).tokens, ['primary', 'secondary', 'range']);
});

test('TC-DG03 W-C1: 衝突が無ければ 0 件（境界: すべて相異なる）', () => {
  // Arrange
  const roleColors = { primary: '#42a5f5', secondary: '#7e57c2' };
  // Act / Assert
  assert.deepEqual(pick(diagnoseTheme({ roleColors }), 'W-C1'), []);
});

test('TC-DG04 W-C1: 参照実装（現行 14 色）は 1 件も報告しない', () => {
  // Arrange: 現行 14 色はすべて相異なる（設計の前提）。ここが落ちるなら参照実装側の欠陥。
  // Act / Assert
  assert.deepEqual(pick(diagnoseTheme({ roleColors: CURRENT }), 'W-C1'), []);
});

// =========================================================================
// W-C2 地とのコントラスト不足
//
// 閾値 3.0 の根拠: WCAG 2.1 SC 1.4.11（非テキストコントラスト）。チャート上のトークンが着色する
//   のは線・帯・ローソクという**グラフィカルオブジェクト**であり、本文テキストの 4.5 ではなく
//   3.0 が適用条件に合致する。参照実装（現行 14 色）の図側の下限は muted 3.314 / secondary 3.434
//   で、閾値 3.0 を上回る（実測）＝参照実装は 1 件も報告されない。
//
// 対象外の 3 語（surface / grid / border）: surface は地そのもの（自己比較で常に 1.0）。
//   grid・border は「面の上に引く最も控えめな構造線」（color_roles.js §4.1.1）であり、地との
//   低コントラストが**意図**である。実測でも現行 grid 1.164 / border 1.320 と 3.0 を大きく
//   下回るため、対象に含めると参照実装そのものが不合格になる。
// =========================================================================

// 黒地に対する灰の CR は (Y+0.05)/0.05 で単調。閾値 3.0 をまたぐ整数階調は実測で n=89/90。
const GRAY_JUST_BELOW_3 = '#595959'; // CR = 2.997975
const GRAY_JUST_ABOVE_3 = '#5a5a5a'; // CR = 3.044835

test('TC-DG05 W-C2: 地とのコントラストが閾値未満のトークンを報告する（measured は実測 CR）', () => {
  // Arrange
  const roleColors = { surface: '#000000', primary: GRAY_JUST_BELOW_3 };
  // Act
  const out = pick(diagnoseTheme({ roleColors }), 'W-C2');
  // Assert
  assert.equal(out.length, 1);
  assert.deepEqual(out[0].tokens, ['primary', 'surface'], '比較した 2 語を挙げる');
  assert.ok(Math.abs(out[0].measured - 2.997975) < 1e-5, `measured=${out[0].measured}`);
});

test('TC-DG06 W-C2 境界: 閾値ちょうど以上は報告しない（判定は厳密な <）', () => {
  // Arrange: 閾値 3.0 をまたぐ隣接階調（実測 2.997975 / 3.044835）。
  // Act
  const below = pick(diagnoseTheme({ roleColors: { surface: '#000000', primary: GRAY_JUST_BELOW_3 } }), 'W-C2');
  const above = pick(diagnoseTheme({ roleColors: { surface: '#000000', primary: GRAY_JUST_ABOVE_3 } }), 'W-C2');
  // Assert
  assert.equal(below.length, 1, `CR ${below[0] && below[0].measured} は閾値未満`);
  assert.deepEqual(above, [], '閾値以上は助言しない');
  assert.equal(SURFACE_CONTRAST_MIN, 3.0, '閾値は定数で公開する（WCAG 2.1 SC 1.4.11）');
});

test('TC-DG07 W-C2: 地そのもの（surface）と構造線（grid / border）は対象外', () => {
  // Arrange: 3 語とも地と同色＝コントラスト 1.0。それでも W-C2 は 1 件も出ない。
  const roleColors = {
    surface: '#000000', grid: '#000000', border: '#000000',
  };
  // Act
  const out = diagnoseTheme({ roleColors });
  // Assert
  assert.deepEqual(pick(out, 'W-C2'), [], '構造線の低コントラストは意図であり欠陥ではない');
  assert.equal(FIGURE_TOKENS.includes('surface'), false);
  assert.equal(FIGURE_TOKENS.includes('grid'), false);
  assert.equal(FIGURE_TOKENS.includes('border'), false);
  assert.equal(FIGURE_TOKENS.length, 15, '語彙 18 語 − 対象外 3 語');
});

test('TC-DG08 W-C2: surface が宣言されていなければ判定しない（比較の相手が無い）', () => {
  // Arrange: 既定の地との比較へ勝手に落とさない。既定を持ち込むと、テーマが地を宣言して
  //   いないだけの状態に対して「地に対して」の助言を出すことになる（前提の捏造）。
  const roleColors = { primary: GRAY_JUST_BELOW_3 };
  // Act / Assert
  assert.deepEqual(pick(diagnoseTheme({ roleColors }), 'W-C2'), []);
});

test('TC-DG09 W-C2: 参照実装（現行 14 色）は 1 件も報告しない（実測下限 muted 3.314）', () => {
  // Act / Assert
  assert.deepEqual(pick(diagnoseTheme({ roleColors: CURRENT }), 'W-C2'), []);
});

// =========================================================================
// W-C3 方向の対比不足（bullish と bearish）
//
// 閾値 1.15 の根拠（**参照実装の実測**であって WCAG ではない）: 上下は色相で分けられており、
//   輝度比では分かれていない。実測は同梱プリセット「基本」1.366962・クロム既定
//   （#26a69a / #ef5350）1.162741 で、WCAG の 3.0 や 4.5 を閾値に採ると**参照実装そのものが
//   不合格**になる。よって閾値は「現行の見た目より方向の輝度差が悪化している」を検出する線＝
//   参照実装の下限 1.162741 を下回らない最大の切りの良い値 1.15 に置く。
//   本診断が捉えられるのは輝度側だけであり、色相が同一で輝度だけ違う組（例: 明るい赤と暗い赤）は
//   捉えられない。色相距離の診断は語彙・domain 関数の追加を要するため本段階の範囲外。
// =========================================================================

const GRAY_JUST_BELOW_115 = '#151515'; // CR(黒) = 1.149981
const GRAY_JUST_ABOVE_115 = '#161616'; // CR(黒) = 1.160464

test('TC-DG10 W-C3: 上下の輝度比が閾値未満なら報告する（measured は実測 CR）', () => {
  // Arrange
  const roleColors = { bullish: '#000000', bearish: GRAY_JUST_BELOW_115 };
  // Act
  const out = pick(diagnoseTheme({ roleColors }), 'W-C3');
  // Assert
  assert.equal(out.length, 1);
  assert.deepEqual(out[0].tokens, ['bullish', 'bearish']);
  assert.ok(Math.abs(out[0].measured - 1.149981) < 1e-5, `measured=${out[0].measured}`);
});

test('TC-DG11 W-C3 境界: 閾値ちょうど以上は報告しない（判定は厳密な <）', () => {
  // Arrange / Act
  const below = pick(diagnoseTheme({ roleColors: { bullish: '#000000', bearish: GRAY_JUST_BELOW_115 } }), 'W-C3');
  const above = pick(diagnoseTheme({ roleColors: { bullish: '#000000', bearish: GRAY_JUST_ABOVE_115 } }), 'W-C3');
  // Assert
  assert.equal(below.length, 1);
  assert.deepEqual(above, []);
  assert.equal(DIRECTION_CONTRAST_MIN, 1.15, '閾値は定数で公開する（参照実装の実測が根拠）');
});

test('TC-DG12 W-C3: 片方しか宣言されていなければ判定しない', () => {
  // Arrange / Act / Assert
  assert.deepEqual(pick(diagnoseTheme({ roleColors: { bullish: '#00bfa5' } }), 'W-C3'), []);
  assert.deepEqual(pick(diagnoseTheme({ roleColors: { bearish: '#ff5252' } }), 'W-C3'), []);
});

test('TC-DG13 W-C3: 参照実装は 1 件も報告しない（プリセット 1.366962 / クロム既定 1.162741）', () => {
  // Act / Assert
  assert.deepEqual(pick(diagnoseTheme({ roleColors: CURRENT }), 'W-C3'), []);
  assert.deepEqual(
    pick(diagnoseTheme({ roleColors: { bullish: '#26a69a', bearish: '#ef5350' } }), 'W-C3'),
    [],
    'クロム既定（テーマ未設定時の現行の見た目）が不合格にならない',
  );
});

test('TC-DG14 W-C3: 上下が同色なら W-C1 と W-C3 の両方が出る（診断は互いに独立）', () => {
  // Arrange: 同色は「意味の衝突」であり同時に「方向が分からない」。片方が他方を抑止しない。
  const roleColors = { bullish: '#26a69a', bearish: '#26a69a' };
  // Act
  const out = diagnoseTheme({ roleColors });
  // Assert
  assert.deepEqual(codesOf(out).sort(), ['W-C1', 'W-C3']);
  assert.equal(pick(out, 'W-C3')[0].measured, 1, '同色の CR は 1');
});

// =========================================================================
// 全域性（LSP）と「助言であって縮退ではない」規律
// =========================================================================

test('TC-DG15 diagnoseTheme: 不正入力でも例外を投げず空配列（全域関数）', () => {
  // Arrange / Act / Assert
  assert.deepEqual(diagnoseTheme(), []);
  assert.deepEqual(diagnoseTheme(null), []);
  assert.deepEqual(diagnoseTheme({}), []);
  assert.deepEqual(diagnoseTheme({ roleColors: null }), []);
  assert.deepEqual(diagnoseTheme({ roleColors: 'x' }), []);
  assert.deepEqual(diagnoseTheme({ roleColors: ['#000000'] }), []);
  assert.deepEqual(diagnoseTheme({ roleColors: 3 }), []);
});

test('TC-DG16 diagnoseTheme: 解釈できない値・語彙外キーは判定に使わない（例外にもしない）', () => {
  // Arrange: 値の正規化と語彙の検査は上流（normalizeRoleColors）の責務。診断は「読める値」だけを
  //   見て、読めない値を欠陥として報告しない（F-C9 は既に未宣言へ落ちている）。
  const roleColors = {
    surface: '#000000', primary: 'red', __unknown__: '#000000', bullish: null, bearish: undefined,
  };
  // Act / Assert
  assert.deepEqual(diagnoseTheme({ roleColors }), []);
});

test('TC-DG17 diagnoseTheme: 合否（ok / code）を返さない — 助言であって縮退ではない', () => {
  // Arrange: 欠陥だらけのテーマでも戻り値は配列であり、保存を止める材料を 1 つも含まない。
  const roleColors = { surface: '#000000', primary: '#000000', bullish: '#000000', bearish: '#000000' };
  // Act
  const out = diagnoseTheme({ roleColors });
  // Assert
  assert.ok(Array.isArray(out), '戻り値は配列のみ');
  assert.ok(out.length > 0, '欠陥は検出されている（＝空だから合否が無いのではない）');
  for (const d of out) {
    assert.deepEqual(Object.keys(d).sort(), ['code', 'measured', 'tokens'],
      `Diagnostic の形は { code, tokens, measured } だけ: ${JSON.stringify(d)}`);
    assert.ok(Array.isArray(d.tokens));
    assert.equal(typeof d.measured, 'number');
    assert.ok(Number.isFinite(d.measured));
  }
});

test('TC-DG18 diagnoseTheme: 入力を破壊しない（純関数）', () => {
  // Arrange
  const roleColors = { surface: '#000000', primary: '#000000' };
  const before = JSON.stringify(roleColors);
  // Act
  diagnoseTheme({ roleColors });
  // Assert
  assert.equal(JSON.stringify(roleColors), before);
});
