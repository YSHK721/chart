// color_themes.js（usecase・純関数）のテスト。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_指標カラーテーマ.md v0.3.0
//   §4.4（COLOR_THEME エンティティ・name 1〜40 文字・roleColors は 14 トークンのみ・#rrggbb 小文字・
//        tfModifier は l ∈ [-1,1] 小数第 3 位・createdAt / updatedAt は UNIX 秒）、
//   §4.7（tfModifier 変調規則のクランプ・丸め）、§4.10（themeId 採番 `thm#{seq}`・破損時復旧）、
//   §4.11（テーマ 50 件・名前 40 文字・roleColors 14 キー）、§5.1（UC-C01 保存の処理順）、
//   §5.3（UC-C03 改名・削除）、§5.7（F-C1 / F-C3 / F-C6 / F-C9）。
// 参照実装（同型元）: js/usecase/chart_templates.js ／ tests/chart_templates_usecase.test.js。
// 構造: Arrange-Act-Assert（AAA）。DOM・Storage 非依存。
//
// ★ 対象モジュール js/usecase/color_themes.js の import は domain だけ（../domain/color_roles.js＝
//   色の語彙、../domain/tf_meta.js＝時間足台帳）。いずれも usecase → domain の内向き依存で、
//   同じ usecase の color_resolver.js も TF_CODES を同じ形で引いている（§7.8）。
//   tfModifier のキー検証は台帳を単一情報源とし、引数では無効化できない。
//   時刻は引数で受ける（決定論性）。
//
// ★ R-4（usecase から console を追い出す）: F-C3 の「未知トークンを無視した」ことは**戻り値**
//   （`ignoredTokens`）で報告し、警告そのものは adapter が出す。F-C6 の `changed` が
//   「usecase は事実を返し、adapter が warn して書き戻す」形になっているのと同じ規律で、
//   本モジュールは副作用（console）を 1 つも持たない。したがって normalizeRoleColors /
//   saveTheme を呼んでも console.warn は 1 回も起きない（下の captureWarn で固定する）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

// 未実装モジュールの読み込み失敗をテスト単位で顕在化させる（ファイル全体の load 失敗にしない）。
async function load() {
  return import('../js/usecase/color_themes.js');
}

function theme({ themeId, name, roleColors = {}, tfModifier = null, createdAt = 1000, updatedAt = 1000 }) {
  return { themeId, name, roleColors, tfModifier, createdAt, updatedAt };
}

// ---------------------------------------------------------------------------
// 名前（§4.4 name・§5.1 処理 1-2・§5.3 改名・F-C1）
// ---------------------------------------------------------------------------

test('TC-C01 normalizeThemeName は trim ＋小文字化した正規化名を返す（§5.1 処理 2）', async () => {
  // Arrange
  const { normalizeThemeName } = await load();
  // Act / Assert
  assert.equal(normalizeThemeName('  Dark Blue  '), 'dark blue');
  assert.equal(normalizeThemeName('ダーク'), 'ダーク');
  assert.equal(normalizeThemeName(null), '', 'null / undefined でも例外を投げない（全域性）');
  assert.equal(normalizeThemeName(undefined), '');
});

test('TC-C02 displayThemeName は trim のみ（表記は入力のまま保存する・§5.1 処理 2）', async () => {
  // Arrange
  const { displayThemeName } = await load();
  // Act / Assert
  assert.equal(displayThemeName('  Dark Blue  '), 'Dark Blue', '大文字小文字を潰さない');
  assert.equal(displayThemeName(null), '');
});

test('TC-C03 名前 0 文字（空・空白のみ）は不正（F-C1）', async () => {
  // Arrange
  const { validateThemeName, CODE } = await load();
  // Act
  const empty = validateThemeName('');
  const blank = validateThemeName('   ');
  // Assert
  assert.equal(empty.ok, false);
  assert.equal(empty.code, CODE.empty);
  assert.equal(blank.ok, false, 'trim 後 0 文字も空扱い');
  assert.equal(blank.code, CODE.empty);
});

test('TC-C04 名前 1 文字（下限）は正当（§4.11）', async () => {
  // Arrange
  const { validateThemeName, CODE } = await load();
  // Act
  const verdict = validateThemeName('A');
  // Assert
  assert.equal(verdict.ok, true);
  assert.equal(verdict.code, CODE.ok);
});

test('TC-C05 名前 40 文字（上限）は正当・41 文字（上限 +1）は不正（§4.11・F-C1）', async () => {
  // Arrange
  const { validateThemeName, CODE } = await load();
  const at = 'a'.repeat(40);
  const over = 'a'.repeat(41);
  // Act
  const atVerdict = validateThemeName(at);
  const overVerdict = validateThemeName(over);
  // Assert
  assert.equal(atVerdict.ok, true, '40 文字は上限内');
  assert.equal(overVerdict.ok, false, '41 文字は上限超過');
  assert.equal(overVerdict.code, CODE.tooLong);
});

test('TC-C06 名前は trim 後の長さで判定する（前後空白は文字数に数えない・§4.4）', async () => {
  // Arrange
  const { validateThemeName } = await load();
  // Act
  const verdict = validateThemeName(`  ${'a'.repeat(40)}  `);
  // Assert
  assert.equal(verdict.ok, true, 'trim 後 40 文字ちょうど');
});

test('TC-C07 正規化名が既存テーマと重複する名前は不正（F-C1）', async () => {
  // Arrange
  const { validateThemeName, CODE } = await load();
  const themes = [theme({ themeId: 'thm#1', name: 'Dark Blue' })];
  // Act
  const verdict = validateThemeName('  dark blue  ', { themes });
  // Assert
  assert.equal(verdict.ok, false, '大文字小文字・前後空白の差は同名とみなす');
  assert.equal(verdict.code, CODE.duplicate);
});

test('TC-C08 改名時に自身の現在名と同一正規化名への変更は許容する（§5.3）', async () => {
  // Arrange
  const { validateThemeName } = await load();
  const themes = [theme({ themeId: 'thm#1', name: 'Dark Blue' })];
  // Act
  const verdict = validateThemeName('DARK BLUE', { themes, excludeThemeId: 'thm#1' });
  // Assert
  assert.equal(verdict.ok, true, '自身は重複判定から除外する');
});

test('TC-C09 findThemeByName は正規化名一致で既存テーマを返し、空名は一致なし（§5.1 処理 2）', async () => {
  // Arrange
  const { findThemeByName } = await load();
  const target = theme({ themeId: 'thm#2', name: 'Light' });
  const themes = [theme({ themeId: 'thm#1', name: 'Dark' }), target];
  // Act / Assert
  assert.equal(findThemeByName({ themes, name: ' LIGHT ' }), target);
  assert.equal(findThemeByName({ themes, name: 'none' }), null, '不一致は null');
  assert.equal(findThemeByName({ themes, name: '   ' }), null, '空名は一致なし（名前検証へ委ねる）');
});

// ---------------------------------------------------------------------------
// roleColors の正規化（§4.4 値は #rrggbb 小文字／§5.1 処理 3／F-C3 未知トークン／F-C9 不正色）
// ---------------------------------------------------------------------------

// console.warn を捕捉する（F-C3「console に警告 1 回」の検証用）。
function captureWarn(fn) {
  const original = console.warn;
  const seen = [];
  console.warn = (...args) => { seen.push(args.join(' ')); };
  try { fn(); } finally { console.warn = original; }
  return seen;
}

test('TC-C10 normalizeRoleColor: #rrggbb は小文字化して受理する（§4.4 小文字 6 桁）', async () => {
  // Arrange
  const { normalizeRoleColor } = await load();
  // Act / Assert
  assert.equal(normalizeRoleColor('#AABBCC'), '#aabbcc', '大文字は小文字へ');
  assert.equal(normalizeRoleColor('#2962ff'), '#2962ff', '既に小文字ならそのまま');
  assert.equal(normalizeRoleColor('  #AaBbCc  '), '#aabbcc', '前後空白は trim');
});

test('TC-C11 normalizeRoleColor: 3 桁短縮形は 6 桁へ展開する（§5.1 処理 3 toHex 相当）', async () => {
  // Arrange
  const { normalizeRoleColor } = await load();
  // Act / Assert
  assert.equal(normalizeRoleColor('#abc'), '#aabbcc');
  assert.equal(normalizeRoleColor('#FFF'), '#ffffff');
  assert.equal(normalizeRoleColor('#000'), '#000000');
});

test('TC-C12 normalizeRoleColor: rgb() / rgba() は #rrggbb へ落とし、アルファは捨てる（§4.7 アルファ非対応）', async () => {
  // Arrange
  const { normalizeRoleColor } = await load();
  // Act / Assert
  assert.equal(normalizeRoleColor('rgb(41, 98, 255)'), '#2962ff');
  assert.equal(normalizeRoleColor('rgba(41, 98, 255, 0.6)'), '#2962ff', 'アルファは保存形に含めない');
  assert.equal(normalizeRoleColor('rgb(300, -5, 10)'), '#ff000a', '0..255 へクランプする');
});

test('TC-C13 normalizeRoleColor: 色として解釈できない値は null（F-C9・当該トークンを未宣言にする）', async () => {
  // Arrange
  const { normalizeRoleColor } = await load();
  // Act / Assert
  assert.equal(normalizeRoleColor('red'), null, '色名は受理しない（値域は toHex 受理集合）');
  assert.equal(normalizeRoleColor('#12345'), null, '5 桁は不正');
  assert.equal(normalizeRoleColor('#gggggg'), null, '16 進以外は不正');
  assert.equal(normalizeRoleColor(''), null);
  assert.equal(normalizeRoleColor(null), null);
  assert.equal(normalizeRoleColor(0x2962ff), null, '非文字列は不正（例外は投げない）');
});

test('TC-C14 normalizeRoleColors: 16 トークンすべてを受理し、値は #rrggbb 小文字へ正規化する（§4.4・§4.11）', async () => {
  // Arrange
  const { normalizeRoleColors } = await load();
  const { COLOR_ROLES } = await import('../js/domain/color_roles.js');
  const input = Object.fromEntries(COLOR_ROLES.map((t) => [t, '#ABCDEF']));
  // Act
  const out = normalizeRoleColors(input);
  // Assert
  assert.equal(Object.keys(out.roleColors).length, 16, '構造的上限 16 キーがすべて通る');
  assert.deepEqual(out.roleColors, Object.fromEntries(COLOR_ROLES.map((t) => [t, '#abcdef'])));
  assert.deepEqual(out.ignoredTokens, [], '語彙内だけなら無視したキーは無い');
});

test('TC-C15 normalizeRoleColors: 未知トークンのキーは無視し、無視した一覧を戻り値で返す（F-C3・R-4）', async () => {
  // Arrange
  const { normalizeRoleColors } = await load();
  let out;
  // Act
  const warns = captureWarn(() => { out = normalizeRoleColors({ bullish: '#ff0000', unknown_a: '#00ff00', unknown_b: '#0000ff' }); });
  // Assert
  assert.deepEqual(out.roleColors, { bullish: '#ff0000' }, '未知トークンはキーごと落とす');
  assert.deepEqual(out.ignoredTokens, ['unknown_a', 'unknown_b'], '無視したキーは戻り値で報告する（警告は adapter が出す）');
  assert.deepEqual(warns, [], 'usecase は console を持たない（副作用は adapter へ・R-4）');
});

test('TC-C16 normalizeRoleColors: 語彙内でも色として解釈できない値はキーごと落とす（F-C9）', async () => {
  // Arrange
  const { normalizeRoleColors } = await load();
  // Act
  const out = normalizeRoleColors({ bullish: '#ff0000', bearish: 'not-a-color', neutral: null, alert: '#0f0' });
  // Assert
  assert.deepEqual(out.roleColors, { bullish: '#ff0000', alert: '#00ff00' }, '未宣言として扱う（既定へ降格させる）');
  assert.deepEqual(out.ignoredTokens, [], '解釈不能な色（F-C9）は「未知トークン」ではない（F-C3 と別事由）');
});

test('TC-C17 normalizeRoleColors: 0 件・非オブジェクトは空オブジェクト（恒等テーマ・§4.4 0 件可）', async () => {
  // Arrange
  const { normalizeRoleColors } = await load();
  // Act / Assert
  assert.deepEqual(normalizeRoleColors({}), { roleColors: {}, ignoredTokens: [] }, '0 件＝恒等テーマ');
  assert.deepEqual(normalizeRoleColors(null), { roleColors: {}, ignoredTokens: [] });
  assert.deepEqual(normalizeRoleColors(undefined), { roleColors: {}, ignoredTokens: [] });
  assert.deepEqual(normalizeRoleColors('nope'), { roleColors: {}, ignoredTokens: [] });
  assert.deepEqual(normalizeRoleColors(['#ff0000']), { roleColors: {}, ignoredTokens: [] }, '配列はトークン表ではない');
});

test('TC-C18 normalizeRoleColors: 入力を破壊しない（純関数）', async () => {
  // Arrange
  const { normalizeRoleColors } = await load();
  const input = { bullish: '#FF0000', unknown_a: '#00ff00' };
  // Act
  captureWarn(() => normalizeRoleColors(input));
  // Assert
  assert.deepEqual(input, { bullish: '#FF0000', unknown_a: '#00ff00' });
});

// ---------------------------------------------------------------------------
// tfModifier の正規化（§4.4 l ∈ [-1,1] 小数第 3 位・§4.7 クランプと丸め）
// ---------------------------------------------------------------------------

test('TC-C19 normalizeTfModifier: 台帳に無いキーは落とす（§4.4 キーは TF_CODES のみ）', async () => {
  // Arrange
  const { normalizeTfModifier } = await load();
  // Act
  const out = normalizeTfModifier({ '1D': 0.2, '3s': 0.5, chart: -0.3 });
  // Assert
  assert.deepEqual(out, { '1D': 0.2 });
});

// 許容キー集合は台帳ただ 1 つで、呼び出し側が差し替える口を持たない。引数で受けていた頃は
//   「渡し忘れ＝キー検証が丸ごと不活性」という無言の抜け道があった（渡さない呼び出しが実在した）。
test('TC-C20 normalizeTfModifier: 許容キー集合は台帳（TF_CODES）で、引数では無効化できない', async () => {
  // Arrange
  const { normalizeTfModifier } = await load();
  const { TF_CODES } = await import('../js/domain/tf_meta.js');
  const declared = Object.fromEntries(TF_CODES.map((tf) => [tf, 0.1]));
  // Act: 第 2 引数に「全キー許可」を意図した値を渡しても、判定は台帳のまま。
  const out = normalizeTfModifier({ ...declared, future_tf: 0.5 }, { timeframes: ['future_tf'] });
  // Assert
  assert.deepEqual(out, declared, '台帳の全コードが残り、台帳外は必ず落ちる');
});

test('TC-C21 normalizeTfModifier: 端点 -1 / 0 / 1 はそのまま保持する（§4.4 l ∈ [-1,1]）', async () => {
  // Arrange
  const { normalizeTfModifier } = await load();
  // Act
  const out = normalizeTfModifier({ '1m': -1, '5m': 0, '1D': 1 });
  // Assert
  assert.deepEqual(out, { '1m': -1, '5m': 0, '1D': 1 });
});

test('TC-C22 normalizeTfModifier: 範囲外は [-1,1] へクランプする（§4.7 手順 2 と同一結果）', async () => {
  // Arrange
  const { normalizeTfModifier } = await load();
  // Act
  const out = normalizeTfModifier({ '1m': 1.5, '5m': -2, '1D': 1000 });
  // Assert
  assert.deepEqual(out, { '1m': 1, '5m': -1, '1D': 1 }, '適用時のクランプと同値＝描画結果は不変');
});

test('TC-C23 normalizeTfModifier: 小数第 4 位以下は §4.7 手順 3 の丸め（0.5 は正方向）で第 3 位へ落とす', async () => {
  // Arrange
  const { normalizeTfModifier } = await load();
  // Act
  const out = normalizeTfModifier({ '1m': 0.12345, '5m': -0.12345, '1D': 0.0005 });
  // Assert
  assert.deepEqual(out, { '1m': 0.123, '5m': -0.123, '1D': 0.001 });
});

test('TC-C24 normalizeTfModifier: 数値でない値・非有限値はキーごと落とす（§4.4 値は number）', async () => {
  // Arrange
  const { normalizeTfModifier } = await load();
  // Act
  const out = normalizeTfModifier(
    { '1m': '0.2', '5m': NaN, '15m': Infinity, '30m': null, '1h': 0.2 },
  );
  // Assert
  assert.deepEqual(out, { '1h': 0.2 });
});

test('TC-C25 normalizeTfModifier: null / 非オブジェクトは null（§4.4 null 可）・空オブジェクトは空のまま', async () => {
  // Arrange
  const { normalizeTfModifier } = await load();
  // Act / Assert
  assert.equal(normalizeTfModifier(null), null);
  assert.equal(normalizeTfModifier(undefined), null);
  assert.equal(normalizeTfModifier('nope'), null);
  assert.equal(normalizeTfModifier([0.2]), null, '配列は時間足表ではない');
  assert.deepEqual(normalizeTfModifier({}), {}, '全足 l = 0 の宣言は空表と同値');
});

test('TC-C26 normalizeTfModifier: 入力を破壊しない（純関数）', async () => {
  // Arrange
  const { normalizeTfModifier } = await load();
  const input = { '1D': 1.5, '3s': 0.5 };
  // Act
  normalizeTfModifier(input);
  // Assert
  assert.deepEqual(input, { '1D': 1.5, '3s': 0.5 });
});

// ---------------------------------------------------------------------------
// themeId 採番（§4.10 `thm#{seq}`・seq = lastSeq + 1・id の再利用禁止・破損時復旧）
// ---------------------------------------------------------------------------

test('TC-C27 nextThemeId: `thm#{lastSeq + 1}` を発行し、更新後の lastSeq を併せて返す（§4.10）', async () => {
  // Arrange
  const { nextThemeId } = await load();
  // Act / Assert
  assert.deepEqual(nextThemeId(0), { themeId: 'thm#1', lastSeq: 1 }, '初回発行は thm#1');
  assert.deepEqual(nextThemeId(7), { themeId: 'thm#8', lastSeq: 8 });
});

test('TC-C28 nextThemeId: lastSeq が整数でない場合は 0 を起点にする（全域性）', async () => {
  // Arrange
  const { nextThemeId } = await load();
  // Act / Assert
  assert.deepEqual(nextThemeId(undefined), { themeId: 'thm#1', lastSeq: 1 });
  assert.deepEqual(nextThemeId('3'), { themeId: 'thm#1', lastSeq: 1 });
  assert.deepEqual(nextThemeId(2.5), { themeId: 'thm#1', lastSeq: 1 });
});

test('TC-C29 recoverLastSeq: 初期化後の lastSeq を既存 thm#N の最大 N 以上へ引き上げる（§4.10 破損時）', async () => {
  // Arrange
  const { recoverLastSeq } = await load();
  const themes = [theme({ themeId: 'thm#3', name: 'A' }), theme({ themeId: 'thm#11', name: 'B' })];
  // Act
  const recovered = recoverLastSeq(0, themes);
  // Assert
  assert.equal(recovered, 11, 'id の再利用・衝突を避ける');
});

test('TC-C30 recoverLastSeq: 既存 lastSeq のほうが大きければ減算しない（§4.10 単調・削除で減らさない）', async () => {
  // Arrange
  const { recoverLastSeq } = await load();
  const themes = [theme({ themeId: 'thm#2', name: 'A' })];
  // Act / Assert
  assert.equal(recoverLastSeq(9, themes), 9);
  assert.equal(recoverLastSeq(9, []), 9, '全テーマ削除後も lastSeq は保持する');
});

test('TC-C31 recoverLastSeq: 形式外の id と非整数 lastSeq は 0 起点で無視する（全域性）', async () => {
  // Arrange
  const { recoverLastSeq } = await load();
  const themes = [
    theme({ themeId: 'tpl#99', name: 'A' }),
    theme({ themeId: 'thm#x', name: 'B' }),
    { name: 'C' },
    null,
    theme({ themeId: 'thm#4', name: 'D' }),
  ];
  // Act / Assert
  assert.equal(recoverLastSeq(undefined, themes), 4, 'thm#N 形式のみを見る');
  assert.equal(recoverLastSeq(0, undefined), 0);
});

// ---------------------------------------------------------------------------
// UC-C01 テーマ作成・保存（§5.1 の処理順・§4.11 上限・F-C1）
// ---------------------------------------------------------------------------

test('TC-C32 saveTheme: 新規は採番して追加し、createdAt / updatedAt に引数の時刻を入れる（§5.1・§4.4）', async () => {
  // Arrange
  const { saveTheme, CODE } = await load();
  // Act
  const r = saveTheme({
    themes: [], lastSeq: 4, name: '  Dark Blue  ',
    roleColors: { bullish: '#00FF00' }, tfModifier: { '1D': 0.2 }, now: 1700000000,
  });
  // Assert
  assert.equal(r.ok, true);
  assert.equal(r.code, CODE.ok);
  assert.equal(r.themeId, 'thm#5');
  assert.equal(r.lastSeq, 5, '発行した seq を返す（呼び出し側が activeTheme.v1 と単一原子で永続化する）');
  assert.deepEqual(r.themes, [{
    themeId: 'thm#5',
    name: 'Dark Blue',
    roleColors: { bullish: '#00ff00' },
    tfModifier: { '1D': 0.2 },
    createdAt: 1700000000,
    updatedAt: 1700000000,
  }]);
});

test('TC-C33 saveTheme: 正規化名が一致する既存テーマは上書き更新する（themeId・createdAt 不変／§5.1 処理 2）', async () => {
  // Arrange
  const { saveTheme } = await load();
  const themes = [
    theme({ themeId: 'thm#1', name: 'Dark Blue', roleColors: { bullish: '#111111', muted: '#222222' }, tfModifier: { '1D': 0.5 }, createdAt: 100, updatedAt: 100 }),
    theme({ themeId: 'thm#2', name: 'Light' }),
  ];
  // Act
  const r = saveTheme({
    themes, lastSeq: 2, name: 'DARK BLUE',
    roleColors: { bearish: '#333333' }, tfModifier: null, now: 900,
  });
  // Assert
  assert.equal(r.ok, true);
  assert.equal(r.themeId, 'thm#1', 'themeId を保持する');
  assert.equal(r.lastSeq, 2, '上書きは採番しない');
  assert.equal(r.themes.length, 2, '件数は増えない');
  assert.deepEqual(r.themes[0], {
    themeId: 'thm#1',
    name: 'DARK BLUE',
    roleColors: { bearish: '#333333' },
    tfModifier: null,
    createdAt: 100,
    updatedAt: 900,
  }, 'name は入力の表記・roleColors / tfModifier は置換（マージしない）・createdAt 不変・updatedAt 更新');
});

test('TC-C34 saveTheme: 50 件（上限）までは新規保存でき、51 件目は中止して既存を変えない（§4.11・F-C1）', async () => {
  // Arrange
  const { saveTheme, CODE } = await load();
  const at49 = Array.from({ length: 49 }, (_, i) => theme({ themeId: `thm#${i + 1}`, name: `t${i + 1}` }));
  // Act: 50 件目（上限ちょうど）
  const fill = saveTheme({ themes: at49, lastSeq: 49, name: 't50', now: 1 });
  // Assert
  assert.equal(fill.ok, true, '50 件目は保存できる');
  assert.equal(fill.themes.length, 50);
  // Act: 51 件目（上限 +1）
  const over = saveTheme({ themes: fill.themes, lastSeq: fill.lastSeq, name: 't51', now: 2 });
  // Assert
  assert.equal(over.ok, false);
  assert.equal(over.code, CODE.limit);
  assert.equal(over.themeId, null);
  assert.equal(over.themes, fill.themes, '既存データは不変（同一参照を返す）');
  assert.equal(over.lastSeq, 50, 'lastSeq を消費しない');
});

test('TC-C35 saveTheme: 件数上限に達していても同名の上書き更新は可能（§5.1 例外）', async () => {
  // Arrange
  const { saveTheme } = await load();
  const at50 = Array.from({ length: 50 }, (_, i) => theme({ themeId: `thm#${i + 1}`, name: `t${i + 1}` }));
  // Act
  const r = saveTheme({ themes: at50, lastSeq: 50, name: 't7', roleColors: { alert: '#ff0000' }, now: 5 });
  // Assert
  assert.equal(r.ok, true);
  assert.equal(r.themeId, 'thm#7');
  assert.equal(r.themes.length, 50);
});

test('TC-C36 saveTheme: 名前が不正なら保存を中止し既存データ・lastSeq を変えない（F-C1）', async () => {
  // Arrange
  const { saveTheme, CODE } = await load();
  const themes = [theme({ themeId: 'thm#1', name: 'A' })];
  // Act
  const empty = saveTheme({ themes, lastSeq: 1, name: '   ', now: 9 });
  const tooLong = saveTheme({ themes, lastSeq: 1, name: 'a'.repeat(41), now: 9 });
  // Assert
  assert.equal(empty.ok, false);
  assert.equal(empty.code, CODE.empty);
  assert.equal(empty.themes, themes, '既存データは不変');
  assert.equal(empty.lastSeq, 1);
  assert.equal(tooLong.ok, false);
  assert.equal(tooLong.code, CODE.tooLong);
  assert.equal(tooLong.themes, themes);
});

test('TC-C37 saveTheme: 未知トークン・解釈不能な色・許容外の時間足キーは保存前に落とす（§5.1 処理 3・F-C3・F-C9）', async () => {
  // Arrange
  const { saveTheme } = await load();
  let r;
  // Act
  const warns = captureWarn(() => {
    r = saveTheme({
      themes: [], lastSeq: 0, name: 'T',
      roleColors: { bullish: '#ABC', unknown_x: '#ffffff', bearish: 'transparent' },
      tfModifier: { '1D': 0.2, '3s': 0.4 },
      now: 7,
    });
  });
  // Assert
  assert.deepEqual(r.themes[0].roleColors, { bullish: '#aabbcc' });
  assert.deepEqual(r.themes[0].tfModifier, { '1D': 0.2 });
  assert.deepEqual(r.ignoredTokens, ['unknown_x'], '無視した未知トークンは保存結果に載る（警告は adapter が出す・R-4）');
  assert.deepEqual(warns, [], 'usecase は console を持たない（R-4）');
});

test('TC-C37b saveTheme: ignoredTokens は正規化に到達した呼び出しだけが持つ（名前不正は空・上限拒否でも載る・F-C1/F-C3）', async () => {
  // Arrange
  const { saveTheme, CODE } = await load();
  const roleColors = { bullish: '#ff0000', unknown_x: '#ffffff' };
  const at50 = Array.from({ length: 50 }, (_, i) => theme({ themeId: `thm#${i + 1}`, name: `t${i + 1}` }));
  // Act
  const invalidName = saveTheme({ themes: [], lastSeq: 0, name: '   ', roleColors, now: 1 });
  const overLimit = saveTheme({ themes: at50, lastSeq: 50, name: 'new', roleColors, now: 1 });
  // Assert
  assert.equal(invalidName.code, CODE.empty);
  assert.deepEqual(invalidName.ignoredTokens, [], '名前検証で中止した呼び出しは色を見ていない（§5.1 処理順 1→3）');
  assert.equal(overLimit.code, CODE.limit);
  assert.deepEqual(overLimit.ignoredTokens, ['unknown_x'], '上限拒否でも正規化は済んでおり、無視した事実は報告される');
});

test('TC-C38 saveTheme: 入力の themes 配列とその要素を破壊しない（純関数）', async () => {
  // Arrange
  const { saveTheme } = await load();
  const existing = theme({ themeId: 'thm#1', name: 'A', roleColors: { bullish: '#111111' }, createdAt: 1, updatedAt: 1 });
  const themes = [existing];
  const snapshot = JSON.parse(JSON.stringify(themes));
  // Act
  saveTheme({ themes, lastSeq: 1, name: 'A', roleColors: { bearish: '#222222' }, now: 50 });
  saveTheme({ themes, lastSeq: 1, name: 'B', now: 50 });
  // Assert
  assert.deepEqual(themes, snapshot, '呼び出し側が確定するまで元の状態は変わらない');
});

test('TC-C39 saveTheme: 同一入力は同一結果（Date.now / Math.random に依存しない・決定論）', async () => {
  // Arrange
  const { saveTheme } = await load();
  const args = { themes: [], lastSeq: 0, name: 'T', roleColors: { neutral: '#123456' }, now: 42 };
  // Act
  const a = saveTheme({ ...args });
  const b = saveTheme({ ...args });
  // Assert
  assert.deepEqual(a, b);
});

// ---------------------------------------------------------------------------
// UC-C03 改名・削除（§5.3）と dangling activeThemeId（F-C6）
// ---------------------------------------------------------------------------

test('TC-C40 renameTheme: 名前を更新し updatedAt を進める（themeId・roleColors・createdAt は不変・§5.3）', async () => {
  // Arrange
  const { renameTheme } = await load();
  const themes = [theme({ themeId: 'thm#1', name: 'A', roleColors: { bullish: '#111111' }, createdAt: 10, updatedAt: 10 })];
  // Act
  const r = renameTheme({ themes, themeId: 'thm#1', name: '  Renamed  ', now: 99 });
  // Assert
  assert.equal(r.ok, true);
  assert.deepEqual(r.themes[0], {
    themeId: 'thm#1', name: 'Renamed', roleColors: { bullish: '#111111' }, tfModifier: null, createdAt: 10, updatedAt: 99,
  });
});

test('TC-C41 renameTheme: 名前が不正（空・41 文字・他テーマと正規化名重複）なら中止し不変（F-C1）', async () => {
  // Arrange
  const { renameTheme, CODE } = await load();
  const themes = [theme({ themeId: 'thm#1', name: 'A' }), theme({ themeId: 'thm#2', name: 'B' })];
  // Act / Assert
  const empty = renameTheme({ themes, themeId: 'thm#1', name: '  ', now: 5 });
  assert.equal(empty.ok, false);
  assert.equal(empty.code, CODE.empty);
  assert.equal(empty.themes, themes, '既存データは不変');

  const tooLong = renameTheme({ themes, themeId: 'thm#1', name: 'a'.repeat(41), now: 5 });
  assert.equal(tooLong.code, CODE.tooLong);

  const dup = renameTheme({ themes, themeId: 'thm#1', name: 'b', now: 5 });
  assert.equal(dup.ok, false);
  assert.equal(dup.code, CODE.duplicate);
  assert.equal(dup.themes, themes);
});

test('TC-C42 renameTheme: 自身の現在名と同一正規化名（表記だけ変更）は許容する（§5.3）', async () => {
  // Arrange
  const { renameTheme } = await load();
  const themes = [theme({ themeId: 'thm#1', name: 'dark blue', updatedAt: 1 })];
  // Act
  const r = renameTheme({ themes, themeId: 'thm#1', name: 'Dark Blue', now: 77 });
  // Assert
  assert.equal(r.ok, true);
  assert.equal(r.themes[0].name, 'Dark Blue');
  assert.equal(r.themes[0].updatedAt, 77);
});

test('TC-C43 renameTheme: 不在 themeId は not_found で中止する', async () => {
  // Arrange
  const { renameTheme, CODE } = await load();
  const themes = [theme({ themeId: 'thm#1', name: 'A' })];
  // Act
  const r = renameTheme({ themes, themeId: 'thm#9', name: 'X', now: 5 });
  // Assert
  assert.equal(r.ok, false);
  assert.equal(r.code, CODE.notFound);
  assert.equal(r.themes, themes);
});

test('TC-C44 deleteTheme: 当該テーマを除去し、activeThemeId が当該 id なら null にする（§5.3）', async () => {
  // Arrange
  const { deleteTheme } = await load();
  const themes = [theme({ themeId: 'thm#1', name: 'A' }), theme({ themeId: 'thm#2', name: 'B' })];
  // Act
  const r = deleteTheme({ themes, themeId: 'thm#2', activeThemeId: 'thm#2' });
  // Assert
  assert.deepEqual(r.themes.map((t) => t.themeId), ['thm#1']);
  assert.equal(r.activeThemeId, null, '選択中テーマの削除は「テーマ未選択」へ落とす');
});

test('TC-C45 deleteTheme: 別テーマが選択中なら選択を保持し、不在 id の削除は無害（全域性）', async () => {
  // Arrange
  const { deleteTheme } = await load();
  const themes = [theme({ themeId: 'thm#1', name: 'A' }), theme({ themeId: 'thm#2', name: 'B' })];
  // Act
  const kept = deleteTheme({ themes, themeId: 'thm#2', activeThemeId: 'thm#1' });
  const absent = deleteTheme({ themes, themeId: 'thm#9', activeThemeId: 'thm#1' });
  // Assert
  assert.equal(kept.activeThemeId, 'thm#1');
  assert.deepEqual(absent.themes.map((t) => t.themeId), ['thm#1', 'thm#2']);
  assert.equal(absent.activeThemeId, 'thm#1');
});

test('TC-C46 deleteTheme: 入力の themes 配列を破壊しない（純関数）', async () => {
  // Arrange
  const { deleteTheme } = await load();
  const themes = [theme({ themeId: 'thm#1', name: 'A' }), theme({ themeId: 'thm#2', name: 'B' })];
  // Act
  deleteTheme({ themes, themeId: 'thm#1', activeThemeId: 'thm#1' });
  // Assert
  assert.equal(themes.length, 2);
});

// ---------------------------------------------------------------------------
// 同梱プリセットへの書き込み（§9 T-1・§5.1・§5.3）
//
// 書き込み 3 経路（保存・改名・削除）の**解決集合は読み出しと同じ合成後の集合**である。
//   永続層だけで解決すると、一覧に見えているプリセットが「不在」と扱われ、改名は not_found、
//   同名保存は新規採番（一覧に同名 2 件）になる（実測 2026-08-09）。
// プリセットが対象になったら**同じ themeId のまま**永続層へ実体化する（新規採番しない）。
//   以降は withPresets の「同じ themeId の永続値が勝つ」規則でユーザー版が一覧に出る。
// ---------------------------------------------------------------------------

test('TC-C48 saveTheme: 同名保存はプリセットを同じ themeId のまま実体化する（新規採番しない）', async () => {
  // Arrange
  const { saveTheme, PRESET_THEMES } = await load();
  const preset = PRESET_THEMES[0];
  // Act
  const r = saveTheme({
    themes: [], lastSeq: 0, name: preset.name, roleColors: { bullish: '#010203' }, now: 500,
  });
  // Assert
  assert.equal(r.ok, true);
  assert.equal(r.themeId, preset.themeId, '新規採番されている（席が 2 つに割れる）');
  assert.equal(r.lastSeq, 0, '実体化は採番を消費しない（§4.10）');
  assert.equal(r.themes.length, 1, '永続層へ実体化するのは 1 件だけ');
  assert.deepEqual(r.themes[0], {
    themeId: preset.themeId,
    name: preset.name,
    roleColors: { bullish: '#010203' },
    tfModifier: null,
    createdAt: preset.createdAt,
    updatedAt: 500,
  }, 'createdAt はプリセットの値を引き継ぎ、updatedAt を更新する');
});

test('TC-C49 saveTheme: themeId 指定でもプリセットの席を更新する（名前を変えても増えない）', async () => {
  // Arrange
  const { saveTheme, PRESET_THEMES } = await load();
  const preset = PRESET_THEMES[0];
  // Act
  const r = saveTheme({
    themes: [], lastSeq: 0, themeId: preset.themeId, name: '別名', roleColors: {}, now: 500,
  });
  // Assert
  assert.equal(r.ok, true);
  assert.equal(r.themeId, preset.themeId);
  assert.deepEqual(r.themes.map((t) => [t.themeId, t.name]), [[preset.themeId, '別名']]);
});

test('TC-C50 saveTheme: 実体化済みプリセットは差し替えになる（2 件目を作らない）', async () => {
  // Arrange: 既に実体化済み（ユーザー版が永続層に在る）。
  const { saveTheme, PRESET_THEMES } = await load();
  const preset = PRESET_THEMES[0];
  const themes = [theme({ themeId: preset.themeId, name: '基本（改）', createdAt: 0, updatedAt: 1 })];
  // Act
  const r = saveTheme({ themes, lastSeq: 0, name: '基本（改）', roleColors: { alert: '#ffffff' }, now: 9 });
  // Assert
  assert.equal(r.themes.length, 1, '実体化済みの席に 2 件目が生えている');
  assert.equal(r.themes[0].themeId, preset.themeId);
  assert.equal(r.themes[0].updatedAt, 9);
});

test('TC-C51 saveTheme: プリセットの実体化は「新規追加」として上限 50 件の判定に含める（§4.11 を変えない）', async () => {
  // Arrange: 永続層が上限ちょうど。
  const { saveTheme, CODE, PRESET_THEMES } = await load();
  const preset = PRESET_THEMES[0];
  const at50 = Array.from({ length: 50 }, (_, i) => theme({ themeId: `thm#${i + 1}`, name: `t${i + 1}` }));
  // Act
  const r = saveTheme({ themes: at50, lastSeq: 50, name: preset.name, roleColors: {}, now: 1 });
  // Assert
  assert.equal(r.ok, false, '行が 1 本増えるのに上限判定を素通りしている');
  assert.equal(r.code, CODE.limit);
  assert.equal(r.themes, at50, '既存データは不変（同一参照を返す）');
});

test('TC-C52 saveTheme: 削除済みプリセットは解決対象にならない（同名は新規採番する）', async () => {
  // Arrange
  const { saveTheme, PRESET_THEMES } = await load();
  const preset = PRESET_THEMES[0];
  // Act
  const r = saveTheme({
    themes: [], lastSeq: 0, name: preset.name, roleColors: {}, now: 1,
    removedPresetIds: [preset.themeId],
  });
  // Assert
  assert.equal(r.themeId, 'thm#1', '削除した席を再利用している（削除の記録が効いていない）');
  assert.equal(r.lastSeq, 1);
});

test('TC-C53 renameTheme: プリセットを同じ themeId のまま実体化して名前だけ更新する（§5.3）', async () => {
  // Arrange
  const { renameTheme, PRESET_THEMES } = await load();
  const preset = PRESET_THEMES[0];
  // Act
  const r = renameTheme({ themes: [], themeId: preset.themeId, name: '  基本（改）  ', now: 42 });
  // Assert
  assert.equal(r.ok, true, `プリセットを改名できない（code=${r.code}）`);
  assert.equal(r.themes.length, 1);
  assert.deepEqual(r.themes[0], {
    ...preset,
    name: '基本（改）',
    updatedAt: 42,
  }, '改名で roleColors・tfModifier・createdAt・themeId は不変（§5.3）');
});

test('TC-C54 renameTheme: 削除済みプリセットは not_found（削除した席が改名で復活しない）', async () => {
  // Arrange
  const { renameTheme, CODE, PRESET_THEMES } = await load();
  const preset = PRESET_THEMES[0];
  // Act
  const r = renameTheme({
    themes: [], themeId: preset.themeId, name: 'X', now: 1, removedPresetIds: [preset.themeId],
  });
  // Assert
  assert.equal(r.ok, false);
  assert.equal(r.code, CODE.notFound);
  assert.deepEqual(r.themes, []);
});

test('TC-C55 renameTheme: 一覧に見えるプリセットと同名への改名は重複として拒否する（F-C1）', async () => {
  // Arrange
  const { renameTheme, CODE, PRESET_THEMES } = await load();
  const preset = PRESET_THEMES[0];
  const themes = [theme({ themeId: 'thm#1', name: 'A' })];
  // Act
  const r = renameTheme({ themes, themeId: 'thm#1', name: preset.name, now: 1 });
  // Assert
  assert.equal(r.ok, false, '一覧に同名が 2 件並ぶ改名が通っている');
  assert.equal(r.code, CODE.duplicate);
  assert.equal(r.themes, themes, '既存データは不変（同一参照を返す）');
});

test('TC-C56 renameTheme: プリセットの実体化も上限 50 件の判定に含める（§4.11）', async () => {
  // Arrange
  const { renameTheme, CODE, PRESET_THEMES } = await load();
  const preset = PRESET_THEMES[0];
  const at50 = Array.from({ length: 50 }, (_, i) => theme({ themeId: `thm#${i + 1}`, name: `t${i + 1}` }));
  // Act
  const r = renameTheme({ themes: at50, themeId: preset.themeId, name: '基本（改）', now: 1 });
  // Assert
  assert.equal(r.ok, false);
  assert.equal(r.code, CODE.limit);
  assert.equal(r.themes, at50);
});

test('TC-C57 deleteTheme: プリセット id は削除の記録へ追記する（行を消すだけでは復活する）', async () => {
  // Arrange
  const { deleteTheme, PRESET_THEMES } = await load();
  const preset = PRESET_THEMES[0];
  // Act
  const r = deleteTheme({ themes: [], themeId: preset.themeId, activeThemeId: preset.themeId });
  // Assert
  assert.deepEqual(r.removedPresetIds, [preset.themeId]);
  assert.deepEqual(r.themes, []);
  assert.equal(r.activeThemeId, null, '選択中テーマの削除は「テーマ未選択」へ落とす（§5.3）');
});

test('TC-C58 deleteTheme: 実体化済みプリセットは行の除去と削除の記録を同時に行う', async () => {
  // Arrange
  const { deleteTheme, PRESET_THEMES } = await load();
  const preset = PRESET_THEMES[0];
  const themes = [theme({ themeId: preset.themeId, name: '基本（改）' }), theme({ themeId: 'thm#1', name: 'A' })];
  // Act
  const r = deleteTheme({ themes, themeId: preset.themeId, activeThemeId: null });
  // Assert
  assert.deepEqual(r.themes.map((t) => t.themeId), ['thm#1'], '行が残ると削除にならない');
  assert.deepEqual(r.removedPresetIds, [preset.themeId], '記録が無いと定義から復活する');
});

test('TC-C59 deleteTheme: 記録が変わらないときは入力配列を同一参照で返す（呼び出し側の書き込み判定源）', async () => {
  // Arrange
  const { deleteTheme, PRESET_THEMES } = await load();
  const preset = PRESET_THEMES[0];
  const removedPresetIds = [preset.themeId];
  // Act: ユーザーテーマの削除／既に記録済みプリセットの再削除
  const user = deleteTheme({ themes: [theme({ themeId: 'thm#1', name: 'A' })], themeId: 'thm#1', removedPresetIds });
  const again = deleteTheme({ themes: [], themeId: preset.themeId, removedPresetIds });
  // Assert
  assert.equal(user.removedPresetIds, removedPresetIds, 'ユーザーテーマの削除で記録を書き換えている');
  assert.equal(again.removedPresetIds, removedPresetIds, '二重追記している（記録が単調に膨らむ）');
  assert.deepEqual(removedPresetIds, [preset.themeId], '入力を破壊しない（純関数）');
});

test('TC-C47 resolveActiveThemeId: 参照先が不在（dangling）なら null へ解決し変更ありを返す（F-C6）', async () => {
  // Arrange
  const { resolveActiveThemeId } = await load();
  const themes = [theme({ themeId: 'thm#1', name: 'A' })];
  // Act
  const r = resolveActiveThemeId({ themes, activeThemeId: 'thm#7' });
  // Assert
  assert.equal(r.activeThemeId, null, 'テーマ未選択として解決する');
  assert.equal(r.changed, true, '呼び出し側が遅延クリーンアップとして永続化する');
});

test('TC-C48 resolveActiveThemeId: 在席 id と未選択（null）はそのまま返す（変更なし）', async () => {
  // Arrange
  const { resolveActiveThemeId } = await load();
  const themes = [theme({ themeId: 'thm#1', name: 'A' })];
  // Act / Assert
  assert.deepEqual(resolveActiveThemeId({ themes, activeThemeId: 'thm#1' }), { activeThemeId: 'thm#1', changed: false });
  assert.deepEqual(resolveActiveThemeId({ themes, activeThemeId: null }), { activeThemeId: null, changed: false });
  assert.deepEqual(resolveActiveThemeId({ themes: [], activeThemeId: null }), { activeThemeId: null, changed: false });
});
